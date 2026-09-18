from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage
from pydantic import ValidationError

import orchestrator.run as run
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.visual_plan import VisualPlanContract, _scene_content_fingerprint
from orchestrator.preflight import PreflightResult
from orchestrator.state.run_manifest import RunManifest, load_run_manifest, save_run_manifest
from orchestrator.tests.test_preflight import make_settings

agent_module = importlib.import_module("orchestrator.agents.visual_agent")

ASSET_IDS = ["img_aaaaaaaaaaaa", "img_bbbbbbbbbbbb", "img_cccccccccccc"]


def analysis_for(asset_id: str) -> dict:
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


def analyzed_assets_contract_for(asset_ids: list[str]) -> dict:
    return {
        "produced_by": "asset_analyst",
        "assets": [
            {
                "asset_id": asset_id,
                "source_path": f"/tmp/{asset_id}.png",
                "original_filename": f"{asset_id}.png",
                "analysis": analysis_for(asset_id),
            }
            for asset_id in asset_ids
        ],
    }


def _scene(sequence: int, words: list[str], asset_id: str, duration: float = 15.0) -> dict:
    return {
        "sequence": sequence,
        "role": "SETUP",
        "estimated_duration_seconds": duration,
        "narration": " ".join(words),
        "source_asset_ids": [asset_id],
        "source_support": "Directly supports this beat.",
        "visual_intent": "A calm visual.",
        "suggested_visual_treatment": "USE_EXISTING_ART",
        "impact_text": "",
        "emotional_goal": "calm",
    }


def final_story_plan_for(asset_ids: list[str]) -> dict:
    words_per_scene = 33
    total_words = words_per_scene * len(asset_ids)
    all_words = [f"word{i}" for i in range(total_words)]
    scenes = []
    for index, asset_id in enumerate(asset_ids):
        chunk = all_words[index * words_per_scene:(index + 1) * words_per_scene]
        scenes.append(_scene(index + 1, chunk, asset_id))
    narration_script = " ".join(all_words)
    return {
        "produced_by": "story_agent",
        "story_title": "The Backwards Law",
        "core_thesis": "Acceptance beats striving.",
        "narrative_strategy": "Hook, human story, reveal, explanation, reflection.",
        "target_duration_seconds": 45.0,
        "target_word_count": total_words,
        "hook": scenes[0]["narration"],
        "narration_script": narration_script,
        "voice_direction": {
            "persona": "Mature, thoughtful guide", "tone": ["calm"], "pace_wpm": 130,
            "delivery_notes": [], "emphasis_phrases": [], "pause_after_phrases": [],
        },
        "story_arc": {
            "opening_tension": "t", "human_or_concrete_story": "s",
            "reversal_or_reveal": "r", "principle_explanation": "p", "viewer_reflection": "v",
        },
        "scenes": scenes,
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


def shot_for(
    sequence: int, asset_id: str, generation_mode: str = "STILL",
    visual_treatment: str = "USE_EXISTING_ART",
) -> dict:
    shot = {
        "sequence": sequence,
        "generation_mode": generation_mode,
        "visual_treatment": visual_treatment,
        "source_asset_ids": [asset_id],
        "shot_goal": "Establish the beat.",
        "frame_composition": "Center the subject.",
        "motion_plan": "Slow push-in.",
        "text_overlay": "",
        "source_support": "Directly grounded in the cited asset.",
        "fade_in_frames": 6,
        "fade_out_frames": 4,
    }
    if generation_mode == "STILL":
        shot["still_motion"] = {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"}
    else:
        shot["still_motion"] = None
    return shot


def visual_plan_for(asset_ids: list[str]) -> dict:
    shots = [shot_for(index + 1, asset_id) for index, asset_id in enumerate(asset_ids)]
    return {
        "produced_by": "visual_agent",
        "overall_visual_style": "Reflective, calm, textured halftone illustrations.",
        "shots": shots,
        "quality_review": {
            "verdict": "APPROVE", "ready_for_generation": True, "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "generation_mode_appropriateness": 9,
                "visual_coherence": 9, "narrative_alignment": 9, "internal_consistency": 9,
            },
        },
    }


def stamped(output: dict, asset_ids: list[str]) -> dict:
    """Persisted shape after `validate_sources` stamps AD-7 fingerprints and
    pydantic serializes the validated contract (matches `model_dump` on disk).
    """
    story_plan = FinalStoryPlanContract.model_validate(final_story_plan_for(asset_ids))
    contract = VisualPlanContract.model_validate(output)
    contract.validate_sources(story_plan)
    return contract.model_dump(mode="json")


def sdk_result(output, *, cost=0.3, is_error=False, subtype="success"):
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
        num_turns=1, session_id="test-session", total_cost_usd=cost,
        structured_output=output,
    )


