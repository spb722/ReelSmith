from __future__ import annotations

import json

import pytest

import orchestrator.run as run
from orchestrator.contracts.subtitle_cues import SubtitleCuesContract
from orchestrator.preflight import PreflightResult
from orchestrator.state.run_manifest import RunManifest, load_run_manifest, save_run_manifest
from orchestrator.tests.test_preflight import make_settings

NARRATION_SCRIPT = " ".join(f"word{i}" for i in range(100))
AUDIO_DURATION_SECONDS = 45.0


def final_story_plan_for(narration_script: str) -> dict:
    return {
        "produced_by": "story_agent",
        "story_title": "The Backwards Law",
        "core_thesis": "Acceptance beats striving.",
        "narrative_strategy": "Hook, human story, reveal, explanation, reflection.",
        "target_duration_seconds": 45.0,
        "target_word_count": 100,
        "hook": narration_script,
        "narration_script": narration_script,
        "voice_direction": {
            "persona": "Mature, thoughtful guide", "tone": ["calm"], "pace_wpm": 130,
            "delivery_notes": [], "emphasis_phrases": [], "pause_after_phrases": [],
        },
        "story_arc": {
            "opening_tension": "t", "human_or_concrete_story": "s",
            "reversal_or_reveal": "r", "principle_explanation": "p", "viewer_reflection": "v",
        },
        "scenes": [{
            "sequence": 1,
            "role": "SETUP",
            "estimated_duration_seconds": 45.0,
            "narration": narration_script,
            "source_asset_ids": ["img_aaaaaaaaaaaa"],
            "source_support": "Directly supports this beat.",
            "visual_intent": "A calm visual.",
            "suggested_visual_treatment": "USE_EXISTING_ART",
            "impact_text": "",
            "emotional_goal": "calm",
        }],
        "unused_assets": [],
        "source_integrity_notes": [],
        "quality_review": {
            "verdict": "APPROVE", "ready_for_voice_generation": True, "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "hook_strength": 9, "spoken_naturalness": 9,
                "narrative_coherence": 9, "voice_alignment": 9, "pacing": 9,
                "scene_structure": 9, "visual_support": 9, "internal_consistency": 9,
            },
        },
    }


def visual_plan_for() -> dict:
    return {
        "produced_by": "visual_agent",
        "overall_visual_style": "Reflective, calm, textured halftone illustrations.",
        "shots": [{
            "sequence": 1,
            "generation_mode": "STILL",
            "visual_treatment": "USE_EXISTING_ART",
            "source_asset_ids": ["img_aaaaaaaaaaaa"],
            "shot_goal": "Establish the beat.",
            "frame_composition": "Center the subject.",
            "motion_plan": "Slow push-in.",
            "text_overlay": "",
            "source_support": "Directly grounded in the cited asset.",
        }],
        "quality_review": {
            "verdict": "APPROVE", "ready_for_generation": True, "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "generation_mode_appropriateness": 9,
                "visual_coherence": 9, "narrative_alignment": 9, "internal_consistency": 9,
            },
        },
    }


def tts_content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


PROMPT_WORD_COUNT = 250  # stands in for build_tts_prompt's real (much larger) prompt


def tts_ok_payload(duration_seconds: float = AUDIO_DURATION_SECONDS, prompt_word_count: int = PROMPT_WORD_COUNT) -> dict:
    return tts_content(json.dumps({
        "audio_path": "audio/narration.wav", "duration_seconds": duration_seconds,
        "model": "gemini-3.1-flash-tts-preview", "voice_name": "Gacrux",
        "prompt_word_count": prompt_word_count,
    }))


def stt_ok_payload(narration_script: str = NARRATION_SCRIPT) -> dict:
    words = [
        {"index": i + 1, "canonical_word": word, "start_seconds": i * 0.4, "end_seconds": i * 0.4 + 0.3}
        for i, word in enumerate(narration_script.split(" "))
    ]
    return tts_content(json.dumps({
        "locked_narration": narration_script,
        "recognized_transcript": narration_script,
        "alignment_stats": {"exact_match_ratio": 0.99},
        "words": words,
    }))


