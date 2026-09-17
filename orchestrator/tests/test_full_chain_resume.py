"""Integration test proving Story 1.5's AC2/AC3: full-chain resume across
all four pre-production stages (1.2 asset_analyst, 1.3 story_agent, 1.4
visual_agent, 1.5 voice_agent). Each stage's own skip-check (a validated
persisted contract) is what makes this work -- there is no new dispatcher
or resume-pointer tracking (Design Notes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage
from PIL import Image

import orchestrator.agents.asset_analyst as asset_analyst_module
import orchestrator.agents.story_agent as story_agent_module
import orchestrator.agents.visual_agent as visual_agent_module
import orchestrator.run as run
from orchestrator.preflight import PreflightResult
from orchestrator.state.run_manifest import load_run_manifest
from orchestrator.tests.test_preflight import make_settings
from orchestrator.tools.deterministic_tools import inspect_image

NARRATION_SCRIPT = " ".join(f"word{i}" for i in range(100))


def analysis_for() -> dict:
    region = dict(x_min=0, y_min=0, x_max=1000, y_max=1000)
    return {
        "content_type": "screenshot",
        "source_text": {
            "heading": "", "body_text": "Read me", "prominent_quote": "",
            "other_story_text": [], "ui_text": [], "verbatim_blocks": [],
            "transcription_confidence": 0.9,
        },
        "visual": {
            "description": "A red card", "main_elements": [], "visual_subjects": [],
            "real_people_visible": False, "illustrated_or_cartoon_figures_visible": False,
            "figure_descriptions": [], "environment": "", "important_actions": [],
            "composition_notes": "",
        },
        "semantic_summary": {
            "core_idea": "A reading prompt", "concepts": [], "emotional_tone": [],
            "requires_external_context": False, "context_needed": [],
        },
        "named_entities": [], "possible_story_roles": ["SETUP"],
        "production": {
            "contains_app_ui": False, "story_art_region_present": True,
            "story_art_region": region, "story_text_region": region, "ui_regions": [],
            "story_art_description": "A card", "vertical_video_suitability": "high",
            "recommended_crop_strategy": "USE_FULL_FRAME",
            "safe_to_crop_ui_without_losing_story": True,
            "suggested_motion": [], "visual_cleanup_needed": [],
        },
        "uncertainties": [],
    }


def analyzed_assets_contract_for(asset: dict) -> dict:
    return {
        "produced_by": "asset_analyst",
        "assets": [{
            "asset_id": asset["asset_id"],
            "source_path": asset["source_path"],
            "original_filename": asset["original_filename"],
            "analysis": analysis_for(),
        }],
    }


def final_story_plan_for(asset_id: str) -> dict:
    return {
        "produced_by": "story_agent",
        "story_title": "The Backwards Law",
        "core_thesis": "Acceptance beats striving.",
        "narrative_strategy": "Hook, human story, reveal, explanation, reflection.",
        "target_duration_seconds": 45.0,
        "target_word_count": 100,
        "hook": NARRATION_SCRIPT,
        "narration_script": NARRATION_SCRIPT,
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
            "narration": NARRATION_SCRIPT,
            "source_asset_ids": [asset_id],
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


def visual_plan_for(asset_id: str) -> dict:
    return {
        "produced_by": "visual_agent",
        "overall_visual_style": "Reflective, calm, textured halftone illustrations.",
        "shots": [{
            "sequence": 1,
            "generation_mode": "STILL",
            "visual_treatment": "USE_EXISTING_ART",
            "source_asset_ids": [asset_id],
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


def subtitle_cues_for() -> dict:
    tokens = NARRATION_SCRIPT.split(" ")
    words = [
        {"index": i + 1, "word": word, "start_seconds": i * 0.4, "end_seconds": i * 0.4 + 0.3}
        for i, word in enumerate(tokens)
    ]
    return {
        "produced_by": "voice_agent",
        "source_narration_script": NARRATION_SCRIPT,
        "alignment_ratio": 0.99,
        "cues": [{
            "cue_id": "cue_001", "start_seconds": words[0]["start_seconds"],
            "end_seconds": words[-1]["end_seconds"], "word_count": len(words),
            "text": NARRATION_SCRIPT, "words": words,
        }],
    }


def sdk_result(output, *, cost=0.3):
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="test-session", total_cost_usd=cost,
        structured_output=output,
    )


def refuse_query(label: str):
    async def fake_query(*, prompt, options):
        raise AssertionError(f"{label} must not run: its contract is already validly persisted")
        yield  # pragma: no cover - unreachable; keeps this an async generator

    return fake_query


def tool_content(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def create_audio_file() -> None:
    audio_path = Path("audio/narration.wav")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-audio-bytes")


class RefusingTool:
    async def handler(self, args: dict) -> dict:
        raise AssertionError("voice_agent's tools must not run: SubtitleCuesContract is already validly persisted")


class RecordingTool:
    """Records every call and returns a canned response; the TTS variant
    also writes `audio/narration.wav` (the real tool's side effect), since
    `SubtitleCuesContract.validate_sources`'s AD-11 check requires it to
    exist after a real successful run.
    """

    def __init__(self, calls, response, *, writes_audio=False):
        self._calls = calls
        self._response = response
        self._writes_audio = writes_audio

    async def handler(self, args):
        self._calls.append(args)
        if self._writes_audio:
            create_audio_file()
        return self._response


@pytest.fixture
def chain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run, "load_settings", lambda: make_settings())
    monkeypatch.setattr(run, "run_preflight", lambda **kwargs: PreflightResult(passed=True))

    source = tmp_path / "source_images"
    source.mkdir()
    image_path = source / "one.png"
    Image.new("RGB", (20, 40), "red").save(image_path)
    asset = inspect_image(image_path)

    run.ANALYZED_ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    run.ANALYZED_ASSETS_FILE.write_text(json.dumps(analyzed_assets_contract_for(asset)), encoding="utf-8")
    run.FINAL_STORY_PLAN_FILE.write_text(json.dumps(final_story_plan_for(asset["asset_id"])), encoding="utf-8")

    return source, asset["asset_id"]


def test_partial_chain_resumes_only_from_first_missing_stage(chain, monkeypatch):
    """AC2: stages 1-2 (asset_analyst, story_agent) already valid; 3-4
    (visual_agent, voice_agent) missing. Re-invocation must not re-run
    asset_analyst/story_agent, and must resume from visual_agent.
    """
    source, asset_id = chain
    assert not run.VISUAL_PLAN_FILE.exists()
    assert not run.SUBTITLE_CUES_FILE.exists()

    monkeypatch.setattr(asset_analyst_module, "query", refuse_query("asset_analyst"))
    monkeypatch.setattr(story_agent_module, "query", refuse_query("story_agent"))

    visual_calls = []

    async def fake_visual_query(*, prompt, options):
        visual_calls.append((prompt, options))
        yield sdk_result(visual_plan_for(asset_id))

    monkeypatch.setattr(visual_agent_module, "query", fake_visual_query)

    tts_calls, stt_calls, dp_calls = [], [], []

    monkeypatch.setattr(run, "generate_narration_audio", RecordingTool(
        tts_calls, tool_content({"audio_path": "audio/narration.wav", "duration_seconds": 45.0, "prompt_word_count": 250}),
        writes_audio=True,
    ))
    monkeypatch.setattr(run, "extract_word_timing", RecordingTool(
        stt_calls, tool_content({
            "locked_narration": NARRATION_SCRIPT,
            "recognized_transcript": NARRATION_SCRIPT,
            "alignment_stats": {"exact_match_ratio": 0.99},
            "words": [
                {"index": i + 1, "canonical_word": w, "start_seconds": i * 0.4, "end_seconds": i * 0.4 + 0.3}
                for i, w in enumerate(NARRATION_SCRIPT.split(" "))
            ],
        }),
    ))
    monkeypatch.setattr(run, "build_subtitle_cues", RecordingTool(
        dp_calls, tool_content({
            "alignment_ratio": 0.99, "cue_count": 1,
            "cues": [{
                "cue_id": "cue_001", "start_seconds": 0.0, "end_seconds": 40.0, "word_count": 100,
                "text": NARRATION_SCRIPT,
                "words": [
                    {"index": i + 1, "word": w, "start_seconds": i * 0.4, "end_seconds": i * 0.4 + 0.3}
                    for i, w in enumerate(NARRATION_SCRIPT.split(" "))
                ],
            }],
        }),
    ))

    assert run.main([str(source)]) == 0

    assert len(visual_calls) == 1
    assert len(tts_calls) == len(stt_calls) == len(dp_calls) == 1
    assert run.VISUAL_PLAN_FILE.exists()
    assert run.SUBTITLE_CUES_FILE.exists()


def test_fully_valid_chain_makes_zero_new_calls_anywhere(chain, monkeypatch):
    """AC3: the full chain (1.2-1.5) has already completed; re-invocation
    must not re-run any stage.
    """
    source, asset_id = chain
    run.VISUAL_PLAN_FILE.write_text(json.dumps(visual_plan_for(asset_id)), encoding="utf-8")
    create_audio_file()
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    before = {
        path: path.read_bytes() for path in (
            run.ANALYZED_ASSETS_FILE, run.FINAL_STORY_PLAN_FILE, run.VISUAL_PLAN_FILE, run.SUBTITLE_CUES_FILE,
        )
    }

    monkeypatch.setattr(asset_analyst_module, "query", refuse_query("asset_analyst"))
    monkeypatch.setattr(story_agent_module, "query", refuse_query("story_agent"))
    monkeypatch.setattr(visual_agent_module, "query", refuse_query("visual_agent"))
    monkeypatch.setattr(run, "generate_narration_audio", RefusingTool())
    monkeypatch.setattr(run, "extract_word_timing", RefusingTool())
    monkeypatch.setattr(run, "build_subtitle_cues", RefusingTool())

    assert run.main([str(source)]) == 0
    assert run.main([str(source)]) == 0  # idempotent across repeated invocations too

    for path, contents in before.items():
        assert path.read_bytes() == contents
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 0.0
    assert manifest.iteration_counts == {}


def _mock_voice_tools(monkeypatch):
    tts_calls, stt_calls, dp_calls = [], [], []
    monkeypatch.setattr(run, "generate_narration_audio", RecordingTool(
        tts_calls, tool_content({"audio_path": "audio/narration.wav", "duration_seconds": 45.0, "prompt_word_count": 250}),
        writes_audio=True,
    ))
    monkeypatch.setattr(run, "extract_word_timing", RecordingTool(
        stt_calls, tool_content({
            "locked_narration": NARRATION_SCRIPT,
            "recognized_transcript": NARRATION_SCRIPT,
            "alignment_stats": {"exact_match_ratio": 0.99},
            "words": [
                {"index": i + 1, "canonical_word": w, "start_seconds": i * 0.4, "end_seconds": i * 0.4 + 0.3}
                for i, w in enumerate(NARRATION_SCRIPT.split(" "))
            ],
        }),
    ))
    monkeypatch.setattr(run, "build_subtitle_cues", RecordingTool(
        dp_calls, tool_content({
            "alignment_ratio": 0.99, "cue_count": 1,
            "cues": [{
                "cue_id": "cue_001", "start_seconds": 0.0, "end_seconds": 40.0, "word_count": 100,
                "text": NARRATION_SCRIPT,
                "words": [
                    {"index": i + 1, "word": w, "start_seconds": i * 0.4, "end_seconds": i * 0.4 + 0.3}
                    for i, w in enumerate(NARRATION_SCRIPT.split(" "))
                ],
            }],
        }),
    ))
    return tts_calls, stt_calls, dp_calls


def test_stages_1_to_4_valid_only_voice_agent_missing_resumes_at_voice_agent_only(chain, monkeypatch):
    """AC2: narration and visual planning are both already done; only
    voice_agent's SubtitleCuesContract is missing. Re-invocation must
    resume at voice_agent only -- stages 1-4 (asset_analyst, story_agent,
    visual_agent) make zero calls.
    """
    source, asset_id = chain
    run.VISUAL_PLAN_FILE.write_text(json.dumps(visual_plan_for(asset_id)), encoding="utf-8")
    assert not run.SUBTITLE_CUES_FILE.exists()

    monkeypatch.setattr(asset_analyst_module, "query", refuse_query("asset_analyst"))
    monkeypatch.setattr(story_agent_module, "query", refuse_query("story_agent"))
    monkeypatch.setattr(visual_agent_module, "query", refuse_query("visual_agent"))
    tts_calls, stt_calls, dp_calls = _mock_voice_tools(monkeypatch)

    assert run.main([str(source)]) == 0

    assert len(tts_calls) == len(stt_calls) == len(dp_calls) == 1
    assert run.SUBTITLE_CUES_FILE.exists()


def test_persisted_subtitle_cues_with_deleted_audio_does_not_skip(chain, monkeypatch):
    """AD-11: earlier stages (1-3) stay valid and untouched, but a
    persisted SubtitleCuesContract whose audio/narration.wav has since been
    deleted must not be treated as a valid skip target -- voice_agent runs
    for real again.
    """
    source, asset_id = chain
    run.VISUAL_PLAN_FILE.write_text(json.dumps(visual_plan_for(asset_id)), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    assert not Path("audio/narration.wav").exists()

    monkeypatch.setattr(asset_analyst_module, "query", refuse_query("asset_analyst"))
    monkeypatch.setattr(story_agent_module, "query", refuse_query("story_agent"))
    monkeypatch.setattr(visual_agent_module, "query", refuse_query("visual_agent"))
    tts_calls, stt_calls, dp_calls = _mock_voice_tools(monkeypatch)

    assert run.main([str(source)]) == 0

    assert len(tts_calls) == len(stt_calls) == len(dp_calls) == 1
    assert Path("audio/narration.wav").exists()
