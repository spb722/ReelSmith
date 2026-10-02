"""Contracted Veo generation, result inspection, and QA preview tools."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from claude_agent_sdk import create_sdk_mcp_server, tool
from google import genai
from google.cloud import storage
from google.genai import types

from orchestrator.contracts.veo import (
    VeoFailureContract,
    VeoOutcomeContract,
    VeoResultContract,
    VeoSeedContract,
    sha256_file,
    shot_fingerprint,
)
from orchestrator.contracts.visual_plan import Shot
from orchestrator import tracing
from orchestrator.progress import format_duration, log
from orchestrator.settings import (
    VEO_ALLOWED_DURATION_SECONDS,
    VEO_EXTENSION_RESOLUTIONS,
    VEO_EXTENSION_SECONDS,
    veo_extend_long_shots_setting,
)
from orchestrator.tools.camera_motion import describe_camera_motion, measure_camera_motion
from orchestrator.tools.cover_tools import _video_duration_seconds as _ffprobe_duration
from orchestrator.state.run_manifest import _atomic_write_json
from orchestrator.tools.deterministic_tools import resolve_binary
from orchestrator.tools.gemini_tools import authoritative_shot
from orchestrator.tools.vendor_limits import VEO, vendor_slot


VEO_OUTPUT_DIR = Path("generated/veo")
VEO_PREVIEW_DIR = VEO_OUTPUT_DIR / "previews"
VEO_OPERATION_DIR = Path("metadata/veo_operations")
# Tolerance when matching a shot's fractional duration against the integer
# clip lengths Veo offers, so a 6.001s shot still takes a 6s clip.
CLIP_DURATION_EPSILON = 0.05
# An extended shot: one clip of the longest allowed length, then one extension.
EXTENDED_FIRST_CLIP_SECONDS = max(VEO_ALLOWED_DURATION_SECONDS)  # 8
EXTENSION_PARTS_DIR = VEO_OUTPUT_DIR / "extension_parts"   # not globbed by the UI's *.mp4 listing
STATIC_CAMERA_ZOOM_BAND = (0.92, 1.08)   # mirrors veo-production SKILL.md / visual_reviewer.py
STATIC_CAMERA_MAX_PAN_PERCENT = 5


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    current = obj
    for key in keys:
        if current is None:
            return default
        current = current.get(key) if isinstance(current, dict) else getattr(current, key, None)
    return default if current is None else current


def _json_safe(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, str):
        # Mirrors the raw-bytes redaction below: an inline base64 video
        # payload can arrive as a `video_bytes` string instead of `bytes`,
        # and must not be inlined in full into a dump meant to be preserved
        # indefinitely under metadata/veo_operations/.
        if key == "video_bytes":
            return {"inline_bytes_length": len(value)}
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"inline_bytes_length": len(value)}
    if isinstance(value, dict):
        return {str(item_key): _json_safe(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, key=key) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json", exclude_none=False), key=key)
        except Exception:
            pass
    if hasattr(value, "to_json_dict"):
        try:
            return _json_safe(value.to_json_dict(), key=key)
        except Exception:
            pass
    return repr(value)


def operation_to_jsonable(operation: Any) -> dict:
    data = _json_safe(operation)
    return data if isinstance(data, dict) else {"value": data}


def _operation_roots(operation: Any) -> list[Any]:
    return [root for root in (_get(operation, "response"), _get(operation, "result")) if root is not None]


def _generated_videos(operation: Any) -> list[Any]:
    videos: list[Any] = []
    seen: set[tuple[str | None, int]] = set()
    for root in _operation_roots(operation):
        for generated in _get(root, "generated_videos", default=[]) or []:
            video = _get(generated, "video")
            uri = _get(video, "uri")
            raw = _get(video, "video_bytes")
            if isinstance(raw, (bytes, bytearray)):
                raw_marker = hashlib.sha256(bytes(raw)).hexdigest()
            elif isinstance(raw, str):
                raw_marker = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            else:
                raw_marker = ""
            marker = (uri, raw_marker)
            if marker not in seen:
                videos.append(generated)
                seen.add(marker)
    return videos


def _rai_details(operation: Any) -> tuple[int, list[str]]:
    count = 0
    reasons: list[str] = []
    for root in _operation_roots(operation):
        raw_count = _get(root, "rai_media_filtered_count", default=0) or 0
        try:
            count = max(count, int(raw_count))
        except (TypeError, ValueError):
            pass
        raw_reasons = _get(root, "rai_media_filtered_reasons", default=[]) or []
        if isinstance(raw_reasons, str):
            raw_reasons = [raw_reasons]
        for reason in raw_reasons:
            text = str(reason)
            if text not in reasons:
                reasons.append(text)
    return count, reasons


def _operation_error(operation: Any) -> Any:
    errors = [_get(operation, "error")]
    errors.extend(_get(root, "error") for root in _operation_roots(operation))
    present = [_json_safe(error) for error in errors if error is not None]
    return present if present else None


def _download_gcs(uri: str, destination: Path, project_id: str) -> None:
    if not uri.startswith("gs://"):
        raise ValueError(f"Veo returned unsupported non-GCS URI: {uri}")
    bucket_and_blob = uri[5:]
    bucket_name, separator, blob_name = bucket_and_blob.partition("/")
    if not separator or not bucket_name or not blob_name:
        raise ValueError(f"Malformed GCS URI: {uri}")
    client = storage.Client(project=project_id)
    client.bucket(bucket_name).blob(blob_name).download_to_filename(str(destination))


def _write_video_bytes(raw: bytes | bytearray | str, destination: Path) -> None:
    payload = base64.b64decode(raw) if isinstance(raw, str) else bytes(raw)
    if not payload:
        raise ValueError("Veo returned empty inline video bytes")
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)


def materialize_video_output(
    operation: Any,
    *,
    shot_sequence: int,
    project_id: str,
    downloader: Callable[[str, Path, str], None] = _download_gcs,
    destination: Path | None = None,
) -> tuple[Path, str | None]:
    """Materialize the first usable SDK output to one deterministic MP4."""

    destination = destination or VEO_OUTPUT_DIR / f"shot_{shot_sequence:02d}.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for generated in _generated_videos(operation):
        video = _get(generated, "video")
        raw = _get(video, "video_bytes")
        uri = _get(video, "uri")
        try:
            if raw:
                _write_video_bytes(raw, destination)
            elif uri:
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                downloader(str(uri), temporary, project_id)
                temporary.replace(destination)
            else:
                errors.append("generated_videos entry contained neither video_bytes nor uri")
                continue
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if destination.is_file() and destination.stat().st_size > 0:
            return destination, str(uri) if uri else None
        errors.append("materialized video was empty")
    raise ValueError("No usable Veo video output: " + "; ".join(errors or ["no generated_videos entries"]))


# Fractions of the clip's own length at which QA preview frames are taken.
# They reproduce the original hardcoded 0.5/2.5/5.5s exactly for an 8s clip,
# while staying inside a shorter one. The last one sits near the end: without
# it the final 2.5s of an 8s clip were never looked at.
PREVIEW_POSITION_FRACTIONS = (1 / 16, 5 / 16, 11 / 16, 15 / 16)


def select_clip_duration_seconds(
    shot_duration_seconds: float,
    max_duration_seconds: int,
    allowed: Sequence[int] = VEO_ALLOWED_DURATION_SECONDS,
) -> int:
    """Shortest allowed Veo clip length that still covers the whole shot.

    Raises `ValueError` when the shot is longer than any allowed length -- Veo
    produces one fixed-length clip, and one clip alone cannot cover such a
    shot. `select_clip_plan` decides whether an extension can.
    """
    candidates = sorted(value for value in allowed if value <= max_duration_seconds)
    for duration in candidates:
        if duration + CLIP_DURATION_EPSILON >= shot_duration_seconds:
            return duration
    raise ValueError(
        f"Shot is {shot_duration_seconds:.2f}s, longer than the longest available Veo clip "
        f"({max(candidates) if candidates else max_duration_seconds}s)"
    )


def is_extension_length(shot_duration_seconds: float) -> bool:
    """True when the shot is longer than one clip but fits one clip + one extension."""

    return (
        EXTENDED_FIRST_CLIP_SECONDS + CLIP_DURATION_EPSILON
        < shot_duration_seconds
        <= EXTENDED_FIRST_CLIP_SECONDS + VEO_EXTENSION_SECONDS + CLIP_DURATION_EPSILON
    )


def select_clip_plan(
    shot_duration_seconds: float, max_duration_seconds: int, *,
    extend: bool = False, resolution: str = "720p",
) -> tuple[int, int]:
    """(first clip seconds, extension seconds); extension 0 means today's single clip.

    An extension is only ever one 7s extension of an 8s clip at 720p: the one
    way it was tested live (2026-09-30).
    """

    try:
        return select_clip_duration_seconds(shot_duration_seconds, max_duration_seconds), 0
    except ValueError:
        if not extend:
            raise
    d = shot_duration_seconds
    first, ext = EXTENDED_FIRST_CLIP_SECONDS, VEO_EXTENSION_SECONDS
    if max_duration_seconds < first:
        raise ValueError(
            f"Shot is {d:.2f}s; extending a Veo clip needs VEO_DURATION_SECONDS of {first}, "
            f"but it is {max_duration_seconds}"
        )
    if resolution not in VEO_EXTENSION_RESOLUTIONS:
        raise ValueError(
            f"Shot is {d:.2f}s; Veo video extension is only used at "
            f"{', '.join(VEO_EXTENSION_RESOLUTIONS)}, not {resolution}"
        )
    if d > first + ext + CLIP_DURATION_EPSILON:
        raise ValueError(
            f"Shot is {d:.2f}s, longer than one {first}s Veo clip plus one {ext}s extension "
            f"({first + ext}s)"
        )
    return first, ext


def extended_preview_positions(shown_seconds: float, join_seconds: float = 8.0) -> list[float]:
    """QA frame times for an extended clip: the first clip, both sides of the
    join, and the end of the shown part. Frames 3 and 4 sit either side of the join."""

    tail = shown_seconds - join_seconds
    positions = [0.5, 4.0, 7.5, join_seconds + min(0.5, tail / 3)]
    if tail >= 1.5:
        positions.append(join_seconds + tail / 2)
    positions.append(shown_seconds - min(0.25, tail / 3))
    return [round(position, 3) for position in positions]


def extract_video_previews(
    video_path: Path, shot_sequence: int, clip_duration_seconds: float,
    shown_duration_seconds: float = 0.0, *, positions: Sequence[float] | None = None,
) -> list[str]:
    """Extract four deterministic JPEGs for semantic clip QA.

    Remotion shows only the first `shown_duration_seconds` of the clip, so the
    frames are sampled within that part: a defect after it is never seen, and
    one inside it must not be missed. An unknown (0) or longer shown duration
    samples the whole clip. `positions`, when given, are the frame times instead.
    """

    VEO_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_binary("ffmpeg")
    paths: list[str] = []
    errors: list[str] = []
    sampled_seconds = clip_duration_seconds
    if 0 < shown_duration_seconds < clip_duration_seconds:
        sampled_seconds = shown_duration_seconds
    if positions is None:
        positions = tuple(round(sampled_seconds * f, 3) for f in PREVIEW_POSITION_FRACTIONS)
    for index, position in enumerate(positions, start=1):
        output = VEO_PREVIEW_DIR / f"shot_{shot_sequence:02d}_{index:02d}.jpg"
        output.unlink(missing_ok=True)
        completed = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(position), "-i", str(video_path), "-frames:v", "1", str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and output.is_file() and output.stat().st_size > 0:
            paths.append(str(output))
        else:
            errors.append(
                f"{position}s: exit {completed.returncode} {(completed.stderr or '').strip()[:200]}"
            )

    # Clip QA is impossible without frames, and an agent cannot fix a broken
    # ffmpeg by retrying. Failing loudly here turns three wasted attempts and an
    # opaque "preview_paths was empty" halt into one actionable message.
    if not paths:
        raise RuntimeError(
            f"ffmpeg ({ffmpeg}) extracted no preview frames from {video_path}: "
            + "; ".join(errors)
        )
    return paths


# A retry's correction is a note on the motion, not a second scene description:
# live corrections grew past 500 words re-describing the whole corridor.
VIDEO_CORRECTION_MAX_WORDS = 60
STATIC_CAMERA = "Static shot: the camera remains completely still."
# The extension continues a finished movement as a quiet hold: the tested
# extension kept moving and talking when asked for more motion.
EXTENSION_MOTION = (
    "The movement has finished. Whoever is in the picture stays in the pose they have "
    "reached and holds almost still, breathing slowly, with the mouth closed and the face turned to "
    "the viewer. Nothing new happens and nothing new appears."
)
EXTENSION_NEGATIVE_EXTRA = ", talking, speaking, open mouth, turning away"


def _prompt_text(text: str) -> str:
    # Google's Veo guidance: quotation marks invite the model to render text.
    return " ".join(text.replace('"', "").split())


def build_video_prompt(shot: Shot, seed: VeoSeedContract, correction: str = "") -> tuple[str, str]:
    """The motion-only prompt and the negative prompt for one image-to-video call.

    The seed already fixes subject, scene and style, so Google's image-to-video
    guidance is to prompt the motion alone: re-describing the picture confuses
    the model. Veo 3.x also runs every prompt through a rewriter that adds
    camera moves and cannot be turned off, and a long list of "do not"s only
    hands it more words to build on -- so what to avoid goes in the negative
    prompt, as plain nouns. `video_motion_intent` is the planner's motion for
    this shot as video; `motion_plan` also serves the still fallback.

    The camera is always static. Veo repeatedly ignored "locked-off" wording
    and tracked or pushed in on its own, but holding still while the subject
    moves is what it does most reliably, so that is the only camera it is
    asked for -- first, in Google's own term, as its prompt guide advises.
    """

    motion = _prompt_text(shot.video_motion_intent or shot.motion_plan)
    prompt = (
        f"{STATIC_CAMERA} {motion} Only what this describes moves; everything else in the frame "
        "stays where it is. "
        "Keep the starting frame's art style, colours and every person in it exactly as drawn, "
        "with the same faces throughout."
    )
    words = correction.split()
    if words:
        prompt += (
            " Adjustment from the last take: "
            + _prompt_text(" ".join(words[:VIDEO_CORRECTION_MAX_WORDS]))
        )
    # Last, so it also outweighs the plan and the correction: a plan that said
    # "text fades in softly over the closing seconds" made Veo burn garbled
    # pseudo-text into a clip whose seed was clean.
    prompt += " The picture stays a clean plate with no writing anywhere."
    negative_prompt = (
        "text, captions, subtitles, logo, watermark, user interface, extra people, duplicate subjects, "
        "extra limbs, morphing, photorealistic live action, camera movement, camera shake, dolly, zoom, "
        "tracking shot"
    )
    return prompt, negative_prompt


def build_extension_prompt(shot: Shot, seed: VeoSeedContract) -> tuple[str, str]:
    """The prompt and negative prompt for extending clip 1. It never carries a
    retry's correction: that note is about clip 1's motion."""

    prompt = (
        f"{STATIC_CAMERA} Continue this same moment from the video's last frame. {EXTENSION_MOTION} "
        "Keep the video's art style, colours and every person in it exactly as drawn, with the same "
        "faces throughout. The picture stays a clean plate with no writing anywhere."
    )
    return prompt, build_video_prompt(shot, seed)[1] + EXTENSION_NEGATIVE_EXTRA


def _failure(
    *,
    shot: Shot,
    attempt: int,
    code: str,
    reason: str,
    retryable: bool,
    cost_usd: float,
    operation_name: str | None = None,
    operation_dump_path: Path | None = None,
    rai_count: int = 0,
    rai_reasons: list[str] | None = None,
    operation_error: Any = None,
    stage: str = "veo_generation",
    extra_partial_paths: Sequence[str] = (),
) -> VeoOutcomeContract:
    failure = VeoFailureContract(
        produced_by="veo_tool",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        stage=stage,
        code=code,
        reason=reason,
        retryable=retryable,
        attempt=attempt,
        operation_name=operation_name,
        operation_dump_path=str(operation_dump_path) if operation_dump_path else None,
        rai_media_filtered_count=rai_count,
        rai_media_filtered_reasons=list(rai_reasons or []),
        operation_error=operation_error,
        partial_artifact_paths=(
            [str(operation_dump_path)] if operation_dump_path else []
        ) + [str(path) for path in extra_partial_paths],
        cost_usd=cost_usd,
    )
    return VeoOutcomeContract(
        produced_by="veo_tool",
        status="FAILURE",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        failure=failure,
    )


def inspect_completed_operation(
    operation: Any,
    *,
    shot: Shot,
    seed: VeoSeedContract,
    model: str,
    project_id: str,
    attempt: int,
    cost_usd: float,
    clip_duration_seconds: float,
    downloader: Callable[[str, Path, str], None] = _download_gcs,
) -> VeoOutcomeContract:
    """Preserve and inspect both supported SDK roots, returning no bare errors."""

    VEO_OPERATION_DIR.mkdir(parents=True, exist_ok=True)
    dump_path = VEO_OPERATION_DIR / f"shot_{shot.sequence:02d}_attempt_{attempt:02d}.json"
    _atomic_write_json(dump_path, operation_to_jsonable(operation))
    operation_name = _get(operation, "name")
    rai_count, rai_reasons = _rai_details(operation)
    operation_error = _operation_error(operation)
    if rai_count or rai_reasons:
        return _failure(
            shot=shot,
            attempt=attempt,
            code="RAI_FILTERED",
            reason="Veo safety filtering rejected one or more generated media outputs",
            retryable=True,
            cost_usd=cost_usd,
            operation_name=operation_name,
            operation_dump_path=dump_path,
            rai_count=rai_count,
            rai_reasons=rai_reasons,
            operation_error=operation_error,
        )
    if operation_error is not None:
        return _failure(
            shot=shot,
            attempt=attempt,
            code="OPERATION_ERROR",
            reason="Veo completed with an operation error",
            retryable=True,
            cost_usd=cost_usd,
            operation_name=operation_name,
            operation_dump_path=dump_path,
            operation_error=operation_error,
        )
    try:
        local_path, gcs_uri = materialize_video_output(
            operation,
            shot_sequence=shot.sequence,
            project_id=project_id,
            downloader=downloader,
        )
    except Exception as exc:
        return _failure(
            shot=shot,
            attempt=attempt,
            code="MALFORMED_OUTPUT",
            reason=f"{type(exc).__name__}: {exc}",
            retryable=True,
            cost_usd=cost_usd,
            operation_name=operation_name,
            operation_dump_path=dump_path,
        )

    try:
        previews = extract_video_previews(
            local_path, shot.sequence, clip_duration_seconds,
            shown_duration_seconds=shot.end_seconds - shot.start_seconds,
        )
    except Exception as exc:
        return _failure(
            shot=shot,
            attempt=attempt,
            code="PREVIEW_EXTRACTION_FAILED",
            reason=f"{type(exc).__name__}: {exc}",
            retryable=True,
            cost_usd=cost_usd,
            operation_name=operation_name,
            operation_dump_path=dump_path,
        )
    # Evidence for clip QA, never a reason to lose a paid clip: a measurement
    # that breaks is reported as unmeasured.
    try:
        camera_motion = describe_camera_motion(measure_camera_motion(local_path))
    except Exception as exc:
        camera_motion = f"Camera motion could not be measured ({type(exc).__name__}: {exc})."
    log(f"Shot {shot.sequence}: {camera_motion}", indent=2)
    result = VeoResultContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        seed=seed,
        local_video_path=str(local_path),
        video_sha256=sha256_file(local_path),
        gcs_uri=gcs_uri,
        operation_name=operation_name,
        operation_dump_path=str(dump_path),
        preview_paths=previews,
        camera_motion=camera_motion,
        model=model,
        approved=False,
        qa_summary="",
        cost_usd=cost_usd,
    )
    # The tool returns a structurally successful but not-yet-approved result;
    # the agent must inspect previews and turn approval on before its final
    # VeoOutcomeContract can validate as SUCCESS.
    return VeoOutcomeContract(
        produced_by="veo_tool",
        status="SUCCESS",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        result=result,
    )


def _strip_audio_track(path: Path) -> str | None:
    """Drop the extension's silent audio track with a lossless copy, so the
    asset is video-only like every single clip. Returns the error text, if any."""

    temporary = path.with_suffix(".noaudio.mp4")
    try:
        completed = subprocess.run(
            [
                resolve_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(path), "-map", "0:v:0", "-c", "copy", "-an", str(temporary),
            ],
            capture_output=True, text=True, check=False,
        )
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        return f"exit {completed.returncode} {(completed.stderr or '').strip()[:200]}"
    temporary.replace(path)
    return None


def _clip_duration_seconds(path: Path) -> float:
    return _ffprobe_duration(path)


def _submit_and_poll(
    client: Any,
    *,
    source: Any,
    config: Any,
    model: str,
    span_name: str,
    span_input: dict,
    span_meta: dict,
    shot: Shot,
    attempt: int,
    cost_usd: float,
    poll_seconds: float,
    max_poll_seconds: float,
    dump_path: Path,
    code_prefix: str,
    reason_prefix: str,
    waiting_label: str,
    extra_partial_paths: Sequence[str] = (),
) -> tuple[Any | None, VeoOutcomeContract | None]:
    """One submit-and-poll for the extended path; mirrors `run_veo_generation`'s."""

    def dumped_failure(operation: Any, code: str, reason: str) -> VeoOutcomeContract:
        VEO_OPERATION_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(dump_path, operation_to_jsonable(operation))
        return _failure(
            shot=shot, attempt=attempt, code=code_prefix + code, reason=reason_prefix + reason,
            retryable=True, cost_usd=cost_usd, operation_name=_get(operation, "name"),
            operation_dump_path=dump_path, extra_partial_paths=extra_partial_paths,
        )

    with vendor_slot(VEO), tracing.span(span_name, as_type="tool", input=span_input,
                                        metadata=span_meta) as request_span:
        try:
            operation = client.models.generate_videos(model=model, source=source, config=config)
        except Exception as exc:
            # No operation to dump yet: the earlier clip's dump, if any, stands in.
            return None, _failure(
                shot=shot, attempt=attempt, code=code_prefix + "SDK_ERROR",
                reason=reason_prefix + f"Veo request failed: {type(exc).__name__}: {exc}",
                retryable=True, cost_usd=cost_usd,
                operation_dump_path=Path(extra_partial_paths[0]) if extra_partial_paths else None,
                extra_partial_paths=extra_partial_paths[1:],
            )
        request_span.update(output={"operation_name": _get(operation, "name")})
        deadline = time.monotonic() + max_poll_seconds
        poll_started = time.monotonic()
        log(f"Shot {shot.sequence}: {waiting_label} sent off, now waiting for it", indent=2)
        while not bool(_get(operation, "done", default=False)):
            if time.monotonic() >= deadline:
                return None, dumped_failure(
                    operation, "POLL_TIMEOUT",
                    f"Veo operation did not complete within {max_poll_seconds} seconds",
                )
            time.sleep(poll_seconds)
            log(
                f"Shot {shot.sequence}: still {'generating' if not code_prefix else 'extending'}… "
                f"{format_duration(time.monotonic() - poll_started)} so far",
                indent=2,
            )
            try:
                operation = client.operations.get(operation)
            except Exception as exc:
                return None, dumped_failure(
                    operation, "SDK_ERROR", f"Veo polling failed: {type(exc).__name__}: {exc}",
                )
    return operation, None