@pytest.fixture
def stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run, "load_settings", lambda: make_settings())

    def preflight(**kwargs):
        return PreflightResult(passed=True)

    monkeypatch.setattr(run, "run_preflight", preflight)

    async def no_screenshot_stage(source_images_dir, settings, manifest):
        # This file exercises visual_agent/run_visual_stage only; the
        # screenshot and narration stages already have their own test modules.
        return 0

    async def no_narration_stage(settings, manifest):
        return 0

    async def no_voice_stage(settings, manifest):
        # This file exercises visual_agent/run_visual_stage only; voice_agent
        # (the stage that now runs after the visual stage succeeds) is
        # exercised in its own test module.
        no_voice_stage.calls.append((settings, manifest))
        return 0

    async def no_veo_stage(settings, manifest):
        return 0

    async def no_stills_stage(settings, manifest):
        return 0

    async def no_delivery_stage(settings, manifest):
        return 0

    no_voice_stage.calls = []
    monkeypatch.setattr(run, "run_screenshot_stage", no_screenshot_stage)
    monkeypatch.setattr(run, "run_narration_stage", no_narration_stage)
    monkeypatch.setattr(run, "run_voice_stage", no_voice_stage)
    monkeypatch.setattr(run, "run_veo_stage", no_veo_stage)
    monkeypatch.setattr(run, "run_stills_stage", no_stills_stage)
    monkeypatch.setattr(run, "run_delivery_stage", no_delivery_stage)

    analyzed = analyzed_assets_contract_for(ASSET_IDS)
    run.ANALYZED_ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    run.ANALYZED_ASSETS_FILE.write_text(json.dumps(analyzed), encoding="utf-8")

    story_plan = final_story_plan_for(ASSET_IDS)
    run.FINAL_STORY_PLAN_FILE.write_text(json.dumps(story_plan), encoding="utf-8")

    source = tmp_path / "source_images"
    source.mkdir()

    return source, ASSET_IDS, visual_plan_for(ASSET_IDS)


