"""Measure how far a generated clip's camera moved, from the pixels alone.

Three still previews cannot show camera motion reliably -- a subject walking
away and a camera dollying after it look much alike -- and Veo repeatedly
tracked forward on shots that asked for a still camera. This measures it.

Method: sample frames a third of a second apart, phase-correlate many small
patches between neighbouring frames, and fit one zoom-plus-pan to the patch
shifts while trimming the ones that disagree. A moving subject covers a
minority of patches, so it is trimmed as an outlier and what is left is the
background, i.e. the camera. Zoom and pan are then chained across the clip.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from orchestrator.tools.deterministic_tools import resolve_binary

SAMPLE_FPS = 3
WIDTH, HEIGHT = 288, 512
PATCH = 32
# A patch this flat has nothing to lock onto (flat cel-shaded walls, sky).
MIN_PATCH_CONTRAST = 6.0
MIN_PEAK = 0.15
MIN_TRACKED_PATCHES = 8
_WINDOW = np.outer(np.hanning(PATCH), np.hanning(PATCH))


def _sample_frames(video_path: Path) -> np.ndarray:
    completed = subprocess.run(
        [
            resolve_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video_path),
            "-vf", f"fps={SAMPLE_FPS},scale={WIDTH}:{HEIGHT},format=gray", "-f", "rawvideo", "-",
        ],
        capture_output=True,
        check=True,
    )
    frames = np.frombuffer(completed.stdout, np.uint8)
    return frames.reshape(-1, HEIGHT, WIDTH).astype(np.float64)


def _subpixel(values: np.ndarray, index: int) -> float:
    if index <= 0 or index >= len(values) - 1:
        return 0.0
    left, centre, right = values[index - 1], values[index], values[index + 1]
    return 0.5 * (left - right) / (left - 2 * centre + right + 1e-12)


def _patch_shift(before: np.ndarray, after: np.ndarray) -> tuple[float, float, float]:
    """(dx, dy, peak) of `after` relative to `before`, by phase correlation."""

    spectrum = np.fft.fft2((after - after.mean()) * _WINDOW) * np.conj(
        np.fft.fft2((before - before.mean()) * _WINDOW)
    )
    spectrum /= np.abs(spectrum) + 1e-9
    surface = np.fft.fftshift(np.real(np.fft.ifft2(spectrum)))
    iy, ix = np.unravel_index(np.argmax(surface), surface.shape)
    dy = iy - PATCH // 2 + _subpixel(surface[:, ix], iy)
    dx = ix - PATCH // 2 + _subpixel(surface[iy, :], ix)
    return dx, dy, float(surface[iy, ix])


def _pair_motion(before: np.ndarray, after: np.ndarray) -> tuple[float, float, float] | None:
    """(zoom, pan_x, pan_y) between two neighbouring frames, pan in pixels at frame centre."""

    points, shifts = [], []
    step = PATCH // 2
    for y in range(0, HEIGHT - PATCH + 1, step):
        for x in range(0, WIDTH - PATCH + 1, step):
            patch = before[y:y + PATCH, x:x + PATCH]
            if patch.std() < MIN_PATCH_CONTRAST:
                continue
            dx, dy, peak = _patch_shift(patch, after[y:y + PATCH, x:x + PATCH])
            if peak < MIN_PEAK or abs(dx) > PATCH / 3 or abs(dy) > PATCH / 3:
                continue
            points.append((x + PATCH / 2, y + PATCH / 2))
            shifts.append((dx, dy))
    if len(points) < MIN_TRACKED_PATCHES:
        return None
    p, d = np.array(points), np.array(shifts)
    # shift = k * position + t: a uniform zoom about any point plus a pan.
    keep = np.ones(len(p), bool)
    for _ in range(5):
        pk, dk = p[keep], d[keep]
        design = np.zeros((2 * len(pk), 3))
        design[0::2, 0], design[0::2, 1] = pk[:, 0], 1
        design[1::2, 0], design[1::2, 2] = pk[:, 1], 1
        target = np.empty(2 * len(pk))
        target[0::2], target[1::2] = dk[:, 0], dk[:, 1]
        (k, tx, ty), *_ = np.linalg.lstsq(design, target, rcond=None)
        residual = np.hypot(d[:, 0] - (k * p[:, 0] + tx), d[:, 1] - (k * p[:, 1] + ty))
        trimmed = residual < max(0.75, 2.5 * float(np.median(residual[keep])))
        if trimmed.sum() < MIN_TRACKED_PATCHES or (trimmed == keep).all():
            break
        keep = trimmed
    return 1 + k, k * WIDTH / 2 + tx, k * HEIGHT / 2 + ty


def measure_camera_motion(video_path: Path) -> dict | None:
    """How much the background grew and slid from the first frame to the last.

    `zoom` > 1 means the camera pushed in or moved forward, < 1 pulled back.
    Pans are percentages of the frame width/height. None when too little of
    the clip had texture to track, so the caller can say it is unmeasured
    rather than report a guess.
    """

    frames = _sample_frames(video_path)
    zoom, pan_x, pan_y, measured = 1.0, 0.0, 0.0, 0
    for before, after in zip(frames[:-1], frames[1:]):
        motion = _pair_motion(before, after)
        if motion is None:
            continue
        step_zoom, step_x, step_y = motion
        zoom *= step_zoom
        pan_x += step_x / zoom
        pan_y += step_y / zoom
        measured += 1
    pairs = len(frames) - 1
    if pairs < 1 or measured < pairs / 2:
        return None
    return {
        "zoom": round(float(zoom), 2),
        "pan_x_percent": int(round(100 * pan_x / WIDTH)),
        "pan_y_percent": int(round(100 * pan_y / HEIGHT)),
    }


def describe_camera_motion(motion: dict | None) -> str:
    if motion is None:
        return "Camera motion could not be measured (too little texture to track)."
    return (
        f"Measured camera motion, first frame to last: background zoom x{motion['zoom']:.2f} "
        f"(>1 = camera moved in/forward, <1 = moved back), pan {motion['pan_x_percent']:+d}% of "
        f"frame width, {motion['pan_y_percent']:+d}% of frame height."
    )
