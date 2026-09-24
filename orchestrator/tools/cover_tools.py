"""Deterministic helpers for cover generation: frames and the grid crop.

Codex draws the finished cover, headline included -- see `build_cover_prompt`
in `orchestrator/cover.py`. An earlier version had Codex draw a text-free plate
and composited the type with Pillow, on the reasoning that image models
misspell. Tried side by side in `test/try_cover.py`, the composited version
looked like type dropped on a picture and the Codex-designed version looked
like a designed cover, with the headline spelled correctly every time. The
compositing was removed rather than kept as a dead alternative.

What stays here is what has a correct answer: pulling a frame out of a clip,
and working out what Instagram's profile grid keeps.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from orchestrator.contracts.cover import GRID_CROP_ASPECT_RATIO
from orchestrator.progress import log
from orchestrator.tools.deterministic_tools import resolve_binary


def extract_video_frame(video_path: Path, output_path: Path, *, position_fraction: float = 0.5) -> Path:
    """Pull one frame out of a Veo clip, so video shots can back a cover too.

    Reuses the same ffmpeg resolution as Veo's QA previews, which already
    guards against the broken Anaconda ffmpeg that shadows Homebrew's.
    """

    ffmpeg = resolve_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = _video_duration_seconds(video_path)
    timestamp = round(duration * position_fraction, 3)
    completed = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(timestamp), "-i", str(video_path),
            "-frames:v", "1", str(output_path),
        ],
        capture_output=True, text=True,
    )
    if completed.returncode != 0 or not output_path.is_file():
        raise RuntimeError(
            f"ffmpeg could not take a frame from {video_path} at {timestamp}s: "
            f"{(completed.stderr or '').strip()[-400:]}"
        )
    return output_path


def _video_duration_seconds(video_path: Path) -> float:
    ffprobe = resolve_binary("ffprobe")
    completed = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float((completed.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe gave no duration for {video_path}") from exc


def grid_crop_box(width: int, height: int) -> tuple[int, int]:
    """(top, height) of the portrait slice the profile grid keeps."""

    crop_height = min(height, int(width / GRID_CROP_ASPECT_RATIO))
    return max(0, (height - crop_height) // 2), crop_height


def write_grid_preview(cover_path: Path, output_path: Path) -> Path:
    """What Instagram's profile grid keeps of the cover.

    The grid crops the 9:16 cover to a portrait slice, so a headline that
    reads fine full-screen can be cut off where most people first see the
    post. Written so that is visible before posting, not after.
    """

    cover = Image.open(cover_path).convert("RGB")
    top, crop_height = grid_crop_box(cover.width, cover.height)
    preview = cover.crop((0, top, cover.width, min(cover.height, top + crop_height)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path, format="PNG", optimize=True)
    log(f"Grid preview written to {output_path} ({preview.width}x{preview.height})", indent=2)
    return output_path