def _check_completed(
    operation: Any,
    *,
    shot: Shot,
    attempt: int,
    cost_usd: float,
    dump_path: Path,
    code_prefix: str,
    reason_prefix: str,
    extra_partial_paths: Sequence[str],
    dump_extra: dict | None = None,
) -> VeoOutcomeContract | None:
    """Dump a finished operation; a safety refusal or operation error is a failure."""

    VEO_OPERATION_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(dump_path, {**operation_to_jsonable(operation), **(dump_extra or {})})
    operation_name = _get(operation, "name")
    rai_count, rai_reasons = _rai_details(operation)
    operation_error = _operation_error(operation)
    if rai_count or rai_reasons:
        return _failure(
            shot=shot, attempt=attempt, code=code_prefix + "RAI_FILTERED",
            reason=reason_prefix + "Veo safety filtering rejected one or more generated media outputs",
            retryable=True, cost_usd=cost_usd, operation_name=operation_name,
            operation_dump_path=dump_path, rai_count=rai_count, rai_reasons=rai_reasons,
            operation_error=operation_error, extra_partial_paths=extra_partial_paths,
        )
    if operation_error is not None:
        return _failure(
            shot=shot, attempt=attempt, code=code_prefix + "OPERATION_ERROR",
            reason=reason_prefix + "Veo completed with an operation error",
            retryable=True, cost_usd=cost_usd, operation_name=operation_name,
            operation_dump_path=dump_path, operation_error=operation_error,
            extra_partial_paths=extra_partial_paths,
        )
    return None


