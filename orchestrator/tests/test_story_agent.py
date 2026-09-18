from __future__ import annotations

import copy
import importlib
import json

import pytest
from claude_agent_sdk import ResultMessage
from PIL import Image
from pydantic import ValidationError

import orchestrator.run as run
from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.preflight import PreflightResult
from orchestrator.state.run_manifest import RunManifest, load_run_manifest, save_run_manifest
from orchestrator.tests.test_preflight import make_settings
from orchestrator.tools.deterministic_tools import inspect_image

agent_module = importlib.import_module("orchestrator.agents.story_agent")


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


def analyzed_assets_contract_for(assets: list[dict]) -> dict:
    return {
        "produced_by": "asset_analyst",
        "assets": [
            {**{k: a[k] for k in ("asset_id", "source_path", "original_filename")},
             "analysis": analysis_for(a["asset_id"])}
            for a in assets
        ],
    }


def scenes_for(asset_ids: list[str], word_count: int = 100, per_scene: int = 20, duration: float = 9.0):
    words = [f"word{i}" for i in range(word_count)]
    scenes = []
    for i in range(0, word_count, per_scene):
        chunk = words[i:i + per_scene]
        scenes.append({
            "sequence": i // per_scene + 1,
            "role": "SETUP",
            "estimated_duration_seconds": duration,
            "narration": " ".join(chunk),
            "source_asset_ids": [asset_ids[0]],
            "source_support": "Directly supports this beat.",
            "visual_intent": "A calm visual.",
            "suggested_visual_treatment": "USE_EXISTING_ART",
            "impact_text": "",
            "emotional_goal": "calm",
        })
    return " ".join(words), scenes


