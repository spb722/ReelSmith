"""Deterministic reference-reel regression harness (Story 2.1). Compares a
rendered `.mp4` against the committed `reference_reel_metrics.json` fixture
(itself computed once from the existing, approved `remotion/out/book_reel_v1.mp4`
via this same module's `compute_fixture` -- never read live by the harness,
per Decisions) on: exact duration/fps/resolution/frame-count match, and
perceptual-hash frame comparison at each subtitle cue's centre frame
(`floor(midpoint(cue.start_seconds, cue.end_seconds) * fps)`, Design Notes).

No existing phash/regression code exists anywhere in this repo (Code Map) --
written from scratch here using only already-available dependencies
(Pillow, numpy, scipy) rather than adding a new one. This is a one-time
proof against the existing reference reel, not a per-run production gate
(Boundaries): it is `@tool`-wrapped per `run.py`'s deterministic-tool
convention, but is not wired into `run.py`.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from claude_agent_sdk import create_sdk_mcp_server, tool
from PIL import Image
from scipy.fftpack import dct

# Epic-2-context: "starting threshold Hamming distance <= 5 of 64 bits,
# flagged for calibration against first real output."
DEFAULT_PHASH_HAMMING_THRESHOLD = 5

# AAC audio tracks commonly report a slightly longer container duration than
# the video stream (encoder priming/padding) -- measured empirically on the
# reference reel itself: video stream 52.4333s vs. format/audio duration
# 52.48s (~0.047s, ~1.4 frames at 30fps). This tolerance is generous enough
# to absorb that normal padding while still catching a genuinely wrong or
# missing audio track. Flagged for calibration, same as the pHash threshold.
AUDIO_DURATION_TOLERANCE_SECONDS = 0.1

PHASH_HASH_SIZE = 8
PHASH_HIGH_FREQ_FACTOR = 4


def _run_ffprobe(args: list[str], *, video_path: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", *args],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "(no stderr output)"
        raise RuntimeError(
            f"ffprobe failed to read {video_path} (exit code {exc.returncode}): {stderr}"
        ) from exc
    return dict(
        line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line
    )


def measure_video(video_path: Path) -> dict:
    """Video stream width/height/fps/frame_count/duration (the exact
    ffprobe invocation named in Verification), plus audio-track presence
    and duration.
    """
    video_kv = _run_ffprobe([
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
        "-of", "default=noprint_wrappers=1", str(video_path),
    ], video_path=video_path)

    required_video_keys = ("width", "height", "r_frame_rate", "nb_frames", "duration")
    missing = [key for key in required_video_keys if key not in video_kv]
    if missing:
        raise RuntimeError(
            f"ffprobe's video-stream output for {video_path} is missing {missing} "
            f"(got: {video_kv or '(empty)'}) -- is this a valid video file with a video stream?"
        )

    numerator, denominator = video_kv["r_frame_rate"].split("/")
    if float(denominator) == 0:
        raise RuntimeError(
            f"ffprobe reported an unusable r_frame_rate {video_kv['r_frame_rate']!r} for "
            f"{video_path} (zero denominator)."
        )
    fps = float(numerator) / float(denominator)

    audio_kv = _run_ffprobe([
        "-select_streams", "a:0", "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1", str(video_path),
    ], video_path=video_path)
    audio_present = "duration" in audio_kv

    return {
        "width": int(video_kv["width"]),
        "height": int(video_kv["height"]),
        "fps": fps,
        "frame_count": int(video_kv["nb_frames"]),
        "duration_seconds": float(video_kv["duration"]),
        "audio_present": audio_present,
        "audio_duration_seconds": float(audio_kv["duration"]) if audio_present else None,
    }


def frame_centre_index(cue: dict, fps: float) -> int:
    midpoint = (cue["start_seconds"] + cue["end_seconds"]) / 2
    return math.floor(midpoint * fps)


def extract_frames(video_path: Path, frame_indices: list[int]) -> dict[int, Image.Image]:
    """Extract each requested (0-indexed) frame number in a single ffmpeg
    decode pass -- one `select` filter naming every index, rather than
    seeking once per frame.
    """
    unique_sorted = sorted(set(frame_indices))
    select_expr = "+".join(f"eq(n\\,{index})" for index in unique_sorted)
    with tempfile.TemporaryDirectory() as tmp_dir:
        pattern = Path(tmp_dir) / "frame_%04d.png"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(video_path),
                "-vf", f"select='{select_expr}'", "-fps_mode", "passthrough",
                str(pattern),
            ],
            check=True,
        )
        output_files = sorted(Path(tmp_dir).glob("frame_*.png"))
        if len(output_files) != len(unique_sorted):
            # `select` + `-fps_mode passthrough` emits matched frames strictly
            # in increasing frame-number order, so a short count always means
            # the missing ones are the highest-numbered requested indices
            # (i.e. beyond this video's actual frame count).
            missing_indices = unique_sorted[len(output_files):]
            raise RuntimeError(
                f"Expected {len(unique_sorted)} extracted frames from {video_path}, got "
                f"{len(output_files)}. Missing frame index(es) (likely out of range for "
                f"this video's frame count): {missing_indices}."
            )
        frames: dict[int, Image.Image] = {}
        for index, path in zip(unique_sorted, output_files):
            with Image.open(path) as image:
                frames[index] = image.convert("RGB").copy()
    return frames


def compute_phash(image: Image.Image) -> int:
    """Standard 64-bit DCT perceptual hash (the well-known `phash`
    algorithm): resize to 32x32 grayscale, 2D DCT, keep the top-left 8x8
    low-frequency block, threshold each coefficient against the block's own
    median. Implemented directly (Pillow + numpy + scipy, all already
    dependencies of this repo) rather than adding a new `imagehash` dependency.
    """
    size = PHASH_HASH_SIZE * PHASH_HIGH_FREQ_FACTOR
    pixels = np.asarray(image.convert("L").resize((size, size), Image.LANCZOS), dtype=float)
    dct_full = dct(dct(pixels, axis=0), axis=1)
    dct_low = dct_full[:PHASH_HASH_SIZE, :PHASH_HASH_SIZE]
    median = np.median(dct_low)
    bits = (dct_low > median).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def compute_fixture(video_path: Path, cues: list[dict]) -> dict:
    """One-time computation of `reference_reel_metrics.json`'s content from
    the existing, approved reference render. Uses the exact same
    measurement/extraction/hashing code the harness itself later compares
    against, so fixture and harness can never silently drift apart.
    """
    measured = measure_video(video_path)
    frame_indices = [frame_centre_index(cue, measured["fps"]) for cue in cues]
    frames = extract_frames(video_path, frame_indices)
    cue_centre_phashes = {
        cue["cue_id"]: format(compute_phash(frames[frame_centre_index(cue, measured["fps"])]), "016x")
        for cue in cues
    }
    return {
        "width": measured["width"],
        "height": measured["height"],
        "fps": measured["fps"],
        "frame_count": measured["frame_count"],
        "duration_seconds": measured["duration_seconds"],
        "cue_centre_phashes": cue_centre_phashes,
    }


def compare_to_fixture(
    video_path: Path, cues: list[dict], fixture: dict,
    *, phash_threshold: int = DEFAULT_PHASH_HAMMING_THRESHOLD,
) -> dict:
    """Compare `video_path` against `fixture` (as produced by
    `compute_fixture`), returning a clear pass/fail diagnostic per metric
    (Tasks & Acceptance) rather than a single opaque boolean.
    """
    measured = measure_video(video_path)
    checks: list[dict] = []

    def add(metric: str, expected: object, actual: object, passed: bool) -> None:
        checks.append({"metric": metric, "expected": expected, "actual": actual, "passed": passed})

    add("width", fixture["width"], measured["width"], fixture["width"] == measured["width"])
    add("height", fixture["height"], measured["height"], fixture["height"] == measured["height"])
    add("fps", fixture["fps"], measured["fps"], fixture["fps"] == measured["fps"])
    add(
        "frame_count", fixture["frame_count"], measured["frame_count"],
        fixture["frame_count"] == measured["frame_count"],
    )
    add(
        "duration_seconds", fixture["duration_seconds"], measured["duration_seconds"],
        fixture["duration_seconds"] == measured["duration_seconds"],
    )
    add("audio_present", True, measured["audio_present"], measured["audio_present"] is True)
    if measured["audio_present"]:
        audio_gap = abs(measured["audio_duration_seconds"] - measured["duration_seconds"])
        add(
            "audio_duration_matches_video",
            f"<= {AUDIO_DURATION_TOLERANCE_SECONDS}s of {measured['duration_seconds']}",
            measured["audio_duration_seconds"],
            audio_gap <= AUDIO_DURATION_TOLERANCE_SECONDS,
        )

    frame_indices = [frame_centre_index(cue, measured["fps"]) for cue in cues]
    frames = extract_frames(video_path, frame_indices)
    cue_centre_phashes = fixture.get("cue_centre_phashes", {})
    for cue in cues:
        index = frame_centre_index(cue, measured["fps"])
        actual_hash = compute_phash(frames[index])
        expected_hash_hex = cue_centre_phashes.get(cue["cue_id"])
        if expected_hash_hex is None:
            raise ValueError(
                f"Fixture has no cue_centre_phashes entry for cue id {cue['cue_id']!r} -- "
                f"known cue ids in fixture: {sorted(cue_centre_phashes)}."
            )
        distance = hamming_distance(actual_hash, int(expected_hash_hex, 16))
        checks.append({
            "metric": f"phash[{cue['cue_id']}]@frame_{index}",
            "expected": expected_hash_hex,
            "actual": format(actual_hash, "016x"),
            "hamming_distance": distance,
            "passed": distance <= phash_threshold,
        })

    return {"passed": all(check["passed"] for check in checks), "checks": checks}


@tool(
    "run_regression_harness",
    "Compare a rendered reel's video against the committed reference-reel fixture",
    {"video_path": str, "cues": list, "fixture_path": str},
)
async def run_regression_harness(args: dict) -> dict:
    fixture_path = Path(args["fixture_path"])
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Regression fixture not found: {fixture_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Regression fixture {fixture_path} is not valid JSON: {exc}") from exc
    result = compare_to_fixture(Path(args["video_path"]), args["cues"], fixture)
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


regression_harness_server = create_sdk_mcp_server(
    name="regression_harness", tools=[run_regression_harness],
)
