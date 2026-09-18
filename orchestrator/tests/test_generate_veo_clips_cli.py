"""Exercises generate_veo_clips.py's own main() end to end (mocked paid
call). Story 2.3 review fix #1: no test ever called main() directly, which is
exactly how the missing --approve-clip flag (main() always raising
RuntimeError after a real successful paid Veo generation) shipped unnoticed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import generate_veo_clips as cli
from orchestrator.contracts.veo import (
    VeoOutcomeContract,
    VeoResultContract,
    VeoSeedContract,
    sha256_file,
    shot_fingerprint,
)
from orchestrator.contracts.visual_plan import QualityReview, Shot, VisualPlanContract
from orchestrator.tests.test_preflight import make_settings


@pytest.fixture(autouse=True)
def working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _shot() -> Shot:
    return Shot(
        sequence=1,
        generation_mode="VEO",
        visual_treatment="AI_VIDEO_CANDIDATE",
        source_asset_ids=["img_aaaaaaaaaaaa"],
        shot_goal="Show the scene.",
        frame_composition="Center the subject.",
        motion_plan="Use quiet motion.",
        text_overlay="",
        source_support="Directly supported.",
    )


def _write_visual_plan(shot: Shot) -> None:
    plan = VisualPlanContract(
        produced_by="visual_agent",
        overall_visual_style="Calm, restrained illustration.",
        shots=[shot],
        quality_review=QualityReview(
            verdict="APPROVE",
            ready_for_generation=True,
            confidence=0.9,
            scores={
                "source_fidelity": 9, "generation_mode_appropriateness": 9,
                "visual_coherence": 9, "narrative_alignment": 9, "internal_consistency": 9,
            },
        ),
    )
    cli.VISUAL_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    cli.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")


def _write_approved_seed(shot: Shot) -> VeoSeedContract:
    source = Path("source_images/source.png")
    seed_path = Path("generated/veo_seeds") / f"shot_{shot.sequence:02d}_seed.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    seed_path.write_bytes(b"seed")
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
    payload = seed.model_dump(mode="json")
    payload["model_text_response"] = ""
    cli.SEED_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    cli.SEED_RESULTS_FILE.write_text(json.dumps({"results": [payload]}), encoding="utf-8")
    return seed


def _fake_unapproved_outcome(shot: Shot, seed: VeoSeedContract) -> VeoOutcomeContract:
    video_path = Path("generated/veo") / f"shot_{shot.sequence:02d}.mp4"
    dump_path = Path("metadata/veo_operations") / f"shot_{shot.sequence:02d}_attempt_01.json"
    preview_path = Path("generated/veo/previews") / f"shot_{shot.sequence:02d}_01.jpg"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    dump_path.write_text("{}")
    preview_path.write_bytes(b"preview")
    result = VeoResultContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        seed=seed,
        local_video_path=str(video_path),
        video_sha256=sha256_file(video_path),
        gcs_uri="gs://bucket/shot-1.mp4",
        operation_name="operations/1",
        operation_dump_path=str(dump_path),
        preview_paths=[str(preview_path)],
        model="veo-model",
        approved=False,
        qa_summary="",
        cost_usd=1.2,
    )
    return VeoOutcomeContract(
        produced_by="veo_tool",
        status="SUCCESS",
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        result=result,
    )


def test_main_requires_approve_clip_flag_to_reach_success_after_paid_generation(monkeypatch):
    shot = _shot()
    _write_visual_plan(shot)
    seed = _write_approved_seed(shot)

    monkeypatch.setattr(cli, "load_settings", lambda: make_settings())
    monkeypatch.setattr(cli.genai, "Client", lambda **kwargs: object())
    monkeypatch.setattr(cli, "run_veo_generation", lambda client, **kwargs: _fake_unapproved_outcome(shot, seed))

    monkeypatch.setattr(sys, "argv", ["generate_veo_clips.py", "--shot", "1"])
    with pytest.raises(RuntimeError, match="explicit semantic QA approval"):
        cli.main()

    results = json.loads(cli.RESULTS_FILE.read_text(encoding="utf-8"))
    assert results["outcomes"]["1"]["result"]["approved"] is False
    assert cli.STATUS_FILE.exists() is False

    monkeypatch.setattr(sys, "argv", ["generate_veo_clips.py", "--shot", "1", "--approve-clip"])
    cli.main()

    results = json.loads(cli.RESULTS_FILE.read_text(encoding="utf-8"))
    assert results["outcomes"]["1"]["result"]["approved"] is True
    assert results["outcomes"]["1"]["result"]["qa_summary"]
    assert cli.STATUS_FILE.read_text(encoding="utf-8").startswith("SUCCESS")


def test_approve_clip_without_prior_results_raises():
    with pytest.raises(ValueError, match="No Veo generation results"):
        cli._approve_selected_clip(1)


def test_approve_selected_seed_rejects_ambiguous_duplicate_shot_sequence():
    shot = _shot()
    seed = _write_approved_seed(shot)
    duplicate = seed.model_dump(mode="json")
    duplicate["approved"] = False
    duplicate["qa_summary"] = ""
    data = json.loads(cli.SEED_RESULTS_FILE.read_text(encoding="utf-8"))
    data["results"].append(duplicate)
    cli.SEED_RESULTS_FILE.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="Multiple contracted seed results"):
        cli._approve_selected_seed(1)


def test_main_rejects_default_gcs_bucket_before_paid_call(monkeypatch):
    shot = _shot()
    _write_visual_plan(shot)
    _write_approved_seed(shot)

    monkeypatch.setattr(cli, "load_settings", lambda: make_settings(gcs_bucket_uri=""))
    monkeypatch.setattr(sys, "argv", ["generate_veo_clips.py", "--shot", "1"])

    with pytest.raises(ValueError, match="GCS_BUCKET_URI must be explicitly configured"):
        cli.main()