def _run_extended_generation(
    client: Any,
    extension_client: Any,
    *,
    shot: Shot,
    seed: VeoSeedContract,
    project_id: str,
    model: str,
    gcs_output_uri: str,
    resolution: str,
    first_seconds: int,
    extension_seconds: int,
    poll_seconds: float,
    max_poll_seconds: float,
    attempt: int,
    cost_usd: float,
    correction: str,
) -> VeoOutcomeContract:
    """A shot longer than one clip: an image-to-video clip, then one Veo
    extension of it, still one tool call. Clip 1's failures keep today's codes;
    the extension's carry an `EXTENSION_` prefix and cost both calls."""

    shown = shot.end_seconds - shot.start_seconds
    nn = f"{shot.sequence:02d}"
    seed_number = 1000 + shot.sequence + attempt - 1

    def config(duration: int, negative_prompt: str) -> Any:
        return types.GenerateVideosConfig(
            number_of_videos=1,
            duration_seconds=duration,
            aspect_ratio="9:16",
            resolution=resolution,
            output_gcs_uri=gcs_output_uri,
            generate_audio=False,
            enhance_prompt=True,
            negative_prompt=negative_prompt,
            person_generation="allow_adult",
            seed=seed_number,
        )

    def span_meta(duration: int, **extra: Any) -> dict:
        return {
            "provider": "gemini", "model": model, "duration_seconds": duration,
            "aspect_ratio": "9:16", "resolution": resolution, "gcs_output_uri": gcs_output_uri,
            "attempt": attempt, "shot_sequence": shot.sequence, **extra,
        }

    # Clip 1: today's image-to-video call at the full first-clip length.
    prompt, negative_prompt = build_video_prompt(shot, seed, correction)
    clip1_dump = VEO_OPERATION_DIR / f"shot_{nn}_attempt_{attempt:02d}.json"
    operation, failure = _submit_and_poll(
        client,
        source=types.GenerateVideosSource(
            prompt=prompt, image=types.Image.from_file(location=seed.local_path),
        ),
        config=config(first_seconds, negative_prompt),
        model=model,
        span_name="veo.generate_videos",
        span_input={"prompt": prompt, "negative_prompt": negative_prompt,
                    "seed_image_path": seed.local_path},
        span_meta=span_meta(first_seconds, extension_seconds=extension_seconds),
        shot=shot, attempt=attempt, cost_usd=cost_usd,
        poll_seconds=poll_seconds, max_poll_seconds=max_poll_seconds,
        dump_path=clip1_dump, code_prefix="", reason_prefix="", waiting_label="video",
    )
    if failure is None:
        failure = _check_completed(
            operation, shot=shot, attempt=attempt, cost_usd=cost_usd, dump_path=clip1_dump,
            code_prefix="", reason_prefix="", extra_partial_paths=(),
        )
    if failure is not None:
        return failure
    clip1_name = _get(operation, "name")

    def clip1_failure(code: str, reason: str, retryable: bool, **extra: Any) -> VeoOutcomeContract:
        return _failure(
            shot=shot, attempt=attempt, code=code, reason=reason, retryable=retryable,
            cost_usd=cost_usd, operation_name=clip1_name, operation_dump_path=clip1_dump, **extra,
        )

    videos = _generated_videos(operation)
    if not videos:
        return clip1_failure("MALFORMED_OUTPUT", "No usable Veo video output: no generated_videos entries",
                             True)
    clip1_uri = str(_get(videos[0], "video", "uri", default=""))
    if not clip1_uri.startswith("gs://"):
        return clip1_failure(
            "EXTENSION_NEEDS_GCS_URI",
            "Veo returned the first clip without a gs:// URI, and only a stored clip can be extended",
            False,
        )
    EXTENSION_PARTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        clip1_path, _ = materialize_video_output(
            operation, shot_sequence=shot.sequence, project_id=project_id,
            downloader=_download_gcs, destination=EXTENSION_PARTS_DIR / f"shot_{nn}_clip1.mp4",
        )
    except Exception as exc:
        return clip1_failure("MALFORMED_OUTPUT", f"{type(exc).__name__}: {exc}", True)

    # Early camera gate: an extension cannot undo clip 1's camera move, so a
    # clip already out of band is not extended at the cost of another call.
    try:
        motion = measure_camera_motion(clip1_path)
    except Exception:
        motion = None
    if motion is not None:
        zoom, pan_x, pan_y = motion["zoom"], motion["pan_x_percent"], motion["pan_y_percent"]
        low, high = STATIC_CAMERA_ZOOM_BAND
        if (not low <= zoom <= high or abs(pan_x) > STATIC_CAMERA_MAX_PAN_PERCENT
                or abs(pan_y) > STATIC_CAMERA_MAX_PAN_PERCENT):
            log(f"Shot {shot.sequence}: first {first_seconds}s clip's camera moved (zoom x{zoom:.2f}) "
                "— not extending it", error=True)
            return clip1_failure(
                "FIRST_CLIP_CAMERA_MOVED",
                f"The first {first_seconds}s clip's camera moved (zoom x{zoom:.2f}, pan {pan_x:+d}% / "
                f"{pan_y:+d}%), outside the static-camera limits (zoom x0.92-x1.08, pan within 5%), "
                "so it was not extended",
                True, stage="clip_qa", extra_partial_paths=[str(clip1_path)],
            )

    # The extension: same model, config and seed, continuing clip 1's video.
    log(f"Shot {shot.sequence}: first {first_seconds}s of video done, now extending it by "
        f"{extension_seconds}s", indent=2)
    ext_prompt, ext_negative = build_extension_prompt(shot, seed)
    ext_dump = VEO_OPERATION_DIR / f"shot_{nn}_attempt_{attempt:02d}_extension.json"
    both_cost = 2 * cost_usd
    parts = [str(clip1_dump), str(clip1_path)]
    ext_operation, failure = _submit_and_poll(
        extension_client,
        source=types.GenerateVideosSource(
            prompt=ext_prompt, video=types.Video(uri=clip1_uri, mime_type="video/mp4"),
        ),
        config=config(extension_seconds, ext_negative),
        model=model,
        span_name="veo.extend_video",
        span_input={"prompt": ext_prompt, "negative_prompt": ext_negative,
                    "source_video_uri": clip1_uri},
        span_meta=span_meta(extension_seconds, extends_operation=clip1_name),
        shot=shot, attempt=attempt, cost_usd=both_cost,
        poll_seconds=poll_seconds, max_poll_seconds=max_poll_seconds,
        dump_path=ext_dump, code_prefix="EXTENSION_", reason_prefix="Extension: ",
        waiting_label="extension", extra_partial_paths=parts,
    )
    if failure is None:
        failure = _check_completed(
            ext_operation, shot=shot, attempt=attempt, cost_usd=both_cost, dump_path=ext_dump,
            code_prefix="EXTENSION_", reason_prefix="Extension: ", extra_partial_paths=parts,
            dump_extra={"extends_operation_dump_path": str(clip1_dump)},
        )
    if failure is not None:
        return failure
    ext_name = _get(ext_operation, "name")

    def ext_failure(code: str, reason: str, retryable: bool = True) -> VeoOutcomeContract:
        return _failure(
            shot=shot, attempt=attempt, code=code, reason=reason, retryable=retryable,
            cost_usd=both_cost, operation_name=ext_name, operation_dump_path=ext_dump,
            extra_partial_paths=parts,
        )

    try:
        local_path, gcs_uri = materialize_video_output(
            ext_operation, shot_sequence=shot.sequence, project_id=project_id, downloader=_download_gcs,
        )
    except Exception as exc:
        return ext_failure("EXTENSION_MALFORMED_OUTPUT", f"Extension: {type(exc).__name__}: {exc}")
    error = _strip_audio_track(local_path)
    if error:
        log(f"Shot {shot.sequence}: could not remove the silent audio track from the extended clip "
            f"({error}); keeping it")
    try:
        got = _clip_duration_seconds(local_path)
    except Exception as exc:
        return ext_failure("EXTENSION_MALFORMED_OUTPUT", f"Extension: {type(exc).__name__}: {exc}")
    if got < shown - CLIP_DURATION_EPSILON:
        return ext_failure(
            "EXTENSION_TOO_SHORT",
            f"Extension: Veo returned {got:.2f}s of video, shorter than the {shown:.2f}s the shot "
            f"shows (expected the combined {first_seconds + extension_seconds}s clip)",
            retryable=False,
        )
    positions = extended_preview_positions(shown, float(first_seconds))
    try:
        previews = extract_video_previews(
            local_path, shot.sequence, float(first_seconds + extension_seconds),
            shown_duration_seconds=shown,
            positions=positions,
        )
    except Exception as exc:
        return ext_failure("EXTENSION_PREVIEW_EXTRACTION_FAILED",
                           f"Extension: {type(exc).__name__}: {exc}")
    # The join note names frames 3 and 4, so a skipped frame would shift them.
    if len(previews) != len(positions):
        return ext_failure("EXTENSION_PREVIEW_EXTRACTION_FAILED",
                           f"Extension: extracted {len(previews)} of {len(positions)} preview frames")
    try:
        camera_motion = describe_camera_motion(measure_camera_motion(local_path, max_seconds=shown))
    except Exception as exc:
        camera_motion = f"Camera motion could not be measured ({type(exc).__name__}: {exc})."
    camera_motion += (
        f" This clip is one {first_seconds}s Veo clip extended by {extension_seconds}s; the reel "
        f"shows its first {shown:.2f}s and the camera was measured over that part. Preview frames 3 "
        f"and 4 sit just before and after the join at {float(first_seconds):.1f}s: pose, face, "
        "clothes, art style and lighting must carry straight across it, and the face stays toward "
        "the viewer to the end."
    )
    log(f"Shot {shot.sequence}: {camera_motion}", indent=2)
    result = VeoResultContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        seed=seed,
        local_video_path=str(local_path),
        video_sha256=sha256_file(local_path),
        gcs_uri=gcs_uri,
        operation_name=ext_name,
        operation_dump_path=str(ext_dump),
        preview_paths=previews,
        camera_motion=camera_motion,
        model=model,
        approved=False,
        qa_summary="",
        cost_usd=both_cost,
    )
    return VeoOutcomeContract(
        produced_by="veo_tool",
        status="SUCCESS",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        result=result,
    )


