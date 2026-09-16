from __future__ import annotations

import asyncio
import copy
import importlib
import json

import pytest
from claude_agent_sdk import ResultMessage
from PIL import Image
from pydantic import ValidationError

import orchestrator.run as run
from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.preflight import PreflightResult
from orchestrator.state.run_manifest import RunManifest, load_run_manifest, save_run_manifest
from orchestrator.tests.test_preflight import make_settings
from orchestrator.tools.deterministic_tools import inspect_image

analyst = importlib.import_module("orchestrator.agents.asset_analyst")


def contract_for(assets):
    region = dict(x_min=0, y_min=0, x_max=1000, y_max=1000)
    analysis = {
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
    return {
        "produced_by": "asset_analyst",
        "assets": [
            {**{k: a[k] for k in ("asset_id", "source_path", "original_filename")}, "analysis": copy.deepcopy(analysis)}
            for a in assets
        ],
    }


def sdk_result(output, *, cost=0.2, is_error=False, subtype="success"):
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
    events = []

    def preflight(**kwargs):
        events.append("preflight")
        return PreflightResult(passed=True)

    monkeypatch.setattr(run, "run_preflight", preflight)
    source = tmp_path / "source_images"
    source.mkdir()
    for index in range(6):
        Image.new("RGB", (10 + index, 20), "red").save(source / f"{index}.PNG")
    assets = [inspect_image(p) for p in sorted(source.iterdir())]
    return source, contract_for(assets), events


def mock_query(monkeypatch, outputs):
    calls = []

    async def fake_query(*, prompt, options):
        calls.append((prompt, options))
        item = outputs[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        yield item

    monkeypatch.setattr(analyst, "query", fake_query)
    return calls


def failure_report():
    paths = list(run.RUN_STATE_DIR.glob("failure_asset_analyst_*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_fresh_run_reads_via_scoped_agent_and_persists(stage, monkeypatch):
    source, output, events = stage
    calls = mock_query(monkeypatch, [sdk_result(output)])
    assert run.main([str(source)]) == 0
    assert events == ["preflight"]
    options = calls[0][1]
    # Lifted onto the top-level session, not delegated via --agent: verified
    # live that --agent silently drops output_format/structured_output.
    assert options.system_prompt == analyst.asset_analyst.prompt
    assert not options.agents
    assert not options.extra_args
    assert options.tools == options.allowed_tools == ["Read"]
    assert options.permission_mode == "dontAsk"
    assert options.strict_mcp_config
    assert options.output_format["schema"] == AnalyzedAssetsContract.model_json_schema()
    assert options.max_budget_usd == 10
    assert options.max_buffer_size == 20 * 1024 * 1024
    assert json.loads(run.ANALYZED_ASSETS_FILE.read_text()) == output
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 0.2
    assert manifest.iteration_counts == {"asset_analyst": 1}
    assert manifest.session_id == "test-session"


def test_failed_schema_self_corrects_with_feedback_and_remaining_budget(stage, monkeypatch):
    source, output, _ = stage
    broken = copy.deepcopy(output)
    del broken["assets"][0]["analysis"]["source_text"]
    calls = mock_query(monkeypatch, [sdk_result(broken, cost=1.5), sdk_result(output, cost=0.5)])
    assert run.main([str(source)]) == 0
    assert "source_text" in calls[1][0]
    assert "Previous output" in calls[1][0]
    assert calls[1][1].max_budget_usd == 8.5
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.budget_spent_usd == 2
    assert manifest.iteration_counts["asset_analyst"] == 2


def test_four_invalid_results_halt_with_diagnostics_and_no_output(stage, monkeypatch):
    source, _, _ = stage
    calls = mock_query(monkeypatch, [sdk_result({}) for _ in range(4)])
    assert run.main([str(source)]) == 1
    assert len(calls) == 4
    report = failure_report()
    assert report["stage_id"] == "asset_analyst"
    assert report["failed_contract_name"] == "AnalyzedAssetsContract"
    assert report["attempt_count"] == 4
    assert report["timestamp"]
    assert "metadata/assets.json" in report["partial_artifact_paths"]
    assert len(report["partial_artifact_paths"]) == 5
    assert not run.ANALYZED_ASSETS_FILE.exists()
    assert load_run_manifest(run.RUN_STATE_DIR).budget_spent_usd == pytest.approx(0.8)
    # The ceiling is persisted; restarting cannot purchase another four attempts.
    assert run.main([str(source)]) == 1
    assert len(calls) == 4


def test_valid_persisted_contract_skips_claude_but_still_preflights_and_ingests(stage, monkeypatch):
    source, output, events = stage
    run.ANALYZED_ASSETS_FILE.parent.mkdir()
    run.ANALYZED_ASSETS_FILE.write_text(json.dumps(output))
    before = run.ANALYZED_ASSETS_FILE.read_bytes()
    calls = mock_query(monkeypatch, [])
    for _ in range(2):
        assert run.main([str(source)]) == 0
        assert json.loads(run.ASSETS_FILE.read_text())["asset_count"] == 6
        run.ASSETS_FILE.unlink()
    assert events == ["preflight", "preflight"]
    assert calls == []
    assert run.ANALYZED_ASSETS_FILE.read_bytes() == before


@pytest.mark.parametrize("bad_file", ["malformed", "incomplete", "different_sources", "gemini_legacy"])
def test_invalid_or_stale_persistence_cannot_skip(stage, monkeypatch, bad_file):
    source, output, _ = stage
    bad = copy.deepcopy(output)
    if bad_file == "different_sources":
        bad["assets"][0]["asset_id"] = "img_000000000000"
    if bad_file == "incomplete":
        bad["assets"].pop()
    if bad_file == "gemini_legacy":
        # A real `analyze_assets.py` (Gemini) manifest: full source coverage,
        # schema-shaped, but no `produced_by` -- must never satisfy resume (AD-1).
        del bad["produced_by"]
        for asset in bad["assets"]:
            asset["model"] = "gemini-2.5-flash"
    run.ANALYZED_ASSETS_FILE.parent.mkdir()
    run.ANALYZED_ASSETS_FILE.write_text("{" if bad_file == "malformed" else json.dumps(bad))
    calls = mock_query(monkeypatch, [sdk_result(output)])
    assert run.main([str(source)]) == 0
    assert len(calls) == 1


def test_budget_exhausted_at_stage_halts_before_claude(stage, monkeypatch):
    source, _, _ = stage
    calls = mock_query(monkeypatch, [])
    save_run_manifest(RunManifest(budget_spent_usd=10), run.RUN_STATE_DIR)
    assert run.main([str(source)]) == 1
    assert calls == []
    assert failure_report()["attempt_count"] == 0


def test_budget_consumed_by_failed_attempt_prevents_next_call(stage, monkeypatch):
    source, _, _ = stage
    calls = mock_query(monkeypatch, [sdk_result({}, cost=10)])
    assert run.main([str(source)]) == 1
    assert len(calls) == 1
    assert "Budget exhausted" in failure_report()["reason"]


def test_sdk_budget_stop_halts_even_below_exact_ceiling(stage, monkeypatch):
    source, _, _ = stage
    calls = mock_query(monkeypatch, [sdk_result({}, cost=9.9, is_error=True, subtype="error_max_budget_usd")])
    assert run.main([str(source)]) == 1
    assert len(calls) == 1
    assert "budget" in failure_report()["reason"]


def test_sdk_failure_retries_with_feedback(stage, monkeypatch):
    source, output, _ = stage
    calls = mock_query(monkeypatch, [RuntimeError("transport interrupted"), sdk_result(output)])
    assert run.main([str(source)]) == 0
    assert "transport interrupted" in calls[1][0]


def test_result_without_usable_cost_is_not_accepted(stage, monkeypatch):
    source, output, _ = stage
    mock_query(monkeypatch, [sdk_result(output, cost=None)])
    assert run.main([str(source)]) == 1
    assert not run.ANALYZED_ASSETS_FILE.exists()
    assert "cost" in failure_report()["reason"]


def test_omitted_screenshot_fails_even_when_schema_valid(stage, monkeypatch):
    source, output, _ = stage
    incomplete = copy.deepcopy(output)
    incomplete["assets"].pop()
    calls = mock_query(monkeypatch, [sdk_result(incomplete), sdk_result(output)])
    assert run.main([str(source)]) == 0
    assert "every ingested screenshot" in calls[1][0]


def test_preflight_failure_prevents_ingest_and_claude(stage, monkeypatch):
    source, _, _ = stage
    monkeypatch.setattr(run, "run_preflight", lambda **kw: PreflightResult(False, "credentials", "missing"))
    calls = mock_query(monkeypatch, [])
    assert run.main([str(source)]) == 1
    assert calls == []
    assert not run.ASSETS_FILE.exists()


@pytest.mark.parametrize("mutation", ["missing", "enum", "bool", "region", "confidence"])
def test_nested_contract_rejects_invalid_analysis(stage, mutation):
    _, output, _ = stage
    analysis = output["assets"][0]["analysis"]
    if mutation == "missing":
        del analysis["uncertainties"]
    elif mutation == "enum":
        analysis["possible_story_roles"] = ["INVENTED"]
    elif mutation == "bool":
        analysis["visual"]["real_people_visible"] = "false"
    elif mutation == "region":
        analysis["production"]["story_art_region"]["x_min"] = 1001
    else:
        analysis["source_text"]["transcription_confidence"] = 1.1
    with pytest.raises(ValidationError):
        AnalyzedAssetsContract.model_validate(output)
