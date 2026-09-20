from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

import orchestrator.tools.deterministic_tools as deterministic_tools_module
from orchestrator.tools.deterministic_tools import (
    ImpactPhraseNotFoundError,
    REMOTION_DIR,
    REMOTION_PUBLIC_DIR,
    build_subtitle_cues,
    deterministic_server,
    discover_images,
    extract_word_timing,
    ingest,
    inspect_image,
    render_remotion,
    sha256_file,
    sync_remotion_assets,
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
    # Short valid PCM WAV so the new chunking path can open it.
    import wave as _wave
    with _wave.open(str(audio_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * 2400)  # 0.1s

    word_triples = [(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(TOKENS)]

    def fake_transcribe(audio_bytes, project_id):
        assert project_id == "test-project"
        assert len(audio_bytes) > 44  # WAV header + PCM
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
    import wave as _wave
    with _wave.open(str(audio_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * 2400)

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


def test_extract_word_timing_splits_long_wav_and_offsets_chunk_times(tmp_path, monkeypatch):
    """Audio longer than STT's sync limit is chunked; later chunk times shift."""
    audio_path = tmp_path / "long.wav"
    import wave as _wave
    rate = 24000
    # 70 seconds of silence -> two chunks at 55s max.
    with _wave.open(str(audio_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * (rate * 70))

    calls: list[bytes] = []

    def fake_transcribe(audio_bytes, project_id):
        calls.append(audio_bytes)
        # First chunk returns early words; second returns later words with
        # chunk-local times that must be offset by ~55s.
        if len(calls) == 1:
            triples = [(w, float(i), float(i) + 0.5) for i, w in enumerate(TOKENS[:5])]
        else:
            triples = [(w, float(i), float(i) + 0.5) for i, w in enumerate(TOKENS[5:])]
        return fake_stt_response(triples)

    monkeypatch.setattr(deterministic_tools_module, "transcribe_with_word_offsets", fake_transcribe)

    response = asyncio.run(extract_word_timing.handler({
        "audio_path": str(audio_path), "narration_script": NARRATION, "project_id": "test-project",
    }))
    payload = json.loads(response["content"][0]["text"])
    assert len(calls) == 2
    assert payload["alignment_stats"]["exact_match_ratio"] == 1.0
    # Second chunk's first word ("comes") was local t=0 -> global ~55s.
    comes = next(w for w in payload["words"] if w["canonical_word"] == "comes")
    assert comes["start_seconds"] >= 54.0


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


def test_build_subtitle_cues_raises_distinct_error_when_impact_text_not_in_narration():
    """AD-12: a declared impact_text that doesn't appear anywhere in the
    narration is a text-authoring mismatch, not an alignment-quality
    failure -- it must raise a distinctly identifiable error so the caller
    can treat it as non-recoverable rather than retry it."""
    word_timing = {
        "locked_narration": NARRATION,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": timed_words(TOKENS),
    }
    with pytest.raises(ImpactPhraseNotFoundError):
        asyncio.run(build_subtitle_cues.handler({
            "word_timing": word_timing,
            "narration_script": NARRATION,
            "voice_direction": {},
            "scenes": [{"impact_text": "this phrase is nowhere in the narration"}],
            "viewer_reflection": "",
        }))


def test_sync_remotion_assets_copies_public_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    narration = tmp_path / "audio/narration.wav"
    narration.parent.mkdir(parents=True)
    narration.write_bytes(b"wav")
    cues = tmp_path / "metadata/subtitle_cues.json"
    cues.parent.mkdir(parents=True)
    cues.write_text('{"cues": []}', encoding="utf-8")
    timeline = tmp_path / "timeline.json"
    timeline.write_text('{"shots": []}', encoding="utf-8")
    still = tmp_path / "generated/stills/shot_01.png"
    still.parent.mkdir(parents=True)
    still.write_bytes(b"png")

    response = asyncio.run(sync_remotion_assets.handler({
        "narration_path": str(narration),
        "subtitle_cues_path": str(cues),
        "timeline_path": str(timeline),
        "shot_asset_sources": {"1": str(still)},
    }))
    payload = json.loads(response["content"][0]["text"])
    assert (REMOTION_PUBLIC_DIR / "audio/narration.wav").read_bytes() == b"wav"
    assert (REMOTION_PUBLIC_DIR / "data/subtitle_cues.json").exists()
    assert (REMOTION_PUBLIC_DIR / "data/timeline.json").exists()
    assert (REMOTION_PUBLIC_DIR / "stills/shot_01.png").read_bytes() == b"png"
    assert payload["shot_assets"]["1"] == "stills/shot_01.png"


def test_sync_remotion_assets_same_path_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    public_timeline = REMOTION_PUBLIC_DIR / "data/timeline.json"
    public_timeline.parent.mkdir(parents=True)
    public_timeline.write_text('{"shots": []}', encoding="utf-8")
    narration = tmp_path / "audio/narration.wav"
    narration.parent.mkdir(parents=True)
    narration.write_bytes(b"wav")
    cues = tmp_path / "metadata/subtitle_cues.json"
    cues.parent.mkdir(parents=True)
    cues.write_text('{"cues": []}', encoding="utf-8")
    still = tmp_path / "generated/stills/shot_01.png"
    still.parent.mkdir(parents=True)
    still.write_bytes(b"png")

    response = asyncio.run(sync_remotion_assets.handler({
        "narration_path": str(narration),
        "subtitle_cues_path": str(cues),
        "timeline_path": str(public_timeline),
        "shot_asset_sources": {"1": str(still)},
    }))
    payload = json.loads(response["content"][0]["text"])
    assert payload["copied"]["data/timeline.json"] == "data/timeline.json"
    assert public_timeline.read_text(encoding="utf-8") == '{"shots": []}'


def test_render_remotion_success_uses_absolute_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remotion = REMOTION_DIR
    remotion.mkdir(parents=True)
    (remotion / "package.json").write_text("{}", encoding="utf-8")
    (remotion / "node_modules").mkdir()
    out = tmp_path / "remotion/out/book_reel.mp4"

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == remotion
        assert command[-1] == str(out.resolve())
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"mp4")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(deterministic_tools_module.subprocess, "run", fake_run)
    monkeypatch.setattr(deterministic_tools_module.shutil, "which", lambda _: "/usr/bin/npx")

    response = asyncio.run(render_remotion.handler({"output_path": str(out)}))
    payload = json.loads(response["content"][0]["text"])
    assert Path(payload["output_path"]) == out.resolve()
    assert out.is_file()


def test_render_remotion_failure_raises_runtime_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remotion = REMOTION_DIR
    remotion.mkdir(parents=True)
    (remotion / "package.json").write_text("{}", encoding="utf-8")
    (remotion / "node_modules").mkdir()

    def fake_run(command, **kwargs):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "render failed"})()

    monkeypatch.setattr(deterministic_tools_module.subprocess, "run", fake_run)
    monkeypatch.setattr(deterministic_tools_module.shutil, "which", lambda _: "/usr/bin/npx")

    with pytest.raises(RuntimeError, match="Remotion render failed"):
        asyncio.run(render_remotion.handler({"output_path": str(out := tmp_path / "out.mp4")}))


def test_build_subtitle_cues_promotes_every_declared_impact_text():
    """Every scene's `impact_text` is a line the story agent deliberately
    marked as worth landing on screen, so each one earns its own IMPACT cue --
    not only the first, which is all `derive_impact_phrase` used to return.
    """
    word_timing = {
        "locked_narration": NARRATION,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": timed_words(TOKENS),
    }
    response = asyncio.run(build_subtitle_cues.handler({
        "word_timing": word_timing,
        "narration_script": NARRATION,
        "voice_direction": {},
        "scenes": [
            {"impact_text": "Don't give up now"},
            {"impact_text": "What comes next matters most"},
        ],
        "viewer_reflection": "",
    }))
    payload = json.loads(response["content"][0]["text"])
    impact = [cue for cue in payload["cues"] if cue["style_hint"] == "IMPACT"]
    assert [cue["text"] for cue in impact] == [
        "Don't give up now.", "What comes next matters most.",
    ]


def test_build_subtitle_cues_drops_an_impact_phrase_overlapping_an_earlier_one():
    """`find_phrase_range` only ever reports a phrase's first occurrence, so
    two declared phrases can resolve onto the same words. The later one is
    dropped rather than raising -- it renders as an ordinary cue instead of
    failing the whole run.
    """
    word_timing = {
        "locked_narration": NARRATION,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": timed_words(TOKENS),
    }
    response = asyncio.run(build_subtitle_cues.handler({
        "word_timing": word_timing,
        "narration_script": NARRATION,
        "voice_direction": {},
        "scenes": [
            {"impact_text": "Don't give up now"},
            {"impact_text": "give up"},
        ],
        "viewer_reflection": "",
    }))
    payload = json.loads(response["content"][0]["text"])
    impact = [cue for cue in payload["cues"] if cue["style_hint"] == "IMPACT"]
    assert [cue["text"] for cue in impact] == ["Don't give up now."]


def test_build_subtitle_cues_deduplicates_a_repeated_impact_text():
    """Two scenes declaring the same phrase must not demand two separate cues
    for one stretch of narration.
    """
    word_timing = {
        "locked_narration": NARRATION,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": timed_words(TOKENS),
    }
    response = asyncio.run(build_subtitle_cues.handler({
        "word_timing": word_timing,
        "narration_script": NARRATION,
        "voice_direction": {},
        "scenes": [
            {"impact_text": "Don't give up now"},
            {"impact_text": "Don't give up now"},
        ],
        "viewer_reflection": "",
    }))
    payload = json.loads(response["content"][0]["text"])
    impact = [cue for cue in payload["cues"] if cue["style_hint"] == "IMPACT"]
    assert [cue["text"] for cue in impact] == ["Don't give up now."]


def test_build_subtitle_cues_allows_an_impact_phrase_longer_than_one_cue():
    """A declared `impact_text` may exceed MAX_WORDS_PER_CUE (6), in which case
    the segmenter cannot give it a single cue of its own. This reel's own
    "Few want the struggle that earns it" is 7 words, so requiring exact
    single-cue isolation would hard-fail the voice stage. The phrase instead
    tiles across consecutive IMPACT cues.
    """
    narration = "Few want the struggle that earns it. Everything else is noise."
    tokens = [
        "Few", "want", "the", "struggle", "that", "earns", "it",
        "Everything", "else", "is", "noise",
    ]
    word_timing = {
        "locked_narration": narration,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": timed_words(tokens),
    }
    response = asyncio.run(build_subtitle_cues.handler({
        "word_timing": word_timing,
        "narration_script": narration,
        "voice_direction": {},
        "scenes": [{"impact_text": "Few want the struggle that earns it"}],
        "viewer_reflection": "",
    }))
    payload = json.loads(response["content"][0]["text"])
    impact = [cue for cue in payload["cues"] if cue["style_hint"] == "IMPACT"]
    assert len(impact) >= 2, "a 7-word phrase must tile across consecutive cues"
    joined = " ".join(word["word"] for cue in impact for word in cue["words"])
    assert joined == "Few want the struggle that earns it"
