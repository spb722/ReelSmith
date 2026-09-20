from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import orchestrator.run as run
from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.preflight import PreflightResult
from orchestrator.state.production_assets import upsert_production_asset
from orchestrator.tests.test_full_chain_resume import (
    approved_still_outcome_for,
    create_audio_file,
    still_visual_plan_for,
    subtitle_cues_for,
)
from orchestrator.tests.test_preflight import make_settings


@pytest.fixture
def delivery_chain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(run, "load_settings", lambda: make_settings())
    monkeypatch.setattr(run, "run_preflight", lambda **kwargs: PreflightResult(passed=True))

    source = tmp_path / "source_images"
    source.mkdir()
    asset_id = "img_aaaaaaaaaaaa"

    return asset_id


def _write_delivery_inputs(asset_id: str, *, include_timing: bool = True) -> VisualPlanContract:
    visual_plan = still_visual_plan_for(asset_id)
    if include_timing:
        visual_plan.shots[0].start_seconds = 0.0
        visual_plan.shots[0].end_seconds = 40.0
        visual_plan.shots[0].primary_subtitle_cue_ids = ["cue_001"]
    run.VISUAL_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    create_audio_file()
    outcome = approved_still_outcome_for(visual_plan.shots[0])
    entry = ProductionAssetEntry.from_still_result(outcome.result, [asset_id])
    upsert_production_asset(entry, path=run.PRODUCTION_ASSETS_FILE)
    return visual_plan


