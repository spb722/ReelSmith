from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from orchestrator.tools.camera_motion import describe_camera_motion, measure_camera_motion
from orchestrator.tools.deterministic_tools import resolve_binary


def _ffmpeg() -> str:
    try:
        return resolve_binary("ffmpeg")
    except Exception:
        pytest.skip("no working ffmpeg")


def _textured_plate(path: Path) -> None:
    """A busy flat-colour plate: many shapes, like a cel-shaded background."""
    rng = np.random.default_rng(7)
    image = Image.new("RGB", (720, 1280), (236, 228, 210))
    draw = ImageDraw.Draw(image)
    for _ in range(900):
        x, y = rng.integers(0, 720), rng.integers(0, 1280)
        size = int(rng.integers(6, 40))
        colour = tuple(int(c) for c in rng.integers(0, 255, 3))
        draw.rectangle([x, y, x + size, y + size], fill=colour)
    image.save(path)


def _render(tmp_path: Path, name: str, video_filter: str) -> Path:
    plate = tmp_path / "plate.png"
    _textured_plate(plate)
    out = tmp_path / f"{name}.mp4"
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(plate),
            "-vf", video_filter, "-t", "4", "-r", "24", "-pix_fmt", "yuv420p", str(out),
        ],
        check=True,
    )
    return out


def test_a_locked_camera_with_a_moving_subject_measures_as_still(tmp_path):
    # A dark block (the "subject") walks across a fixed background.
    clip = _render(
        tmp_path, "locked",
        "scale=576:1024,drawbox=x='100+t*60':y=400:w=150:h=300:color=black@1:t=fill",
    )
    motion = measure_camera_motion(clip)
    assert motion is not None
    assert 0.97 <= motion["zoom"] <= 1.03
    assert abs(motion["pan_x_percent"]) <= 2 and abs(motion["pan_y_percent"]) <= 2


def test_a_push_in_measures_as_zoom(tmp_path):
    # 96 frames zooming 1.0 -> 1.3 about the centre.
    clip = _render(
        tmp_path, "push",
        "scale=720:1280,zoompan=z='1+0.3*on/96':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d=1:s=576x1024:fps=24",
    )
    motion = measure_camera_motion(clip)
    assert motion is not None
    assert 1.2 <= motion["zoom"] <= 1.4
    assert "zoom x1." in describe_camera_motion(motion)


def test_unmeasured_motion_says_so():
    assert "could not be measured" in describe_camera_motion(None)


def test_measure_limits_sampling_to_max_seconds(monkeypatch, tmp_path):
    from orchestrator.tools import camera_motion

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"")

    monkeypatch.setattr(camera_motion.subprocess, "run", fake_run)
    clip = tmp_path / "clip.mp4"
    ffmpeg = camera_motion.resolve_binary("ffmpeg")

    assert measure_camera_motion(clip, max_seconds=10.84) is None
    limited = calls[-1]
    at = limited.index("-i")
    assert limited[at - 2:at] == ["-t", "10.840"]

    measure_camera_motion(clip)
    assert calls[-1] == [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(clip),
        "-vf", "fps=3,scale=288:512,format=gray", "-f", "rawvideo", "-",
    ]


def test_max_seconds_measures_only_the_shown_part(tmp_path):
    # Still for the first 2 s (48 frames), then zooming 1.0 -> 1.3 by 4 s.
    clip = _render(
        tmp_path, "still_then_push",
        "scale=720:1280,zoompan=z='if(lt(on,48),1,1+0.3*(on-48)/48)'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=576x1024:fps=24",
    )
    shown = measure_camera_motion(clip, max_seconds=2)
    assert shown is not None
    assert 0.98 <= shown["zoom"] <= 1.02
    whole = measure_camera_motion(clip)
    assert whole is not None
    assert whole["zoom"] > 1.03