def final_story_plan_for(asset_ids: list[str]) -> dict:
    narration_script, scenes = scenes_for(asset_ids)
    return {
        "produced_by": "story_agent",
        "story_title": "The Backwards Law",
        "core_thesis": "Acceptance beats striving.",
        "narrative_strategy": "Hook, human story, reveal, explanation, reflection.",
        "target_duration_seconds": 45.0,
        "target_word_count": 100,
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
        "unused_assets": [
            {"asset_id": asset_id, "reason": "Not used in the final cut."}
            for asset_id in asset_ids[1:]
        ],
        "source_integrity_notes": [],
        "quality_review": {
            "verdict": "APPROVE",
            "ready_for_voice_generation": True,
            "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "hook_strength": 9, "spoken_naturalness": 9,
                "narrative_coherence": 9, "voice_alignment": 9, "pacing": 9,
                "scene_structure": 9, "visual_support": 9, "internal_consistency": 9,
            },
        },
    }


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

    source = tmp_path / "source_images"
    source.mkdir()
    for index in range(6):
        Image.new("RGB", (10 + index, 20), "red").save(source / f"{index}.PNG")
    assets = [inspect_image(p) for p in sorted(source.iterdir())]

    analyzed = analyzed_assets_contract_for(assets)
    run.ANALYZED_ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    run.ANALYZED_ASSETS_FILE.write_text(json.dumps(analyzed), encoding="utf-8")

    asset_ids = [a["asset_id"] for a in assets]

    async def no_screenshot_stage(source_images_dir, settings, manifest):
        # This file exercises story_agent/run_narration_stage only; the
        # screenshot stage already has its own test module.
        return 0

    async def no_visual_stage(settings, manifest):
        # Same reasoning: visual_agent (the stage that now runs after
        # narration succeeds) is exercised in its own test module.
        no_visual_stage.calls.append((settings, manifest))
        return 0

    async def no_voice_stage(settings, manifest):
        # Same reasoning, one stage further still: voice_agent is
        # exercised in its own test module.
        no_voice_stage.calls.append((settings, manifest))
        return 0

    async def no_veo_stage(settings, manifest):
        return 0

    async def no_stills_stage(settings, manifest):
        return 0

    async def no_delivery_stage(settings, manifest):
        return 0

    no_visual_stage.calls = []
    no_voice_stage.calls = []
    monkeypatch.setattr(run, "run_screenshot_stage", no_screenshot_stage)
    monkeypatch.setattr(run, "run_visual_stage", no_visual_stage)
    monkeypatch.setattr(run, "run_voice_stage", no_voice_stage)
    monkeypatch.setattr(run, "run_veo_stage", no_veo_stage)
    monkeypatch.setattr(run, "run_stills_stage", no_stills_stage)
    monkeypatch.setattr(run, "run_delivery_stage", no_delivery_stage)

    return source, asset_ids, final_story_plan_for(asset_ids)


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
    paths = list(run.RUN_STATE_DIR.glob("failure_story_agent_*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_fresh_run_writes_via_scoped_agent_and_persists(stage, monkeypatch):
    source, asset_ids, output = stage
    calls = mock_query(monkeypatch, [sdk_result(output)])
    assert run.main([str(source)]) == 0
    options = calls[0][1]
    # Lifted onto the top-level session, not delegated via --agent: verified
    # live (Story 1.3) that --agent silently drops output_format/structured_output.
    assert options.system_prompt == agent_module.story_agent.prompt
    assert not options.agents
    assert not options.extra_args
    assert options.tools == options.allowed_tools == []
    assert options.permission_mode == "dontAsk"
    assert options.strict_mcp_config
    assert options.output_format["schema"] == FinalStoryPlanContract.model_json_schema()
    assert options.max_budget_usd == 10
    assert options.max_buffer_size == 20 * 1024 * 1024
    assert json.loads(run.FINAL_STORY_PLAN_FILE.read_text()) == output
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 0.3
    assert manifest.iteration_counts == {"story_agent": 1}


def test_failed_quality_bar_self_corrects_with_feedback_and_remaining_budget(stage, monkeypatch):
    source, asset_ids, output = stage
    low_score = copy.deepcopy(output)
    low_score["quality_review"]["scores"]["source_fidelity"] = 7
    calls = mock_query(monkeypatch, [sdk_result(low_score, cost=1.5), sdk_result(output, cost=0.5)])
    assert run.main([str(source)]) == 0
    assert "source_fidelity" in calls[1][0]
    assert "Previous output" in calls[1][0]
    assert calls[1][1].max_budget_usd == 8.5
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 2
    assert manifest.iteration_counts["story_agent"] == 2


def test_four_invalid_results_halt_with_diagnostics_and_no_output(stage, monkeypatch):
    source, asset_ids, _ = stage
    calls = mock_query(monkeypatch, [sdk_result({}) for _ in range(4)])
    assert run.main([str(source)]) == 1
    assert len(calls) == 4
    report = failure_report()
    assert report["stage_id"] == "story_agent"
    assert report["failed_contract_name"] == "FinalStoryPlanContract"
    assert report["attempt_count"] == 4
    assert report["timestamp"]
    assert "metadata/analyzed_assets.json" in report["partial_artifact_paths"]
    assert len(report["partial_artifact_paths"]) == 5
    assert not run.FINAL_STORY_PLAN_FILE.exists()
    assert load_run_manifest(run.RUN_STATE_DIR).budget_spent_usd == pytest.approx(1.2)
    assert run.main([str(source)]) == 1
    assert len(calls) == 4


def test_valid_persisted_contract_skips_claude(stage, monkeypatch):
    source, asset_ids, output = stage
    run.FINAL_STORY_PLAN_FILE.write_text(json.dumps(output))
    before = run.FINAL_STORY_PLAN_FILE.read_bytes()
    calls = mock_query(monkeypatch, [])
    for _ in range(2):
        assert run.main([str(source)]) == 0
    assert calls == []
    assert run.FINAL_STORY_PLAN_FILE.read_bytes() == before


@pytest.mark.parametrize("bad_file", ["malformed", "stale_asset_id", "gemini_legacy"])
def test_invalid_or_stale_persistence_cannot_skip(stage, monkeypatch, bad_file):
    source, asset_ids, output = stage
    bad = copy.deepcopy(output)
    if bad_file == "stale_asset_id":
        bad["scenes"][0]["source_asset_ids"] = ["img_000000000000"]
        bad["unused_assets"] = []
    if bad_file == "gemini_legacy":
        # A real `story_quality_loop.py` (Gemini) manifest: schema-shaped,
        # but no `produced_by` -- must never satisfy resume (AD-1).
        del bad["produced_by"]
    run.FINAL_STORY_PLAN_FILE.write_text("{" if bad_file == "malformed" else json.dumps(bad))
    calls = mock_query(monkeypatch, [sdk_result(output)])
    assert run.main([str(source)]) == 0
    assert len(calls) == 1


def test_no_analyzed_assets_contract_prevents_story_agent(stage, monkeypatch):
    source, asset_ids, _ = stage
    run.ANALYZED_ASSETS_FILE.unlink()
    calls = mock_query(monkeypatch, [])
    assert run.main([str(source)]) == 1
    assert calls == []
    failure_files = list(run.RUN_STATE_DIR.glob("failure_story_agent_*.json"))
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
    assert not run.FINAL_STORY_PLAN_FILE.exists()
    assert "cost" in failure_report()["reason"]


@pytest.mark.parametrize("mutation", ["missing_field", "bad_verdict", "score_too_low"])
def test_contract_rejects_invalid_quality_plan(stage, mutation):
    _, asset_ids, output = stage
    data = copy.deepcopy(output)
    if mutation == "missing_field":
        del data["voice_direction"]
    elif mutation == "bad_verdict":
        data["quality_review"]["verdict"] = "REVISE"
    else:
        data["quality_review"]["scores"]["internal_consistency"] = 5
    with pytest.raises(ValidationError):
        FinalStoryPlanContract.model_validate(data)