def run_veo_generation(
    client: genai.Client,
    *,
    shot: Shot,
    seed: VeoSeedContract,
    project_id: str,
    model: str,
    gcs_output_uri: str,
    resolution: str,
    duration_seconds: int,
    poll_seconds: float,
    max_poll_seconds: float,
    attempt: int,
    cost_usd: float,
    correction: str = "",
    extend_long_shots: bool = False,
    extension_client: genai.Client | None = None,
) -> VeoOutcomeContract:
    """Validate the seed before the paid call, then poll and inspect it."""

    try:
        seed.validate_for_shot(shot)
    except Exception as exc:
        return _failure(
            shot=shot,
            attempt=attempt,
            code="UNSAFE_SEED",
            reason=f"{type(exc).__name__}: {exc}",
            retryable=False,
            cost_usd=0.0,
        )

    # `duration_seconds` is the configured ceiling; the clip is generated at
    # the shortest allowed length that still covers this shot, so a 5s shot
    # doesn't pay for 8s of video. A shot longer than the ceiling never
    # reaches here -- `_promote_video_candidates` refuses to promote it.
    try:
        clip_duration_seconds = select_clip_duration_seconds(
            shot.end_seconds - shot.start_seconds, duration_seconds,
        )
    except ValueError as exc:
        # With VEO_EXTEND_LONG_SHOTS on, a shot longer than one clip may be
        # one clip plus an extension; a shot that fits one clip never gets here.
        if extend_long_shots:
            try:
                first, ext = select_clip_plan(shot.end_seconds - shot.start_seconds, duration_seconds,
                                              extend=True, resolution=resolution)
            except ValueError as ext_exc:
                return _failure(shot=shot, attempt=attempt, code="UNSAFE_SEED", reason=str(ext_exc),
                                retryable=False, cost_usd=0.0)
            return _run_extended_generation(
                client, extension_client or client, shot=shot, seed=seed, project_id=project_id,
                model=model, gcs_output_uri=gcs_output_uri, resolution=resolution,
                first_seconds=first, extension_seconds=ext, poll_seconds=poll_seconds,
                max_poll_seconds=max_poll_seconds, attempt=attempt, cost_usd=cost_usd,
                correction=correction,
            )
        return _failure(
            shot=shot,
            attempt=attempt,
            code="UNSAFE_SEED",
            reason=str(exc),
            retryable=False,
            cost_usd=0.0,
        )

    prompt, negative_prompt = build_video_prompt(shot, seed, correction)
    # One VEO_CONCURRENCY slot per operation, held from submit until the
    # operation is done: that is the span during which it counts against Veo.
    # The trace span records the request exactly as sent (FR14); it opens once
    # the slot is held, so its duration is Veo's, not the queue's.
    with vendor_slot(VEO), tracing.span("veo.generate_videos", as_type="tool", input={
        "prompt": prompt, "negative_prompt": negative_prompt, "seed_image_path": seed.local_path,
    }, metadata={
        "provider": "gemini", "model": model, "duration_seconds": clip_duration_seconds,
        "aspect_ratio": "9:16", "resolution": resolution, "gcs_output_uri": gcs_output_uri,
        "attempt": attempt, "shot_sequence": shot.sequence,
    }) as request_span:
        try:
            image = types.Image.from_file(location=seed.local_path)
            operation = client.models.generate_videos(
                model=model,
                source=types.GenerateVideosSource(prompt=prompt, image=image),
                config=types.GenerateVideosConfig(
                    number_of_videos=1,
                    duration_seconds=clip_duration_seconds,
                    aspect_ratio="9:16",
                    resolution=resolution,
                    output_gcs_uri=gcs_output_uri,
                    generate_audio=False,
                    enhance_prompt=True,
                    negative_prompt=negative_prompt,
                    person_generation="allow_adult",
                    seed=1000 + shot.sequence + attempt - 1,
                ),
            )
        except Exception as exc:
            return _failure(
                shot=shot,
                attempt=attempt,
                code="SDK_ERROR",
                reason=f"Veo request failed: {type(exc).__name__}: {exc}",
                retryable=True,
                cost_usd=cost_usd,
            )
        request_span.update(output={"operation_name": _get(operation, "name")})
        deadline = time.monotonic() + max_poll_seconds
        # The poll loop is the single longest silence in a run (up to
        # `max_poll_seconds`), so every pass reports rather than waiting mutely.
        poll_started = time.monotonic()
        log(f"Shot {shot.sequence}: video sent off to be generated, now waiting for it", indent=2)
        while not bool(_get(operation, "done", default=False)):
            if time.monotonic() >= deadline:
                VEO_OPERATION_DIR.mkdir(parents=True, exist_ok=True)
                dump_path = VEO_OPERATION_DIR / f"shot_{shot.sequence:02d}_attempt_{attempt:02d}.json"
                _atomic_write_json(dump_path, operation_to_jsonable(operation))
                return _failure(
                    shot=shot,
                    attempt=attempt,
                    code="POLL_TIMEOUT",
                    reason=f"Veo operation did not complete within {max_poll_seconds} seconds",
                    retryable=True,
                    cost_usd=cost_usd,
                    operation_name=_get(operation, "name"),
                    operation_dump_path=dump_path,
                )
            time.sleep(poll_seconds)
            log(
                f"Shot {shot.sequence}: still generating… "
                f"{format_duration(time.monotonic() - poll_started)} so far",
                indent=2,
            )
            try:
                operation = client.operations.get(operation)
            except Exception as exc:
                VEO_OPERATION_DIR.mkdir(parents=True, exist_ok=True)
                dump_path = VEO_OPERATION_DIR / f"shot_{shot.sequence:02d}_attempt_{attempt:02d}.json"
                _atomic_write_json(dump_path, operation_to_jsonable(operation))
                return _failure(
                    shot=shot,
                    attempt=attempt,
                    code="SDK_ERROR",
                    reason=f"Veo polling failed: {type(exc).__name__}: {exc}",
                    retryable=True,
                    cost_usd=cost_usd,
                    operation_name=_get(operation, "name"),
                    operation_dump_path=dump_path,
                )
    return inspect_completed_operation(
        operation,
        shot=shot,
        seed=seed,
        model=model,
        project_id=project_id,
        attempt=attempt,
        cost_usd=cost_usd,
        clip_duration_seconds=clip_duration_seconds,
    )


