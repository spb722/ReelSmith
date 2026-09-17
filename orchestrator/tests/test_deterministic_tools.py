from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from PIL import Image

import orchestrator.tools.deterministic_tools as deterministic_tools_module
from orchestrator.tools.deterministic_tools import (
    build_subtitle_cues, deterministic_server, discover_images, extract_word_timing, ingest, inspect_image,
    sha256_file,
)


def test_stable_hash_ids_and_original_metadata(tmp_path):
    source = tmp_path / "one.PNG"
    renamed = tmp_path / "renamed.PNG"
    Image.new("RGB", (20, 40), "red").save(source)
    renamed.write_bytes(source.read_bytes())
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert sha256_file(source) == expected_hash
    original, copy = inspect_image(source), inspect_image(renamed)
    assert original["asset_id"] == copy["asset_id"] == f"img_{expected_hash[:12]}"
    assert original["image"] == {
        "format": "PNG", "width": 20, "height": 40, "orientation": "portrait",
        "mode": "RGB", "aspect_ratio": 0.5,
    }
    assert copy["original_filename"] == "renamed.PNG"
    Image.new("RGB", (20, 40), "blue").save(source)
    assert inspect_image(source)["asset_id"] != original["asset_id"]


def test_discovery_and_sdk_tool_write_registry_recursively(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sources"
    (source / "nested").mkdir(parents=True)
    Image.new("RGB", (10, 20)).save(source / "z.PNG")
    Image.new("RGB", (20, 10)).save(source / "nested" / "a.jpg")
    (source / "ignore.txt").write_text("not an image")
    assert discover_images(source) == [source / "nested" / "a.jpg", source / "z.PNG"]
    response = asyncio.run(ingest.handler({"source_images_dir": str(source)}))
    manifest = json.loads(response["content"][0]["text"])
    assert manifest == json.loads((tmp_path / "metadata/assets.json").read_text())
    assert manifest["asset_count"] == 2
    assert all(a["semantic_analysis"] is None for a in manifest["assets"])
    assert deterministic_server["name"] == "deterministic"


@pytest.mark.parametrize("kind", ["missing", "empty", "corrupt"])
def test_bad_input_does_not_publish_a_registry(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sources"
    if kind != "missing":
        source.mkdir()
    if kind == "corrupt":
        (source / "invalid.png").write_text("not an image")
    with pytest.raises((ValueError, OSError)):
        asyncio.run(ingest.handler({"source_images_dir": str(source)}))
    assert not (tmp_path / "metadata/assets.json").exists()


# ============================================================
# extract_word_timing / build_subtitle_cues -- exercise the real ported
# alignment/segmentation algorithm directly (mirroring `ingest`'s own
# direct-`.handler()`-call style above), not just via a wholesale mock at
# the tool boundary (which is all `test_voice_agent.py` does).
# ============================================================

NARRATION = "Don't give up now. What comes next matters most."
TOKENS = ["Don't", "give", "up", "now", "What", "comes", "next", "matters", "most"]


def timed_words(tokens: list[str]) -> list[dict]:
    return [
        {"index": i + 1, "canonical_word": w, "start_seconds": round(i * 0.4, 3), "end_seconds": round(i * 0.4 + 0.3, 3)}
        for i, w in enumerate(tokens)
    ]


def fake_stt_response(word_triples: list[tuple[str, float, float]]) -> dict:
    return {
        "results": [{
            "alternatives": [{
                "transcript": " ".join(w for w, _, _ in word_triples),
                "words": [
                    {"word": w, "startOffset": f"{s:.3f}s", "endOffset": f"{e:.3f}s"}
                    for w, s, e in word_triples
                ],
            }],
        }],
    }


def test_extract_word_timing_passing_alignment_run(tmp_path, monkeypatch):
    audio_path = tmp_path / "narration.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    word_triples = [(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(TOKENS)]

    def fake_transcribe(audio_bytes, project_id):
        assert audio_bytes == b"fake-audio-bytes"
        assert project_id == "test-project"
        return fake_stt_response(word_triples)

    monkeypatch.setattr(deterministic_tools_module, "transcribe_with_word_offsets", fake_transcribe)

    response = asyncio.run(extract_word_timing.handler({
        "audio_path": str(audio_path), "narration_script": NARRATION, "project_id": "test-project",
    }))
    payload = json.loads(response["content"][0]["text"])
    assert payload["alignment_stats"]["exact_match_ratio"] == 1.0
    assert len(payload["words"]) == len(TOKENS)
    assert payload["words"][0]["canonical_word"] == "Don't"


def test_extract_word_timing_gate_raises_below_threshold(tmp_path, monkeypatch):
    audio_path = tmp_path / "narration.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    # Every recognized word is unrelated to the canonical narration.
    word_triples = [(f"xyz{i}", i * 0.4, i * 0.4 + 0.3) for i in range(len(TOKENS))]
    monkeypatch.setattr(
        deterministic_tools_module, "transcribe_with_word_offsets",
        lambda audio_bytes, project_id: fake_stt_response(word_triples),
    )

    with pytest.raises(RuntimeError, match="ASR alignment quality is too low"):
        asyncio.run(extract_word_timing.handler({
            "audio_path": str(audio_path), "narration_script": NARRATION, "project_id": "test-project",
        }))


def test_build_subtitle_cues_recheck_gate_raises_below_threshold():
    word_timing = {
        "locked_narration": NARRATION,
        "alignment_stats": {"exact_match_ratio": 0.90},
        "words": timed_words(TOKENS),
    }
    with pytest.raises(RuntimeError, match="alignment is not accurate enough"):
        asyncio.run(build_subtitle_cues.handler({
            "word_timing": word_timing, "narration_script": NARRATION, "voice_direction": {},
            "scenes": [], "viewer_reflection": "",
        }))


def test_build_subtitle_cues_derives_impact_and_reflection_from_story_plan_data():
    """Impact/reflection phrases come from the current FinalStoryPlanContract's
    data (a scene's `impact_text`, `story_arc.viewer_reflection`), not a
    hardcoded literal (Renegotiated, 2026-09-17)."""
    word_timing = {
        "locked_narration": NARRATION,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": timed_words(TOKENS),
    }
    response = asyncio.run(build_subtitle_cues.handler({
        "word_timing": word_timing,
        "narration_script": NARRATION,
        "voice_direction": {},
        "scenes": [{"impact_text": "Don't give up now"}],
        "viewer_reflection": "What comes next matters most",
    }))
    payload = json.loads(response["content"][0]["text"])
    assert payload["cue_count"] == 2
    cues = payload["cues"]
    assert cues[0]["style_hint"] == "IMPACT"
    assert cues[0]["text"] == "Don't give up now."
    assert cues[1]["style_hint"] == "REFLECTION"
    assert cues[1]["text"] == "What comes next matters most."


def test_build_subtitle_cues_skips_impact_requirement_when_no_scene_declares_one():
    """No scene declares a non-empty impact_text this run -> the IMPACT-cue
    requirement is skipped entirely rather than always failing."""
    word_timing = {
        "locked_narration": NARRATION,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": timed_words(TOKENS),
    }
    response = asyncio.run(build_subtitle_cues.handler({
        "word_timing": word_timing,
        "narration_script": NARRATION,
        "voice_direction": {},
        "scenes": [{"impact_text": ""}],
        "viewer_reflection": "",
    }))
    payload = json.loads(response["content"][0]["text"])
    assert payload["cue_count"] == 2
    assert all(cue["style_hint"] not in {"IMPACT", "REFLECTION"} for cue in payload["cues"])