def dp_ok_payload(narration_script: str = NARRATION_SCRIPT, alignment_ratio: float = 0.99) -> dict:
    tokens = narration_script.split(" ")
    cues = []
    for start in range(0, len(tokens), 5):
        chunk = tokens[start:start + 5]
        cue_words = [
            {"index": start + i + 1, "word": word, "start_seconds": (start + i) * 0.4, "end_seconds": (start + i) * 0.4 + 0.3}
            for i, word in enumerate(chunk)
        ]
        cues.append({
            "cue_id": f"cue_{len(cues) + 1:03d}",
            "start_seconds": cue_words[0]["start_seconds"],
            "end_seconds": cue_words[-1]["end_seconds"],
            "duration_seconds": cue_words[-1]["end_seconds"] - cue_words[0]["start_seconds"],
            "start_word_index": cue_words[0]["index"],
            "end_word_index": cue_words[-1]["index"],
            "word_count": len(chunk),
            "text": " ".join(chunk),
            "style_hint": "NORMAL",
            "duration_warning": False,
            "words": cue_words,
        })
    return tts_content(json.dumps({
        "alignment_ratio": alignment_ratio, "cue_count": len(cues), "cues": cues,
    }))


class FakeTool:
    """Stand-in for an `@tool`-decorated `SdkMcpTool`: only `.handler` is
    ever called by `run_voice_pipeline`, mirroring how `run_screenshot_stage`
    calls `ingest.handler` directly (no Claude session anywhere, AD-1).
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def handler(self, args: dict) -> dict:
        self.calls.append(args)
        item = self._responses[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run, "load_settings", lambda: make_settings())

    def preflight(**kwargs):
        return PreflightResult(passed=True)

    monkeypatch.setattr(run, "run_preflight", preflight)

    async def no_screenshot_stage(source_images_dir, settings, manifest):
        return 0

    async def no_narration_stage(settings, manifest):
        return 0

    async def no_visual_stage(settings, manifest):
        return 0

    monkeypatch.setattr(run, "run_screenshot_stage", no_screenshot_stage)
    monkeypatch.setattr(run, "run_narration_stage", no_narration_stage)
    monkeypatch.setattr(run, "run_visual_stage", no_visual_stage)

    story_plan = final_story_plan_for(NARRATION_SCRIPT)
    run.FINAL_STORY_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    run.FINAL_STORY_PLAN_FILE.write_text(json.dumps(story_plan), encoding="utf-8")
    run.VISUAL_PLAN_FILE.write_text(json.dumps(visual_plan_for()), encoding="utf-8")

    source = tmp_path / "source_images"
    source.mkdir()
    return source


def mock_tools(monkeypatch, *, tts=None, stt=None, dp=None):
    tts_tool = FakeTool(tts if tts is not None else [tts_ok_payload()])
    stt_tool = FakeTool(stt if stt is not None else [stt_ok_payload()])
    dp_tool = FakeTool(dp if dp is not None else [dp_ok_payload()])
    monkeypatch.setattr(run, "generate_narration_audio", tts_tool)
    monkeypatch.setattr(run, "extract_word_timing", stt_tool)
    monkeypatch.setattr(run, "build_subtitle_cues", dp_tool)
    return tts_tool, stt_tool, dp_tool


def failure_report():
    paths = list(run.RUN_STATE_DIR.glob("failure_voice_agent_*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def expected_cost(duration_seconds: float = AUDIO_DURATION_SECONDS, prompt_word_count: int = PROMPT_WORD_COUNT) -> float:
    from orchestrator.settings import (
        GEMINI_TTS_AUDIO_TOKENS_PER_SECOND,
        GEMINI_TTS_INPUT_TOKEN_COST_USD,
        GEMINI_TTS_OUTPUT_TOKEN_COST_USD,
        STT_COST_PER_SECOND_USD,
    )
    tts_input = prompt_word_count * GEMINI_TTS_INPUT_TOKEN_COST_USD
    tts_output = duration_seconds * GEMINI_TTS_AUDIO_TOKENS_PER_SECOND * GEMINI_TTS_OUTPUT_TOKEN_COST_USD
    stt = duration_seconds * STT_COST_PER_SECOND_USD
    return round(tts_input + tts_output + stt, 6)


def test_fresh_run_runs_tools_in_sequence_and_persists_validated_contract(stage, monkeypatch):
    source = stage
    tts_tool, stt_tool, dp_tool = mock_tools(monkeypatch)
    assert run.main([str(source)]) == 0
    assert len(tts_tool.calls) == len(stt_tool.calls) == len(dp_tool.calls) == 1
    # STT/DP each consume the previous tool's own output, chained in order.
    assert stt_tool.calls[0]["audio_path"] == "audio/narration.wav"
    assert dp_tool.calls[0]["word_timing"]["alignment_stats"]["exact_match_ratio"] == 0.99

    saved = json.loads(run.SUBTITLE_CUES_FILE.read_text())
    contract = SubtitleCuesContract.model_validate(saved)
    assert contract.produced_by == "voice_agent"
    assert contract.source_narration_script == NARRATION_SCRIPT
    assert len(contract.cues) == 20

    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == pytest.approx(expected_cost())
    assert manifest.iteration_counts == {"voice_agent": 1}


def test_valid_persisted_contract_skips_tools(stage, monkeypatch):
    source = stage
    persisted = {
        "produced_by": "voice_agent",
        "source_narration_script": NARRATION_SCRIPT,
        "alignment_ratio": 0.99,
        "cues": [{
            "cue_id": "cue_001", "start_seconds": 0.0, "end_seconds": 1.0, "word_count": 2,
            "text": "word0 word1",
            "words": [
                {"index": 1, "word": "word0", "start_seconds": 0.0, "end_seconds": 0.3},
                {"index": 2, "word": "word1", "start_seconds": 0.4, "end_seconds": 0.7},
            ],
        }],
    }
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(persisted), encoding="utf-8")
    before = run.SUBTITLE_CUES_FILE.read_bytes()
    tts_tool, stt_tool, dp_tool = mock_tools(monkeypatch, tts=[], stt=[], dp=[])
    for _ in range(2):
        assert run.main([str(source)]) == 0
    assert tts_tool.calls == stt_tool.calls == dp_tool.calls == []
    assert run.SUBTITLE_CUES_FILE.read_bytes() == before


@pytest.mark.parametrize("bad_file", ["malformed", "legacy_no_produced_by", "stale_narration"])
def test_invalid_or_stale_persistence_cannot_skip(stage, monkeypatch, bad_file):
    source = stage
    persisted = {
        "produced_by": "voice_agent",
        "source_narration_script": NARRATION_SCRIPT,
        "alignment_ratio": 0.99,
        "cues": [{
            "cue_id": "cue_001", "start_seconds": 0.0, "end_seconds": 1.0, "word_count": 2,
            "text": "word0 word1",
            "words": [
                {"index": 1, "word": "word0", "start_seconds": 0.0, "end_seconds": 0.3},
                {"index": 2, "word": "word1", "start_seconds": 0.4, "end_seconds": 0.7},
            ],
        }],
    }
    if bad_file == "legacy_no_produced_by":
        # A real build_subtitle_cues.py manual-run manifest: schema-shaped
        # in the legacy sense but missing produced_by -- must never satisfy
        # resume (mirrors AnalyzedAssetsContract/FinalStoryPlanContract).
        del persisted["produced_by"]
    if bad_file == "stale_narration":
        persisted["source_narration_script"] = "a completely different narration script entirely"
    run.SUBTITLE_CUES_FILE.write_text("{" if bad_file == "malformed" else json.dumps(persisted), encoding="utf-8")
    tts_tool, stt_tool, dp_tool = mock_tools(monkeypatch)
    assert run.main([str(source)]) == 0
    assert len(tts_tool.calls) == len(stt_tool.calls) == len(dp_tool.calls) == 1


@pytest.mark.parametrize("break_it", ["missing", "malformed"])
def test_final_story_plan_precondition_prevents_voice_agent(stage, monkeypatch, break_it):
    source = stage
    if break_it == "missing":
        run.FINAL_STORY_PLAN_FILE.unlink()
    else:
        run.FINAL_STORY_PLAN_FILE.write_text("{not valid json", encoding="utf-8")
    tts_tool, stt_tool, dp_tool = mock_tools(monkeypatch)
    assert run.main([str(source)]) == 1
    assert tts_tool.calls == stt_tool.calls == dp_tool.calls == []
    report = failure_report()
    assert report["failed_contract_name"] == "FinalStoryPlanContract"


@pytest.mark.parametrize("break_it", ["missing", "malformed"])
def test_visual_plan_precondition_prevents_voice_agent(stage, monkeypatch, break_it):
    source = stage
    if break_it == "missing":
        run.VISUAL_PLAN_FILE.unlink()
    else:
        run.VISUAL_PLAN_FILE.write_text("{not valid json", encoding="utf-8")
    tts_tool, stt_tool, dp_tool = mock_tools(monkeypatch)
    assert run.main([str(source)]) == 1
    assert tts_tool.calls == stt_tool.calls == dp_tool.calls == []
    report = failure_report()
    assert report["failed_contract_name"] == "VisualPlanContract"


def test_stt_alignment_gate_failure_triggers_retry_then_succeeds(stage, monkeypatch):
    source = stage
    gate_failure = RuntimeError(
        "ASR alignment quality is too low to trust automatically.\nExact match ratio: 0.500"
    )
    tts_tool, stt_tool, dp_tool = mock_tools(
        monkeypatch,
        tts=[tts_ok_payload(), tts_ok_payload()],
        stt=[gate_failure, stt_ok_payload()],
        dp=[dp_ok_payload()],
    )
    assert run.main([str(source)]) == 0
    assert len(tts_tool.calls) == 2
    assert len(stt_tool.calls) == 2
    assert len(dp_tool.calls) == 1
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.iteration_counts["voice_agent"] == 2


def test_dp_recheck_failure_triggers_retry_then_succeeds(stage, monkeypatch):
    source = stage
    recheck_failure = RuntimeError("word_timing alignment is not accurate enough.\nExact match ratio: 0.960")
    tts_tool, stt_tool, dp_tool = mock_tools(
        monkeypatch,
        tts=[tts_ok_payload(), tts_ok_payload()],
        stt=[stt_ok_payload(), stt_ok_payload()],
        dp=[recheck_failure, dp_ok_payload()],
    )
    assert run.main([str(source)]) == 0
    assert len(dp_tool.calls) == 2
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.iteration_counts["voice_agent"] == 2


def test_retry_ceiling_exhausted_halts_with_shared_failure_report(stage, monkeypatch):
    source = stage
    always_fails = RuntimeError("transient TTS failure")
    tts_tool, stt_tool, dp_tool = mock_tools(monkeypatch, tts=[always_fails] * 3, stt=[], dp=[])
    assert run.main([str(source)]) == 1
    assert len(tts_tool.calls) == 3
    assert stt_tool.calls == dp_tool.calls == []
    report = failure_report()
    assert report["stage_id"] == "voice_agent"
    assert report["failed_contract_name"] == "SubtitleCuesContract"
    assert report["attempt_count"] == 3
    assert report["timestamp"]
    assert "metadata/final_story_plan.json" in report["partial_artifact_paths"]
    assert "metadata/visual_plan.json" in report["partial_artifact_paths"]
    assert not run.SUBTITLE_CUES_FILE.exists()
    # The ceiling is persisted; restarting cannot purchase another three attempts.
    assert run.main([str(source)]) == 1
    assert len(tts_tool.calls) == 3


def test_budget_exhausted_at_stage_halts_before_tools(stage, monkeypatch):
    source = stage
    tts_tool, stt_tool, dp_tool = mock_tools(monkeypatch, tts=[], stt=[], dp=[])
    save_run_manifest(RunManifest(budget_spent_usd=10), run.RUN_STATE_DIR)
    assert run.main([str(source)]) == 1
    assert tts_tool.calls == stt_tool.calls == dp_tool.calls == []
    assert failure_report()["attempt_count"] == 0