def _preview_content(path: Path) -> dict:
    return {
        "type": "image",
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        "mimeType": "image/jpeg",
    }


@tool(
    "generate_veo_clip",
    "Generate one Veo clip from an approved contracted seed and return previews for semantic QA",
    {
        "shot": dict,
        "seed": dict,
        "project_id": str,
        "location": str,
        "model": str,
        "gcs_output_uri": str,
        "resolution": str,
        "duration_seconds": int,
        "poll_seconds": float,
        "max_poll_seconds": float,
        "attempt": int,
        "cost_usd": float,
        "correction": str,
    },
)
async def generate_veo_clip(args: dict) -> dict:
    # Submit, sleep-polling, the GCS download and ffmpeg all block, so the body
    # runs in a worker thread and the event loop stays free (Story 4.3).
    return await asyncio.to_thread(_traced_veo_clip, args)


def _traced_veo_clip(args: dict) -> dict:
    # Runs in the worker thread; the copied context keeps the span under its shot.
    return tracing.traced_tool_call("generate_veo_clip", _generate_veo_clip, args, metadata={
        "provider": "gemini", "model": args.get("model"), "gcs_output_uri": args.get("gcs_output_uri"),
        "duration_ceiling_seconds": args.get("duration_seconds"), "aspect_ratio": "9:16",
        "resolution": args.get("resolution"),
    })


