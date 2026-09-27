"""The renderer must survive any cue timing the voice stage can produce.

A 0.6s IMPACT cue ("just to start") built the range [4, 12, 12, 18], which
Remotion's interpolate() rejects, and the whole finished reel failed at its
last step. This renders the awkward timings into a throwaway public dir, so
the real remotion/public data is never touched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image

REMOTION_DIR = Path(__file__).resolve().parents[2] / "remotion"

pytestmark = pytest.mark.skipif(
    shutil.which("npx") is None or not (REMOTION_DIR / "node_modules").is_dir(),
    reason="needs npx and remotion/node_modules",
)


def _word(index: int, word: str, start: float, end: float) -> dict:
    return {"index": index, "word": word, "start_seconds": start, "end_seconds": end}


def _cue(cue_id: str, start: float, end: float, style: str, words: list[dict]) -> dict:
    return {
        "cue_id": cue_id, "start_seconds": start, "end_seconds": end, "style_hint": style,
        "text": " ".join(word["word"] for word in words), "word_count": len(words), "words": words,
    }


def _public_dir(root: Path) -> Path:
    public = root / "public"
    (public / "data").mkdir(parents=True)
    (public / "stills").mkdir()
    (public / "audio").mkdir()
    Image.new("RGB", (1080, 1920), (40, 60, 90)).save(public / "stills" / "shot_01.png")
    with wave.open(str(public / "audio" / "narration.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 24000 * 3)
    timeline = {"shots": [{
        "sequence": 1, "type": "still", "src": "stills/shot_01.png", "start_seconds": 0.0,
        "end_seconds": 3.0, "primary_subtitle_cue_ids": [], "fade_in_frames": 0, "fade_out_frames": 0,
        "still_motion": {"scale_from": 1.0, "scale_to": 1.05},
    }]}
    cues = [
        # 18 frames: the cue that crashed a real render
        _cue("cue_001", 0.0, 0.6, "IMPACT", [_word(0, "just", 0.0, 0.2), _word(1, "to", 0.2, 0.35),
                                             _word(2, "start", 0.35, 0.6)]),
        # a single frame
        _cue("cue_002", 0.7, 0.733, "IMPACT", [_word(0, "blink", 0.7, 0.733)]),
        # a word shorter than a frame: its start and end round to one frame
        _cue("cue_003", 1.0, 2.0, "IMPACT", [_word(0, "a", 1.0, 1.005), _word(1, "longer", 1.005, 2.0)]),
        _cue("cue_004", 2.1, 2.2, "NORMAL", [_word(0, "But", 2.1, 2.2)]),
    ]
    (public / "data" / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    (public / "data" / "subtitle_cues.json").write_text(json.dumps({"cues": cues}), encoding="utf-8")
    return public


def test_short_and_zero_length_cues_render(tmp_path):
    public = _public_dir(tmp_path)
    output = tmp_path / "edge.mp4"
    completed = subprocess.run(
        ["npx", "remotion", "render", "BookReel", str(output), f"--public-dir={public}", "--log=error"],
        cwd=REMOTION_DIR, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stderr[-2000:] or completed.stdout[-2000:]
    assert output.is_file() and output.stat().st_size > 0
