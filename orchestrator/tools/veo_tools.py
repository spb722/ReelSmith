"""Contracted Veo generation, result inspection, and QA preview tools."""

from __future__ import annotations

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
from orchestrator.settings import VEO_ALLOWED_DURATION_SECONDS
from orchestrator.state.run_manifest import _atomic_write_json


VEO_OUTPUT_DIR = Path("generated/veo")
VEO_PREVIEW_DIR = VEO_OUTPUT_DIR / "previews"
VEO_OPERATION_DIR = Path("metadata/veo_operations")
# Tolerance when matching a shot's fractional duration against the integer
# clip lengths Veo offers, so a 6.001s shot still takes a 6s clip.
CLIP_DURATION_EPSILON = 0.05


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
) -> tuple[Path, str | None]:
    """Materialize the first usable SDK output to one deterministic MP4."""

    destination = VEO_OUTPUT_DIR / f"shot_{shot_sequence:02d}.mp4"
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
# while staying inside a shorter one.
PREVIEW_POSITION_FRACTIONS = (1 / 16, 5 / 16, 11 / 16)


def select_clip_duration_seconds(
    shot_duration_seconds: float,
    max_duration_seconds: int,
    allowed: Sequence[int] = VEO_ALLOWED_DURATION_SECONDS,
) -> int:
    """Shortest allowed Veo clip length that still covers the whole shot.

    Raises `ValueError` when the shot is longer than any allowed length -- Veo
    produces one fixed-length clip and this pipeline never extends, stretches,
    or splits one across a shot, so such a shot cannot be a video shot at all.
    """
    candidates = sorted(value for value in allowed if value <= max_duration_seconds)
    for duration in candidates:
        if duration + CLIP_DURATION_EPSILON >= shot_duration_seconds:
            return duration
    raise ValueError(
        f"Shot is {shot_duration_seconds:.2f}s, longer than the longest available Veo clip "
        f"({max(candidates) if candidates else max_duration_seconds}s)"
    )


def extract_video_previews(
    video_path: Path, shot_sequence: int, clip_duration_seconds: float,
) -> list[str]:
    """Extract three deterministic JPEGs for semantic clip QA."""

    VEO_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    positions = tuple(round(clip_duration_seconds * f, 3) for f in PREVIEW_POSITION_FRACTIONS)
    for index, position in enumerate(positions, start=1):
        output = VEO_PREVIEW_DIR / f"shot_{shot_sequence:02d}_{index:02d}.jpg"
        output.unlink(missing_ok=True)
        completed = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(position), "-i", str(video_path), "-frames:v", "1", str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and output.is_file() and output.stat().st_size > 0:
            paths.append(str(output))
    return paths


def build_video_prompt(shot: Shot, seed: VeoSeedContract, correction: str = "") -> tuple[str, str]:
    prompt = (
        "Animate the supplied recomposed illustration as the exact starting frame. "
        "Preserve its subjects, object count, composition, palette, texture, and editorial illustration style. "
        f"Shot goal: {shot.shot_goal}. Motion: {shot.motion_plan}. "
        "Keep motion restrained and coherent. Do not add words, subtitles, UI, logos, watermarks, "
        "characters, duplicate subjects, photorealism, camera shake, or surreal distortions."
    )
    if correction.strip():
        prompt += f" Correct the prior QA defect without changing the shot's assigned mode: {correction.strip()}"
    negative_prompt = (
        "text, subtitles, user interface, app chrome, logo, watermark, extra people, duplicate subjects, "
        "photorealistic live action, camera shake, fast zoom, surreal distortion"
    )
    return prompt, negative_prompt


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
) -> VeoOutcomeContract:
    failure = VeoFailureContract(
        produced_by="veo_tool",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        stage="veo_generation",
        code=code,
        reason=reason,
        retryable=retryable,
        attempt=attempt,
        operation_name=operation_name,
        operation_dump_path=str(operation_dump_path) if operation_dump_path else None,
        rai_media_filtered_count=rai_count,
        rai_media_filtered_reasons=list(rai_reasons or []),
        operation_error=operation_error,
        partial_artifact_paths=[str(operation_dump_path)] if operation_dump_path else [],
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
        previews = extract_video_previews(local_path, shot.sequence, clip_duration_seconds)
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
        return _failure(
            shot=shot,
            attempt=attempt,
            code="UNSAFE_SEED",
            reason=str(exc),
            retryable=False,
            cost_usd=0.0,
        )

    prompt, negative_prompt = build_video_prompt(shot, seed, correction)
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
    deadline = time.monotonic() + max_poll_seconds
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
    shot = Shot.model_validate(args["shot"])
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
        client = genai.Client(
            vertexai=True,
            project=args["project_id"],
            location=args["location"],
            http_options=types.HttpOptions(api_version="v1"),
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
        )

    content: list[dict] = [
        {"type": "text", "text": json.dumps(outcome.model_dump(mode="json"), ensure_ascii=False)}
    ]
    if outcome.result is not None:
        content.extend(_preview_content(Path(path)) for path in outcome.result.preview_paths)
    return {"content": content}


veo_server = create_sdk_mcp_server(name="veo", tools=[generate_veo_clip])