def test_delivery_happy_path_mocks_sync_and_render(delivery_chain, monkeypatch):
    asset_id = delivery_chain
    visual_plan = _write_delivery_inputs(asset_id)
    sync_calls: list[dict] = []
    render_calls: list[dict] = []

    class RecordingSync:
        async def handler(self, args: dict) -> dict:
            sync_calls.append(args)
            return {"content": [{"type": "text", "text": json.dumps({"copied": {}, "shot_assets": {}})}]}

    class RecordingRender:
        async def handler(self, args: dict) -> dict:
            render_calls.append(args)
            output = Path(args["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"mp4")
            return {"content": [{"type": "text", "text": json.dumps({"output_path": str(output)})}]}

    monkeypatch.setattr(run, "sync_remotion_assets", RecordingSync())
    monkeypatch.setattr(run, "render_remotion", RecordingRender())
    monkeypatch.setattr(run, "check_remotion_delivery_toolchain", lambda: None)

    assert asyncio.run(run.run_delivery_stage(make_settings(), run.load_run_manifest(run.RUN_STATE_DIR))) == 0
    assert len(sync_calls) == 1
    assert len(render_calls) == 1
    timeline = json.loads(run.TIMELINE_FILE.read_text(encoding="utf-8"))
    assert len(timeline["shots"]) == len(visual_plan.shots)
    assert sync_calls[0]["timeline_path"] == str(run.TIMELINE_FILE)


def test_delivery_absorbs_inter_shot_cue_gaps_into_previous_shot(delivery_chain, monkeypatch):
    """Cue pauses (7.21 → 7.64) must not halt delivery; previous shot holds through them."""
    asset_id = delivery_chain
    visual_plan = still_visual_plan_for(asset_id, count=2)
    visual_plan.shots[0].start_seconds = 0.0
    visual_plan.shots[0].end_seconds = 7.21
    visual_plan.shots[0].primary_subtitle_cue_ids = ["cue_001"]
    visual_plan.shots[1].start_seconds = 7.64
    visual_plan.shots[1].end_seconds = 18.65
    visual_plan.shots[1].primary_subtitle_cue_ids = ["cue_002"]
    run.VISUAL_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    cues = subtitle_cues_for()
    cues["cues"] = [
        {**cues["cues"][0], "cue_id": "cue_001", "start_seconds": 0.0, "end_seconds": 7.21},
        {**cues["cues"][0], "cue_id": "cue_002", "start_seconds": 7.64, "end_seconds": 18.65},
    ]
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(cues), encoding="utf-8")
    create_audio_file()
    for shot in visual_plan.shots:
        outcome = approved_still_outcome_for(shot)
        entry = ProductionAssetEntry.from_still_result(outcome.result, [asset_id])
        upsert_production_asset(entry, path=run.PRODUCTION_ASSETS_FILE)

    class RecordingSync:
        async def handler(self, args: dict) -> dict:
            return {"content": [{"type": "text", "text": json.dumps({"copied": {}, "shot_assets": {}})}]}

    class RecordingRender:
        async def handler(self, args: dict) -> dict:
            output = Path(args["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"mp4")
            return {"content": [{"type": "text", "text": json.dumps({"output_path": str(output)})}]}

    monkeypatch.setattr(run, "sync_remotion_assets", RecordingSync())
    monkeypatch.setattr(run, "render_remotion", RecordingRender())
    monkeypatch.setattr(run, "check_remotion_delivery_toolchain", lambda: None)

    assert asyncio.run(run.run_delivery_stage(make_settings(), run.load_run_manifest(run.RUN_STATE_DIR))) == 0
    timeline = json.loads(run.TIMELINE_FILE.read_text(encoding="utf-8"))
    assert timeline["shots"][0]["end_seconds"] == 7.64
    assert timeline["shots"][1]["start_seconds"] == 7.64
    # Disk plan stays unchanged so production-asset fingerprints remain valid.
    on_disk = json.loads(run.VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    assert on_disk["shots"][0]["end_seconds"] == 7.21


def test_close_inter_shot_timing_gaps_extends_previous_end():
    visual_plan = still_visual_plan_for("img_aaaaaaaaaaaa", count=3)
    visual_plan.shots[0].start_seconds = 0.0
    visual_plan.shots[0].end_seconds = 7.21
    visual_plan.shots[1].start_seconds = 7.64
    visual_plan.shots[1].end_seconds = 18.65
    visual_plan.shots[2].start_seconds = 19.12
    visual_plan.shots[2].end_seconds = 31.29
    run._close_inter_shot_timing_gaps(visual_plan.shots)
    assert visual_plan.shots[0].end_seconds == 7.64
    assert visual_plan.shots[1].end_seconds == 19.12
    assert visual_plan.shots[2].end_seconds == 31.29


def test_close_inter_shot_timing_gaps_rejects_overlap():
    visual_plan = still_visual_plan_for("img_aaaaaaaaaaaa", count=2)
    visual_plan.shots[0].start_seconds = 0.0
    visual_plan.shots[0].end_seconds = 10.0
    visual_plan.shots[1].start_seconds = 9.0
    visual_plan.shots[1].end_seconds = 18.0
    with pytest.raises(ValueError, match="overlaps"):
        run._close_inter_shot_timing_gaps(visual_plan.shots)


def test_delivery_halts_on_incomplete_production_assets(delivery_chain, monkeypatch):
    asset_id = delivery_chain
    _write_delivery_inputs(asset_id)
    Path(run.PRODUCTION_ASSETS_FILE).unlink()
    monkeypatch.setattr(run, "check_remotion_delivery_toolchain", lambda: None)

    assert asyncio.run(run.run_delivery_stage(make_settings(), run.load_run_manifest(run.RUN_STATE_DIR))) == 1


def test_delivery_halts_when_converter_rejects_unbackfilled_timing(delivery_chain, monkeypatch):
    asset_id = delivery_chain
    visual_plan = _write_delivery_inputs(asset_id, include_timing=True)
    visual_plan.shots[0].start_seconds = 0.0
    visual_plan.shots[0].end_seconds = 0.0
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(run, "check_remotion_delivery_toolchain", lambda: None)

    assert asyncio.run(run.run_delivery_stage(make_settings(), run.load_run_manifest(run.RUN_STATE_DIR))) == 1


def test_delivery_halts_when_remotion_toolchain_missing(delivery_chain, monkeypatch):
    asset_id = delivery_chain
    _write_delivery_inputs(asset_id)
    from orchestrator.preflight import PreflightResult

    monkeypatch.setattr(
        run,
        "check_remotion_delivery_toolchain",
        lambda: PreflightResult(passed=False, failed_check="remotion", reason="npx missing"),
    )

    assert asyncio.run(run.run_delivery_stage(make_settings(), run.load_run_manifest(run.RUN_STATE_DIR))) == 1