def mock_query(monkeypatch, outputs):
    calls = []

    async def fake_query(*, prompt, options):
        calls.append((prompt, options))
        item = outputs[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        yield item

    monkeypatch.setattr(agent_module, "query", fake_query)
    return calls


def failure_report():
    paths = list(run.RUN_STATE_DIR.glob("failure_visual_agent_*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_fresh_run_writes_via_scoped_agent_and_persists(stage, monkeypatch):
    source, asset_ids, output = stage
    calls = mock_query(monkeypatch, [sdk_result(output)])
    assert run.main([str(source)]) == 0
    options = calls[0][1]
    # Lifted onto the top-level session, not delegated via --agent: same
    # verified reason as story_agent/asset_analyst (--agent silently drops
    # output_format/structured_output).
    assert options.system_prompt == agent_module.visual_agent.prompt
    assert not options.agents
    assert not options.extra_args
    assert options.tools == options.allowed_tools == []
    assert options.permission_mode == "dontAsk"
    assert options.strict_mcp_config
    assert options.output_format["schema"] == VisualPlanContract.model_json_schema()
    assert options.max_budget_usd == 10
    assert options.max_buffer_size == 20 * 1024 * 1024
    assert json.loads(run.VISUAL_PLAN_FILE.read_text()) == stamped(output, asset_ids)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 0.3
    assert manifest.iteration_counts == {"visual_agent": 1}


def test_failed_generation_mode_consistency_self_corrects_with_feedback_and_remaining_budget(stage, monkeypatch):
    source, asset_ids, output = stage
    bad = copy.deepcopy(output)
    bad["shots"][0]["generation_mode"] = "VEO"  # visual_treatment stays USE_EXISTING_ART: invalid (AD-4)
    calls = mock_query(monkeypatch, [sdk_result(bad, cost=1.5), sdk_result(output, cost=0.5)])
    assert run.main([str(source)]) == 0
    assert "VEO" in calls[1][0]
    assert "Previous output" in calls[1][0]
    assert calls[1][1].max_budget_usd == 8.5
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 2
    assert manifest.iteration_counts["visual_agent"] == 2


def test_four_invalid_results_halt_with_diagnostics_and_no_output(stage, monkeypatch):
    source, asset_ids, _ = stage
    calls = mock_query(monkeypatch, [sdk_result({}) for _ in range(4)])
    assert run.main([str(source)]) == 1
    assert len(calls) == 4
    report = failure_report()
    assert report["stage_id"] == "visual_agent"
    assert report["failed_contract_name"] == "VisualPlanContract"
    assert report["attempt_count"] == 4
    assert report["timestamp"]
    assert "metadata/final_story_plan.json" in report["partial_artifact_paths"]
    assert "metadata/analyzed_assets.json" in report["partial_artifact_paths"]
    assert len(report["partial_artifact_paths"]) == 6
    assert not run.VISUAL_PLAN_FILE.exists()
    assert load_run_manifest(run.RUN_STATE_DIR).budget_spent_usd == pytest.approx(1.2)
    assert run.main([str(source)]) == 1
    assert len(calls) == 4


def test_valid_persisted_contract_skips_claude(stage, monkeypatch):
    source, asset_ids, output = stage
    run.VISUAL_PLAN_FILE.write_text(json.dumps(output))
    before = run.VISUAL_PLAN_FILE.read_bytes()
    calls = mock_query(monkeypatch, [])
    for _ in range(2):
        assert run.main([str(source)]) == 0
    assert calls == []
    assert run.VISUAL_PLAN_FILE.read_bytes() == before


@pytest.mark.parametrize("bad_file", ["malformed", "stale_asset_id", "gemini_legacy", "sequence_mismatch"])
def test_invalid_or_stale_persistence_cannot_skip(stage, monkeypatch, bad_file):
    source, asset_ids, output = stage
    bad = copy.deepcopy(output)
    if bad_file == "stale_asset_id":
        # img_bbbbbbbbbbbb is a real asset id in the plan, but belongs to
        # scene 2, not scene 1 -- must still be rejected as stale for shot 1.
        bad["shots"][0]["source_asset_ids"] = [asset_ids[1]]
    if bad_file == "gemini_legacy":
        # A real `visual_director.py` (Gemini) manifest: schema-shaped, but
        # no `produced_by` -- must never satisfy resume (AD-6). It may still
        # carry Story 2.1's real, already-backfilled per-shot timing (as
        # `metadata/visual_plan.json` does) -- a forced regeneration must
        # merge that timing back into the freshly persisted contract, never
        # silently reset it to the SkipJsonSchema defaults.
        del bad["produced_by"]
        for index, shot in enumerate(bad["shots"]):
            shot["start_seconds"] = float(index * 10)
            shot["end_seconds"] = float(index * 10 + 5)
            shot["primary_subtitle_cue_ids"] = [f"cue_{index:03d}"]
            shot["fade_in_frames"] = 8
            shot["fade_out_frames"] = 3
            shot["still_motion"] = {
                "scale_from": 1.0, "scale_to": 1.05, "easing": "linear",
            }
    if bad_file == "sequence_mismatch":
        bad["shots"] = bad["shots"][:-1]  # 2 shots for 3 scenes -- narration changed
    run.VISUAL_PLAN_FILE.write_text("{" if bad_file == "malformed" else json.dumps(bad))
    calls = mock_query(monkeypatch, [sdk_result(output)])
    assert run.main([str(source)]) == 0
    assert len(calls) == 1

    if bad_file == "gemini_legacy":
        persisted = json.loads(run.VISUAL_PLAN_FILE.read_text())
        for index, shot in enumerate(persisted["shots"]):
            assert shot["start_seconds"] == float(index * 10)
            assert shot["end_seconds"] == float(index * 10 + 5)
            assert shot["primary_subtitle_cue_ids"] == [f"cue_{index:03d}"]
            assert shot["fade_in_frames"] == 8
            assert shot["fade_out_frames"] == 3
            assert shot["still_motion"]["scale_to"] == 1.05


def test_no_final_story_plan_contract_prevents_visual_agent(stage, monkeypatch):
    source, asset_ids, _ = stage
    run.FINAL_STORY_PLAN_FILE.unlink()
    calls = mock_query(monkeypatch, [])
    assert run.main([str(source)]) == 1
    assert calls == []
    failure_files = list(run.RUN_STATE_DIR.glob("failure_visual_agent_*.json"))
    assert len(failure_files) == 1


def test_no_analyzed_assets_contract_prevents_visual_agent(stage, monkeypatch):
    source, asset_ids, _ = stage
    run.ANALYZED_ASSETS_FILE.unlink()
    calls = mock_query(monkeypatch, [])
    assert run.main([str(source)]) == 1
    assert calls == []
    failure_files = list(run.RUN_STATE_DIR.glob("failure_visual_agent_*.json"))
    assert len(failure_files) == 1


def test_budget_exhausted_at_stage_halts_before_claude(stage, monkeypatch):
    source, asset_ids, _ = stage
    calls = mock_query(monkeypatch, [])
    save_run_manifest(RunManifest(budget_spent_usd=10), run.RUN_STATE_DIR)
    assert run.main([str(source)]) == 1
    assert calls == []
    assert failure_report()["attempt_count"] == 0


def test_budget_consumed_by_failed_attempt_prevents_next_call(stage, monkeypatch):
    source, asset_ids, _ = stage
    calls = mock_query(monkeypatch, [sdk_result({}, cost=10)])
    assert run.main([str(source)]) == 1
    assert len(calls) == 1
    assert "Budget exhausted" in failure_report()["reason"]


def test_sdk_failure_retries_with_feedback(stage, monkeypatch):
    source, asset_ids, output = stage
    calls = mock_query(monkeypatch, [RuntimeError("transport interrupted"), sdk_result(output)])
    assert run.main([str(source)]) == 0
    assert "transport interrupted" in calls[1][0]


def test_result_without_usable_cost_is_not_accepted(stage, monkeypatch):
    source, asset_ids, output = stage
    mock_query(monkeypatch, [sdk_result(output, cost=None)])
    assert run.main([str(source)]) == 1
    assert not run.VISUAL_PLAN_FILE.exists()
    assert "cost" in failure_report()["reason"]


@pytest.mark.parametrize("mutation", ["missing_field", "bad_verdict", "score_too_low"])
def test_contract_rejects_invalid_quality_plan(stage, mutation):
    _, asset_ids, output = stage
    data = copy.deepcopy(output)
    if mutation == "missing_field":
        del data["overall_visual_style"]
    elif mutation == "bad_verdict":
        data["quality_review"]["verdict"] = "REVISE"
    else:
        data["quality_review"]["scores"]["internal_consistency"] = 5
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)


def test_real_legacy_visual_plan_json_rejected_for_missing_produced_by():
    # Loads the actual real, pre-existing metadata/visual_plan.json (a real
    # visual_director.py/Gemini run, 8 shots) from disk -- not a synthetic
    # legacy-shaped stand-in -- to prove the provenance gate closes this
    # exact artifact, the one this story exists to reject (AD-6).
    real_path = Path(__file__).resolve().parents[2] / "metadata" / "visual_plan.json"
    if not real_path.exists():
        pytest.skip(f"{real_path} not present in this checkout")
    data = json.loads(real_path.read_text(encoding="utf-8"))
    assert "produced_by" not in data
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)