def _generate_veo_clip(args: dict) -> dict:
    shot = Shot.model_validate(authoritative_shot(args["shot"]))
    try:
        seed = VeoSeedContract.model_validate(args["seed"])
        seed.validate_for_shot(shot)
    except Exception as exc:
        outcome = _failure(
            shot=shot,
            attempt=int(args["attempt"]),
            code="UNSAFE_SEED",
            reason=f"{type(exc).__name__}: {exc}",
            retryable=False,
            cost_usd=0.0,
        )
    else:
        extend = veo_extend_long_shots_setting()
        client = genai.Client(
            vertexai=True,
            project=args["project_id"],
            location=args["location"],
            http_options=types.HttpOptions(api_version="v1"),
        )
        extension_client = None
        if extend:
            # The default API version, not v1: the 2026-09-30 live extend test
            # used a default client, and only that call is proven.
            extension_client = genai.Client(
                vertexai=True, project=args["project_id"], location=args["location"],
            )
        outcome = run_veo_generation(
            client,
            shot=shot,
            seed=seed,
            project_id=args["project_id"],
            model=args["model"],
            gcs_output_uri=args["gcs_output_uri"],
            resolution=args["resolution"],
            duration_seconds=int(args["duration_seconds"]),
            poll_seconds=float(args["poll_seconds"]),
            max_poll_seconds=float(args["max_poll_seconds"]),
            attempt=int(args["attempt"]),
            cost_usd=float(args["cost_usd"]),
            correction=args.get("correction", ""),
            extend_long_shots=extend,
            extension_client=extension_client,
        )

    content: list[dict] = [
        {"type": "text", "text": json.dumps(outcome.model_dump(mode="json"), ensure_ascii=False)}
    ]
    if outcome.result is not None:
        content.extend(_preview_content(Path(path)) for path in outcome.result.preview_paths)
    return {"content": content}


veo_server = create_sdk_mcp_server(name="veo", tools=[generate_veo_clip])
