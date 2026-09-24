"""Integration test proving Story 1.5's AC2/AC3: full-chain resume across
all four pre-production stages (1.2 asset_analyst, 1.3 story_agent, 1.4
visual_agent, 1.5 voice_agent). Each stage's own skip-check (a validated
persisted contract) is what makes this work -- there is no new dispatcher
or resume-pointer tracking (Design Notes).
"""

from __future__ import annotations

import asyncio
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
from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.stills import StillFailureContract, StillOutcomeContract, StillResultContract
from orchestrator.contracts.veo import (
    VeoFailureContract,
    VeoOutcomeContract,
    VeoResultContract,
    VeoSeedContract,
    sha256_file,
    shot_fingerprint,
)
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.state.production_assets import load_production_assets, upsert_production_asset
from orchestrator.state.run_manifest import load_run_manifest, save_run_manifest
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
            "fade_in_frames": 6,
            "fade_out_frames": 4,
            "still_motion": {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"},
            "video_candidate_rank": 1,
            "video_motion_intent": "Slow drift across the frame.",
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
        tts_calls, tool_content({"audio_path": "audio/narration.wav", "duration_seconds": 45.0, "prompt_word_count": 250, "model": "gemini-3.1-flash-tts-preview", "voice_name": "Gacrux"}),
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

    async def no_stills_stage(settings, manifest):
        # This test exercises stages 1.2-1.5 resume behavior only;
        # stills_agent/run_stills_stage has its own dedicated test coverage
        # further down in this same file.
        return 0

    async def no_delivery_stage(settings, manifest):
        return 0

    monkeypatch.setattr(run, "run_stills_stage", no_stills_stage)
    monkeypatch.setattr(run, "run_delivery_stage", no_delivery_stage)

    assert run.main(["--project", source.parent.name]) == 0

    assert len(visual_calls) == 1
    assert len(tts_calls) == len(stt_calls) == len(dp_calls) == 1
    assert run.VISUAL_PLAN_FILE.exists()
    assert run.SUBTITLE_CUES_FILE.exists()


def test_fully_valid_chain_makes_zero_new_calls_anywhere(chain, monkeypatch):
    """AC3: the full chain (1.2-1.5) has already completed; re-invocation
    must not re-run any stage.
    """
    source, asset_id = chain
    # Fully complete = timing already backfilled and video nominations already
    # judged; otherwise voice skip would still rewrite visual_plan.json via the
    # shot-timing backfill or the promotion step.
    cues = subtitle_cues_for()
    plan = visual_plan_for(asset_id)
    cue = cues["cues"][0]
    plan["shots"][0]["start_seconds"] = cue["start_seconds"]
    plan["shots"][0]["end_seconds"] = cue["end_seconds"]
    plan["shots"][0]["primary_subtitle_cue_ids"] = [cue["cue_id"]]
    plan["shots"][0]["video_promotion_decided"] = True
    run.VISUAL_PLAN_FILE.write_text(json.dumps(plan), encoding="utf-8")
    create_audio_file()
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(cues), encoding="utf-8")

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

    async def no_stills_stage(settings, manifest):
        # This test exercises stages 1.2-1.5 resume behavior only;
        # stills_agent/run_stills_stage has its own dedicated test coverage
        # further down in this same file.
        return 0

    async def no_delivery_stage(settings, manifest):
        return 0

    monkeypatch.setattr(run, "run_stills_stage", no_stills_stage)
    monkeypatch.setattr(run, "run_delivery_stage", no_delivery_stage)

    assert run.main(["--project", source.parent.name]) == 0
    assert run.main(["--project", source.parent.name]) == 0  # idempotent across repeated invocations too

    for path, contents in before.items():
        assert path.read_bytes() == contents
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 0.0
    assert manifest.iteration_counts == {}


def _mock_voice_tools(monkeypatch):
    tts_calls, stt_calls, dp_calls = [], [], []
    monkeypatch.setattr(run, "generate_narration_audio", RecordingTool(
        tts_calls, tool_content({"audio_path": "audio/narration.wav", "duration_seconds": 45.0, "prompt_word_count": 250, "model": "gemini-3.1-flash-tts-preview", "voice_name": "Gacrux"}),
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

    async def no_stills_stage(settings, manifest):
        return 0

    async def no_delivery_stage(settings, manifest):
        return 0

    monkeypatch.setattr(run, "run_stills_stage", no_stills_stage)
    monkeypatch.setattr(run, "run_delivery_stage", no_delivery_stage)

    assert run.main(["--project", source.parent.name]) == 0

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

    async def no_stills_stage(settings, manifest):
        return 0

    async def no_delivery_stage(settings, manifest):
        return 0

    monkeypatch.setattr(run, "run_stills_stage", no_stills_stage)
    monkeypatch.setattr(run, "run_delivery_stage", no_delivery_stage)

    assert run.main(["--project", source.parent.name]) == 0

    assert len(tts_calls) == len(stt_calls) == len(dp_calls) == 1
    assert Path("audio/narration.wav").exists()


def veo_visual_plan_for(asset_id: str, *, count: int = 1) -> VisualPlanContract:
    data = visual_plan_for(asset_id)
    data["shots"] = []
    for sequence in range(1, count + 1):
        data["shots"].append({
            "sequence": sequence,
            "generation_mode": "VEO",
            "visual_treatment": "AI_VIDEO_CANDIDATE",
            "source_asset_ids": [asset_id],
            "shot_goal": f"Show beat {sequence}.",
            "frame_composition": "Center the subject.",
            "motion_plan": "Use restrained motion.",
            "text_overlay": "",
            "source_support": "Directly grounded in the source.",
            "fade_in_frames": 6,
            "fade_out_frames": 6,
            "still_motion": {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"},
            "video_candidate_rank": sequence,
            "video_motion_intent": "Slow drift across the frame.",
        })
    return VisualPlanContract.model_validate(data)


def approved_outcome_for(shot, *, attempt: int = 1) -> VeoOutcomeContract:
    source = Path("source_images/one.png")
    seed_path = Path("generated/veo_seeds") / f"shot_{shot.sequence:02d}_seed.png"
    video_path = Path("generated/veo") / f"shot_{shot.sequence:02d}.mp4"
    dump_path = Path("metadata/veo_operations") / f"shot_{shot.sequence:02d}_attempt_{attempt:02d}.json"
    preview_path = Path("generated/veo/previews") / f"shot_{shot.sequence:02d}_01.jpg"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_bytes(f"seed-{shot.sequence}".encode())
    video_path.write_bytes(f"video-{shot.sequence}".encode())
    dump_path.write_text("{}")
    preview_path.write_bytes(b"preview")
    seed = VeoSeedContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        source_asset_id=shot.source_asset_ids[0],
        source_image_path=str(source),
        source_image_sha256=sha256_file(source),
        local_path=str(seed_path),
        seed_sha256=sha256_file(seed_path),
        prompt="Clean recomposition.",
        model="image-model",
        approved=True,
        qa_summary="Seed matches the requested scene.",
        cost_usd=0.04,
    )
    result = VeoResultContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        seed=seed,
        local_video_path=str(video_path),
        video_sha256=sha256_file(video_path),
        gcs_uri=f"gs://bucket/shot-{shot.sequence}.mp4",
        operation_name=f"operations/{shot.sequence}",
        operation_dump_path=str(dump_path),
        preview_paths=[str(preview_path)],
        model="veo-model",
        approved=True,
        qa_summary="Clip motion and scene fidelity passed.",
        cost_usd=1.2,
    )
    return VeoOutcomeContract(
        produced_by="veo_agent",
        status="SUCCESS",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        result=result,
    )


def failed_outcome_for(shot, *, retryable: bool, reason: str = "clip failed") -> VeoOutcomeContract:
    failure = VeoFailureContract(
        produced_by="veo_agent",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        stage="veo_generation",
        code="SDK_ERROR",
        reason=reason,
        retryable=retryable,
        attempt=1,
        cost_usd=1.2,
    )
    return VeoOutcomeContract(
        produced_by="veo_agent",
        status="FAILURE",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        failure=failure,
    )


def test_veo_resume_matching_approved_entry_makes_zero_agent_or_paid_calls(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    create_audio_file()
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    outcome = approved_outcome_for(visual_plan.shots[0])
    entry = ProductionAssetEntry.from_veo_result(outcome.result, [asset_id])
    upsert_production_asset(entry, path=run.PRODUCTION_ASSETS_FILE)
    before = run.PRODUCTION_ASSETS_FILE.read_bytes()

    async def refuse_agent(**kwargs):
        raise AssertionError("matching approved VEO entry must make zero agent, seed, or Veo calls")

    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 0
    assert run.PRODUCTION_ASSETS_FILE.read_bytes() == before
    assert manifest.budget_spent_usd == 0.0


def test_two_successful_veo_shots_are_both_preserved_by_keyed_upsert(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id, count=2)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    original_plan = run.VISUAL_PLAN_FILE.read_bytes()
    calls = []

    async def successful_agent(**kwargs):
        calls.append(kwargs["shot"]["sequence"])
        shot = visual_plan.shots[kwargs["shot"]["sequence"] - 1]
        outcome = approved_outcome_for(shot, attempt=kwargs["attempt"])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", successful_agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 0

    production = load_production_assets(run.PRODUCTION_ASSETS_FILE)
    assert calls == [1, 2]
    assert list(production.shots) == ["1", "2"]
    assert run.VISUAL_PLAN_FILE.read_bytes() == original_plan
    assert manifest.budget_spent_usd == pytest.approx(2.68)


def test_veo_stage_generates_only_veo_mode_and_never_changes_plan(chain, monkeypatch):
    source, asset_id = chain
    plan_data = veo_visual_plan_for(asset_id, count=2).model_dump(mode="json")
    plan_data["shots"][1]["generation_mode"] = "STILL"
    plan_data["shots"][1]["visual_treatment"] = "USE_EXISTING_ART"
    plan_data["shots"][1]["still_motion"] = {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"}
    visual_plan = VisualPlanContract.model_validate(plan_data)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    original_plan = run.VISUAL_PLAN_FILE.read_bytes()
    calls = []

    async def successful_agent(**kwargs):
        calls.append(kwargs["shot"]["sequence"])
        outcome = approved_outcome_for(visual_plan.shots[0], attempt=kwargs["attempt"])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", successful_agent)
    assert asyncio.run(
        run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))
    ) == 0

    production = load_production_assets(run.PRODUCTION_ASSETS_FILE)
    assert calls == [1]
    assert list(production.shots) == ["1"]
    assert run.VISUAL_PLAN_FILE.read_bytes() == original_plan


def test_veo_budget_gate_demotes_to_still_before_any_agent_or_paid_call(chain, monkeypatch):
    """Running out of budget is a resource fact, not a defect in the shot, so
    the shot falls back to a still and the reel still finishes. The gate still
    runs before the agent or either paid tool. Retry-ceiling and non-retryable
    Veo failures do still halt -- those signal a real seed/QA problem.
    """
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def refuse_agent(**kwargs):
        raise AssertionError("budget gate must run before the agent or either paid tool")

    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(max_budget_usd=1.0), manifest)) == 0
    assert not run.PRODUCTION_ASSETS_FILE.exists()

    # The plan on disk now routes that shot to the stills stage instead, and it
    # still carries the Ken-Burns motion that fallback needs.
    demoted = VisualPlanContract.model_validate_json(run.VISUAL_PLAN_FILE.read_text())
    assert [shot.generation_mode for shot in demoted.shots] == ["STILL"]
    assert demoted.shots[0].still_motion is not None


def test_retryable_veo_failure_retries_with_correction_then_upserts(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return sdk_result(failed_outcome_for(visual_plan.shots[0], retryable=True).model_dump(mode="json"), cost=0.1)
        outcome = approved_outcome_for(visual_plan.shots[0], attempt=kwargs["attempt"])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    assert asyncio.run(run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0
    assert len(calls) == 2
    assert calls[1]["correction"] == "clip failed"
    assert calls[1]["previous_outcome"]["status"] == "FAILURE"
    assert list(load_production_assets(run.PRODUCTION_ASSETS_FILE).shots) == ["1"]


def test_nonretryable_veo_failure_halts_with_structured_report(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        return sdk_result(failed_outcome_for(visual_plan.shots[0], retryable=False, reason="policy blocked").model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    assert asyncio.run(run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    reports = sorted(run.RUN_STATE_DIR.glob("failure_veo_agent_*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert report["attempt_count"] == 1
    assert "policy blocked" in report["reason"]


def test_retryable_veo_failure_stops_at_attempt_ceiling(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        return sdk_result(failed_outcome_for(visual_plan.shots[0], retryable=True, reason="still failing").model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    manifest.max_attempts["veo_agent"] = 2
    save_run_manifest(manifest, state_dir=run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert calls == [1, 2]
    reports = sorted(run.RUN_STATE_DIR.glob("failure_veo_agent_*.json"))
    assert reports
    assert json.loads(reports[-1].read_text(encoding="utf-8"))["attempt_count"] == 2


def test_corrupt_production_manifest_halts_without_reset_or_agent_call(chain, monkeypatch):
    """A malformed persisted manifest is a hard resume failure, never a reset."""
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    run.PRODUCTION_ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    original = b"{ this is not valid json"
    run.PRODUCTION_ASSETS_FILE.write_bytes(original)

    async def refuse_agent(**kwargs):
        raise AssertionError("corrupt production manifest must halt before the agent")

    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent)
    assert asyncio.run(run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert run.PRODUCTION_ASSETS_FILE.read_bytes() == original


def test_veo_agent_exception_charges_full_attempt_ceiling_and_halts(chain, monkeypatch):
    """Fix #4: an exception from generate_veo_asset must charge the whole
    per-attempt ceiling (remaining_budget), since a real cost is never
    available on this branch and up to that much Claude spend may have
    already occurred.
    """
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def raising_agent(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run, "generate_veo_asset", raising_agent)
    settings = make_settings()
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    expected_charge = settings.max_budget_usd - manifest.budget_spent_usd

    assert asyncio.run(run.run_veo_stage(settings, manifest)) == 1

    assert manifest.budget_spent_usd == pytest.approx(expected_charge)
    assert not run.PRODUCTION_ASSETS_FILE.exists()

    attempt_path = run.RUN_STATE_DIR / "veo_agent_shot_1_attempt_1.json"
    assert attempt_path.is_file()
    attempt_data = json.loads(attempt_path.read_text(encoding="utf-8"))
    assert attempt_data["status"] == "EXCEPTION"
    assert "boom" in attempt_data["error"]
    assert attempt_data["charged_cost_usd"] == pytest.approx(expected_charge)

    reports = sorted(run.RUN_STATE_DIR.glob("failure_veo_agent_*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert report["attempt_count"] == 1


def test_stale_production_manifest_halts_before_agent_call(chain, monkeypatch):
    """A keyed entry from an older visual plan cannot be reused silently."""
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    outcome = approved_outcome_for(visual_plan.shots[0])
    upsert_production_asset(
        ProductionAssetEntry.from_veo_result(outcome.result, [asset_id]),
        path=run.PRODUCTION_ASSETS_FILE,
    )

    stale_plan = visual_plan.model_dump(mode="json")
    stale_plan["shots"][0]["shot_goal"] = "A changed shot must force a deliberate rerun."
    run.VISUAL_PLAN_FILE.write_text(json.dumps(stale_plan), encoding="utf-8")

    async def refuse_agent(**kwargs):
        raise AssertionError("stale production manifest must halt before the agent")

    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent)
    assert asyncio.run(run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1


# ---------------------------------------------------------------------------
# Story 2.4: run_stills_stage -- mirrors run_veo_stage's own test matrix
# above, minus the seed stage (single-stage Gemini image-edit call).
# ---------------------------------------------------------------------------


def still_visual_plan_for(asset_id: str, *, count: int = 1) -> VisualPlanContract:
    data = visual_plan_for(asset_id)
    data["shots"] = []
    for sequence in range(1, count + 1):
        data["shots"].append({
            "sequence": sequence,
            "generation_mode": "STILL",
            "visual_treatment": "USE_EXISTING_ART",
            "source_asset_ids": [asset_id],
            "shot_goal": f"Show beat {sequence}.",
            "frame_composition": "Center the subject.",
            "motion_plan": "Slow push-in.",
            "text_overlay": "",
            "source_support": "Directly grounded in the source.",
            "fade_in_frames": 6,
            "fade_out_frames": 4,
            "still_motion": {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"},
            "video_candidate_rank": sequence,
            "video_motion_intent": "Slow drift across the frame.",
        })
    return VisualPlanContract.model_validate(data)


def approved_still_outcome_for(shot) -> StillOutcomeContract:
    image_path = Path("generated/stills") / f"shot_{shot.sequence:02d}.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(f"still-{shot.sequence}".encode())
    result = StillResultContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        source_asset_id=shot.source_asset_ids[0],
        local_image_path=str(image_path),
        image_sha256=sha256_file(image_path),
        model="image-model",
        approved=True,
        qa_summary="Single clean subject, no UI/text.",
        cost_usd=0.04,
    )
    return StillOutcomeContract(
        produced_by="stills_agent",
        status="SUCCESS",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        result=result,
    )


def failed_still_outcome_for(shot, *, retryable: bool, reason: str = "still failed") -> StillOutcomeContract:
    failure = StillFailureContract(
        produced_by="stills_agent",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        stage="still_qa",
        code="STILL_QA_REJECTED",
        reason=reason,
        retryable=retryable,
        attempt=1,
        cost_usd=0.04,
    )
    return StillOutcomeContract(
        produced_by="stills_agent",
        status="FAILURE",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        failure=failure,
    )


def test_stills_resume_matching_approved_entry_makes_zero_agent_or_paid_calls(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    create_audio_file()
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    outcome = approved_still_outcome_for(visual_plan.shots[0])
    entry = ProductionAssetEntry.from_still_result(outcome.result, [asset_id])
    upsert_production_asset(entry, path=run.PRODUCTION_ASSETS_FILE)
    before = run.PRODUCTION_ASSETS_FILE.read_bytes()

    async def refuse_agent(**kwargs):
        raise AssertionError("matching approved STILL entry must make zero agent or paid calls")

    monkeypatch.setattr(run, "generate_still_asset", refuse_agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 0
    assert run.PRODUCTION_ASSETS_FILE.read_bytes() == before
    assert manifest.budget_spent_usd == 0.0


def test_two_successful_still_shots_are_both_preserved_by_keyed_upsert(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id, count=2)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    original_plan = run.VISUAL_PLAN_FILE.read_bytes()
    calls = []

    async def successful_agent(**kwargs):
        calls.append(kwargs["shot"]["sequence"])
        shot = visual_plan.shots[kwargs["shot"]["sequence"] - 1]
        outcome = approved_still_outcome_for(shot)
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", successful_agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 0

    production = load_production_assets(run.PRODUCTION_ASSETS_FILE)
    assert calls == [1, 2]
    assert list(production.shots) == ["1", "2"]
    assert run.VISUAL_PLAN_FILE.read_bytes() == original_plan
    assert manifest.budget_spent_usd == pytest.approx(0.28)


def test_stills_stage_generates_only_still_mode_and_never_changes_plan(chain, monkeypatch):
    source, asset_id = chain
    plan_data = still_visual_plan_for(asset_id, count=2).model_dump(mode="json")
    plan_data["shots"][1]["generation_mode"] = "VEO"
    plan_data["shots"][1]["visual_treatment"] = "AI_VIDEO_CANDIDATE"
    visual_plan = VisualPlanContract.model_validate(plan_data)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    original_plan = run.VISUAL_PLAN_FILE.read_bytes()
    calls = []

    async def successful_agent(**kwargs):
        calls.append(kwargs["shot"]["sequence"])
        outcome = approved_still_outcome_for(visual_plan.shots[0])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", successful_agent)
    assert asyncio.run(
        run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))
    ) == 0

    production = load_production_assets(run.PRODUCTION_ASSETS_FILE)
    assert calls == [1]
    assert list(production.shots) == ["1"]
    assert run.VISUAL_PLAN_FILE.read_bytes() == original_plan


def test_stills_budget_gate_halts_before_any_agent_or_paid_call(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def refuse_agent(**kwargs):
        raise AssertionError("budget gate must run before the agent or the paid tool")

    monkeypatch.setattr(run, "generate_still_asset", refuse_agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(max_budget_usd=0.01), manifest)) == 1
    assert not run.PRODUCTION_ASSETS_FILE.exists()


def test_stills_agent_no_usable_cost_halts(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        outcome = approved_still_outcome_for(visual_plan.shots[0])
        return sdk_result(outcome.model_dump(mode="json"), cost=None)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    reports = sorted(run.RUN_STATE_DIR.glob("failure_stills_agent_*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert "no usable Claude cost" in report["reason"]
    assert not run.PRODUCTION_ASSETS_FILE.exists()


def test_stills_agent_wrong_fingerprint_is_stamped_from_requested_shot(chain, monkeypatch):
    """Agent-retyped fingerprints must not discard an otherwise-valid still."""
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    shot = visual_plan.shots[0]
    expected_fp = shot_fingerprint(shot)

    async def agent(**kwargs):
        outcome = approved_still_outcome_for(shot).model_dump(mode="json")
        outcome["shot_fingerprint"] = "0" * 64
        outcome["result"]["shot_fingerprint"] = "0" * 64
        return sdk_result(outcome, cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0
    assets = json.loads(run.PRODUCTION_ASSETS_FILE.read_text(encoding="utf-8"))
    entry = assets["shots"]["1"]
    assert entry["shot_fingerprint"] == expected_fp
    attempt = json.loads(
        (run.RUN_STATE_DIR / "stills_agent_shot_1_attempt_1.json").read_text(encoding="utf-8")
    )
    assert attempt["structured_output"]["shot_fingerprint"] == expected_fp


def test_stills_agent_wrong_sequence_halts_and_persists_invalid_outcome(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id, count=2)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        # Agent returns shot 2's approved still while the stage requested shot 1.
        outcome = approved_still_outcome_for(visual_plan.shots[1]).model_dump(mode="json")
        return sdk_result(outcome, cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    reports = sorted(run.RUN_STATE_DIR.glob("failure_stills_agent_*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert "does not match the requested source shot" in report["reason"]
    attempt_path = run.RUN_STATE_DIR / "stills_agent_shot_1_attempt_1.json"
    assert attempt_path.is_file()
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    assert attempt["status"] == "INVALID_OUTCOME"
    assert attempt["structured_output"]["shot_sequence"] == 2
    assert not run.PRODUCTION_ASSETS_FILE.exists()


def test_stills_agent_invented_cost_is_replaced_not_fatal(chain, monkeypatch):
    """The agent's cost_usd is provenance it frequently retypes wrongly, so the
    orchestrator stamps its own figure rather than gating on the agent's.

    Replaces an earlier test that asserted a halt here. A live run halted a
    whole reel because one shot reported $0.1290315 -- roughly its own Claude
    session cost -- for an image call the tool had reported as free, while two
    other shots in the same run copied the same value correctly.
    """
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        outcome = approved_still_outcome_for(visual_plan.shots[0]).model_dump(mode="json")
        outcome["result"]["cost_usd"] = 999.0
        return sdk_result(outcome, cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    settings = make_settings()
    manifest = load_run_manifest(run.RUN_STATE_DIR)

    assert asyncio.run(run.run_stills_stage(settings, manifest)) == 0
    assert not sorted(run.RUN_STATE_DIR.glob("failure_stills_agent_*.json"))

    attempt = json.loads(
        (run.RUN_STATE_DIR / "stills_agent_shot_1_attempt_1.json").read_text(encoding="utf-8")
    )
    assert attempt["paid_tool_cost_usd"] == pytest.approx(settings.image_call_cost_usd)
    assert attempt["structured_output"]["result"]["cost_usd"] == pytest.approx(
        settings.image_call_cost_usd
    )
    # The invented figure must not reach the budget either.
    assert manifest.budget_spent_usd == pytest.approx(0.1 + settings.image_call_cost_usd)


def test_stills_cost_is_stamped_when_the_provider_is_free(chain, monkeypatch):
    """Under IMAGE_PROVIDER=codex an image costs nothing, so the ceiling was
    exactly 0.0 and any invented figure was fatal. Stamping removes that."""
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        outcome = approved_still_outcome_for(visual_plan.shots[0]).model_dump(mode="json")
        outcome["result"]["cost_usd"] = 0.1290315  # the exact figure from the halted run
        return sdk_result(outcome, cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    settings = make_settings(image_call_cost_usd=0.0)
    manifest = load_run_manifest(run.RUN_STATE_DIR)

    assert asyncio.run(run.run_stills_stage(settings, manifest)) == 0
    attempt = json.loads(
        (run.RUN_STATE_DIR / "stills_agent_shot_1_attempt_1.json").read_text(encoding="utf-8")
    )
    assert attempt["paid_tool_cost_usd"] == 0.0
    assert manifest.budget_spent_usd == pytest.approx(0.1)


def test_veo_agent_invented_cost_is_replaced_not_fatal(chain, monkeypatch):
    """Same stamping on the Veo side, where the seed and clip costs are
    separate leaves that the outcome's cost_usd property sums."""
    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        outcome = approved_outcome_for(visual_plan.shots[0]).model_dump(mode="json")
        outcome["result"]["cost_usd"] = 999.0
        outcome["result"]["seed"]["cost_usd"] = 999.0
        return sdk_result(outcome, cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    settings = make_settings()
    manifest = load_run_manifest(run.RUN_STATE_DIR)

    assert asyncio.run(run.run_veo_stage(settings, manifest)) == 0
    attempt = json.loads(
        (run.RUN_STATE_DIR / "veo_agent_shot_1_attempt_1.json").read_text(encoding="utf-8")
    )
    expected = settings.image_call_cost_usd + settings.veo_call_cost_usd
    assert attempt["paid_tool_cost_usd"] == pytest.approx(expected)
    assert attempt["structured_output"]["result"]["seed"]["cost_usd"] == pytest.approx(
        settings.image_call_cost_usd
    )
    assert attempt["structured_output"]["result"]["cost_usd"] == pytest.approx(
        settings.veo_call_cost_usd
    )


def test_a_veo_attempt_that_never_reached_the_clip_is_not_charged_for_one(chain):
    """A seed-stage failure spent nothing on Veo, so charging the clip rate
    would drain the budget for calls that never happened."""
    settings = make_settings()
    visual_plan = veo_visual_plan_for("img_aaaaaaaaaaaa")
    outcome = approved_outcome_for(visual_plan.shots[0])

    seed_only = outcome.model_copy(update={
        "status": "FAILURE",
        "result": None,
        "failure": VeoFailureContract(
            produced_by="veo_tool",
            shot_sequence=outcome.shot_sequence,
            shot_fingerprint=outcome.shot_fingerprint,
            stage="seed_generation",
            code="SEED_GENERATION_FAILED",
            reason="codex refused",
            retryable=True,
            attempt=1,
        ),
    })
    assert run._veo_attempt_cost(seed_only, settings) == (settings.image_call_cost_usd, 0.0)

    # Anything that got as far as the clip is charged for both calls.
    assert run._veo_attempt_cost(outcome, settings) == (
        settings.image_call_cost_usd,
        settings.veo_call_cost_usd,
    )

def test_stills_agent_outcome_source_asset_not_cited_halts(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        outcome = approved_still_outcome_for(visual_plan.shots[0]).model_dump(mode="json")
        outcome["result"]["source_asset_id"] = "img_bbbbbbbbbbbb"
        return sdk_result(outcome, cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    reports = sorted(run.RUN_STATE_DIR.glob("failure_stills_agent_*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert "Invalid stills agent outcome" in report["reason"]
    assert "not cited by the requested shot" in report["reason"]


def test_retryable_still_failure_retries_with_correction_then_upserts(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return sdk_result(
                failed_still_outcome_for(visual_plan.shots[0], retryable=True, reason="duplicate subject").model_dump(mode="json"),
                cost=0.1,
            )
        outcome = approved_still_outcome_for(visual_plan.shots[0])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0
    assert len(calls) == 2
    assert calls[1]["correction"] == "duplicate subject"
    assert calls[1]["previous_outcome"]["status"] == "FAILURE"
    assert list(load_production_assets(run.PRODUCTION_ASSETS_FILE).shots) == ["1"]


def test_nonretryable_still_failure_halts_with_structured_report(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def agent(**kwargs):
        return sdk_result(
            failed_still_outcome_for(visual_plan.shots[0], retryable=False, reason="policy blocked").model_dump(mode="json"),
            cost=0.1,
        )

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    reports = sorted(run.RUN_STATE_DIR.glob("failure_stills_agent_*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert report["attempt_count"] == 1
    assert "policy blocked" in report["reason"]


def test_retryable_still_failure_stops_at_attempt_ceiling(chain, monkeypatch):
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    # The ceiling now comes from the project's config.json, which is the
    # mechanism an operator actually uses to raise or lower it.
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    manifest.max_attempts["stills_agent"] = 2
    save_run_manifest(manifest, state_dir=run.RUN_STATE_DIR)
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        return sdk_result(
            failed_still_outcome_for(visual_plan.shots[0], retryable=True, reason="still failing").model_dump(mode="json"),
            cost=0.1,
        )

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert calls == [1, 2]
    reports = sorted(run.RUN_STATE_DIR.glob("failure_stills_agent_*.json"))
    assert reports
    assert json.loads(reports[-1].read_text(encoding="utf-8"))["attempt_count"] == 2


def test_corrupt_production_manifest_halts_stills_without_reset_or_agent_call(chain, monkeypatch):
    """A malformed persisted manifest is a hard resume failure, never a reset."""
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    run.PRODUCTION_ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    original = b"{ this is not valid json"
    run.PRODUCTION_ASSETS_FILE.write_bytes(original)

    async def refuse_agent(**kwargs):
        raise AssertionError("corrupt production manifest must halt before the agent")

    monkeypatch.setattr(run, "generate_still_asset", refuse_agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert run.PRODUCTION_ASSETS_FILE.read_bytes() == original


def test_stale_production_manifest_halts_before_stills_agent_call(chain, monkeypatch):
    """A keyed entry from an older visual plan cannot be reused silently."""
    source, asset_id = chain
    visual_plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    outcome = approved_still_outcome_for(visual_plan.shots[0])
    upsert_production_asset(
        ProductionAssetEntry.from_still_result(outcome.result, [asset_id]),
        path=run.PRODUCTION_ASSETS_FILE,
    )

    stale_plan = visual_plan.model_dump(mode="json")
    stale_plan["shots"][0]["shot_goal"] = "A changed shot must force a deliberate rerun."
    run.VISUAL_PLAN_FILE.write_text(json.dumps(stale_plan), encoding="utf-8")

    async def refuse_agent(**kwargs):
        raise AssertionError("stale production manifest must halt before the agent")

    monkeypatch.setattr(run, "generate_still_asset", refuse_agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1


def test_veo_and_stills_stages_together_cover_every_shot_exactly_once(chain, monkeypatch):
    """Epic-completion shape check (not a runtime gate this story enforces
    alone): once both stages run over a plan with disjoint VEO/STILL shots,
    ProductionAssetsContract has exactly one approved entry per shot.
    """
    source, asset_id = chain
    plan_data = {
        "produced_by": "visual_agent",
        "overall_visual_style": "Reflective, calm illustration.",
        "shots": [
            {
                "sequence": 1,
                "generation_mode": "VEO",
                "visual_treatment": "AI_VIDEO_CANDIDATE",
                "source_asset_ids": [asset_id],
                "shot_goal": "Show motion.",
                "frame_composition": "Center the subject.",
                "motion_plan": "Use restrained motion.",
                "text_overlay": "",
                "source_support": "Directly grounded in the source.",
                "fade_in_frames": 6,
                "fade_out_frames": 6,
                "still_motion": {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"},
                "video_candidate_rank": 1,
                "video_motion_intent": "Slow drift across the frame.",
            },
            {
                "sequence": 2,
                "generation_mode": "STILL",
                "visual_treatment": "USE_EXISTING_ART",
                "source_asset_ids": [asset_id],
                "shot_goal": "Show a quiet beat.",
                "frame_composition": "Center the subject.",
                "motion_plan": "Slow push-in.",
                "text_overlay": "",
                "source_support": "Directly grounded in the source.",
                "fade_in_frames": 6,
                "fade_out_frames": 4,
                "still_motion": {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"},
                "video_candidate_rank": 2,
                "video_motion_intent": "Slow drift across the frame.",
            },
        ],
        "quality_review": {
            "verdict": "APPROVE", "ready_for_generation": True, "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "generation_mode_appropriateness": 9,
                "visual_coherence": 9, "narrative_alignment": 9, "internal_consistency": 9,
            },
        },
    }
    visual_plan = VisualPlanContract.model_validate(plan_data)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")

    async def veo_agent_call(**kwargs):
        outcome = approved_outcome_for(visual_plan.shots[0])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    async def stills_agent_call(**kwargs):
        outcome = approved_still_outcome_for(visual_plan.shots[1])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", veo_agent_call)
    monkeypatch.setattr(run, "generate_still_asset", stills_agent_call)

    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 0
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 0

    production = load_production_assets(run.PRODUCTION_ASSETS_FILE)
    assert set(production.shots) == {"1", "2"}
    assert production.shots["1"].generation_mode == "VEO"
    assert production.shots["2"].generation_mode == "STILL"


# ============================================================
# Video promotion: visual_agent nominates, the orchestrator decides which
# nominations physically fit a single Veo clip (AD-15).
# ============================================================

def _promotion_plan(asset_id: str, durations: list[float], ranks: list[int | None]):
    """A still plan with real timing, so nominations can be judged."""
    plan = still_visual_plan_for(asset_id, count=len(durations))
    start = 0.0
    for shot, duration, rank in zip(plan.shots, durations, ranks):
        shot.start_seconds = round(start, 2)
        shot.end_seconds = round(start + duration, 2)
        shot.video_candidate_rank = rank
        start += duration
    return plan


def test_promotion_takes_the_best_nominations_that_fit_one_clip():
    """The real reel's six durations: only 7.64s, 5.48s and 5.96s fit inside an
    8s clip, so those are the shots that become video."""
    plan = _promotion_plan(
        "img_aaaaaaaaaaaa",
        [7.64, 11.48, 12.88, 9.80, 5.48, 5.96],
        [1, 2, 3, 4, 5, 6],
    )
    promoted = run._promote_video_candidates(plan, make_settings())
    assert promoted == [1, 5, 6]
    assert [shot.generation_mode for shot in plan.shots] == [
        "VEO", "STILL", "STILL", "STILL", "VEO", "VEO",
    ]


def test_promotion_never_exceeds_the_video_cap():
    plan = _promotion_plan(
        "img_aaaaaaaaaaaa", [5.0] * 6, [1, 2, 3, 4, 5, 6],
    )
    promoted = run._promote_video_candidates(plan, make_settings())
    assert promoted == [1, 2, 3]
    assert len(promoted) == run.MAX_VEO_SHOTS


def test_promotion_follows_rank_not_shot_order():
    plan = _promotion_plan(
        "img_aaaaaaaaaaaa", [5.0] * 4, [4, 3, 2, 1],
    )
    promoted = run._promote_video_candidates(plan, make_settings())
    assert promoted == [4, 3, 2]


def test_promotion_degrades_when_too_few_shots_fit_rather_than_halting():
    """A reel whose beats are nearly all long legitimately ends up with fewer
    video shots -- it must still produce a video."""
    plan = _promotion_plan(
        "img_aaaaaaaaaaaa", [11.0, 12.0, 13.0, 5.0], [1, 2, 3, 4],
    )
    promoted = run._promote_video_candidates(plan, make_settings())
    assert promoted == [4]
    assert [shot.generation_mode for shot in plan.shots] == ["STILL", "STILL", "STILL", "VEO"]


def test_promotion_with_no_shot_short_enough_leaves_an_all_still_reel():
    plan = _promotion_plan("img_aaaaaaaaaaaa", [11.0, 12.0, 13.0, 14.0], [1, 2, 3, 4])
    promoted = run._promote_video_candidates(plan, make_settings())
    assert promoted == []
    assert all(shot.generation_mode == "STILL" for shot in plan.shots)


def test_promotion_still_runs_when_a_regenerated_plan_inherited_its_timing(chain, monkeypatch):
    """A regenerated plan can inherit the previous file's timing, so it needs no
    cue mapping -- but its own nominations have still never been judged. Keying
    promotion off the cue-mapping branch alone would silently skip it and
    quietly return the reel to all-stills.
    """
    source, asset_id = chain
    cues = subtitle_cues_for()
    plan = visual_plan_for(asset_id)
    cue = cues["cues"][0]
    plan["shots"][0]["start_seconds"] = 0.0
    plan["shots"][0]["end_seconds"] = 5.0  # short enough to fit one clip
    plan["shots"][0]["primary_subtitle_cue_ids"] = [cue["cue_id"]]
    # Timing present, but never judged -- exactly the inherited-timing case.
    plan["shots"][0]["video_promotion_decided"] = False
    run.VISUAL_PLAN_FILE.write_text(json.dumps(plan), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(cues), encoding="utf-8")

    run._backfill_visual_plan_shot_timing_from_cues(make_settings())

    judged = VisualPlanContract.model_validate_json(run.VISUAL_PLAN_FILE.read_text())
    assert all(shot.video_promotion_decided for shot in judged.shots)
    assert judged.shots[0].generation_mode == "VEO"


def test_promotion_is_not_redone_once_decided(chain, monkeypatch):
    """A shot deliberately left (or demoted to) STILL must stay that way across
    resumes, or an already-approved still's fingerprint would churn."""
    source, asset_id = chain
    cues = subtitle_cues_for()
    plan = visual_plan_for(asset_id)
    cue = cues["cues"][0]
    plan["shots"][0]["start_seconds"] = cue["start_seconds"]
    plan["shots"][0]["end_seconds"] = cue["end_seconds"]
    plan["shots"][0]["primary_subtitle_cue_ids"] = [cue["cue_id"]]
    plan["shots"][0]["video_promotion_decided"] = True
    plan["shots"][0]["generation_mode"] = "STILL"
    run.VISUAL_PLAN_FILE.write_text(json.dumps(plan), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(cues), encoding="utf-8")

    run._backfill_visual_plan_shot_timing_from_cues(make_settings())

    unchanged = VisualPlanContract.model_validate_json(run.VISUAL_PLAN_FILE.read_text())
    assert unchanged.shots[0].generation_mode == "STILL"
