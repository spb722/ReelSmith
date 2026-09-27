"""The UI's status file (ui/CONTRACT.md section 5): `orchestrator_runs/status.json`.

Every stage is replaced by a fake, so nothing here reaches a vendor.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest

import orchestrator.run as run
from orchestrator import cover, progress
from orchestrator.preflight import PreflightResult
from orchestrator.state.run_manifest import load_run_manifest, save_run_manifest
from orchestrator.tests.test_full_chain_resume import chain  # noqa: F401 - fixture
from orchestrator.tests.test_preflight import make_settings


def read_status(project: Path, name: str = "status.json") -> dict:
    return json.loads((project / "orchestrator_runs" / name).read_text(encoding="utf-8"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project whose six stages are fakes that each record their call and spend $0.50."""

    (tmp_path / "source_images").mkdir()
    monkeypatch.setattr(run, "load_settings", lambda: make_settings())
    monkeypatch.setattr(run, "run_preflight", lambda **kwargs: PreflightResult(passed=True))
    monkeypatch.setattr(run, "resolve_character_references", lambda: [])
    calls: list[str] = []

    def fake(name):
        async def stage(*args, **kwargs):
            calls.append(name)
            manifest = args[-1] if name == "screenshots" else args[1]
            manifest.budget_spent_usd += 0.5
            return 0
        return stage

    monkeypatch.setattr(run, "run_screenshot_stage", fake("screenshots"))
    for name in ("narration", "visual", "voice", "production", "delivery"):
        monkeypatch.setattr(run, f"run_{name}_stage", fake(name))
    return tmp_path, calls, monkeypatch


def test_a_finished_run_reads_finished_with_every_step_done(project):
    path, calls, _ = project
    assert run.main(["--project", path.name]) == 0

    status = read_status(path)
    assert status["kind"] == "reel"
    assert status["state"] == "finished"
    assert status["percent"] == 100
    assert [step["state"] for step in status["steps"]] == ["done"] * 6
    assert [step["name"] for step in status["steps"]] == list(progress.STEP_NAMES)
    assert all(step["started_at"] and step["finished_at"] for step in status["steps"])
    assert status["steps"][2]["cost_usd"] == 0.5
    assert status["spent_usd"] == 3.0
    assert status["budget_usd"] == 10.0
    assert status["stop_reason"] is None and status["stop_kind"] is None
    # The ceilings come from config.json.
    assert status["steps"][1]["max_attempts"] == load_run_manifest(path / "orchestrator_runs").attempts_allowed("story_agent")
    # Written atomically: no temp file is left beside it.
    assert [p.name for p in (path / "orchestrator_runs").iterdir() if p.name.endswith(".tmp")] == []
    # Inactive once the run is over, so nothing else writes a status file.
    assert not progress.run_status.active


@pytest.mark.parametrize(("reason_code", "stop_kind"), [
    ("budget_exhausted", "budget"),
    ("vendor_limit", "vendor"),
    ("retry_ceiling", "attempts"),
    ("unclassified", None),
])
def test_a_halt_reads_stopped_with_its_kind_and_failed_step(project, reason_code, stop_kind):
    path, calls, monkeypatch = project

    async def halting_visual_stage(settings, manifest):
        return run._halt("visual_agent", "VisualPlanContract", "it went wrong", reason_code=reason_code)

    monkeypatch.setattr(run, "run_visual_stage", halting_visual_stage)
    assert run.main(["--project", path.name]) == 1

    status = read_status(path)
    assert status["state"] == "stopped"
    assert status["stop_kind"] == stop_kind
    assert status["stop_reason"].startswith("it went wrong")
    assert status["step"] == 3
    assert [step["state"] for step in status["steps"]] == ["done", "done", "failed", "waiting", "waiting", "waiting"]


def test_a_voice_halt_without_a_reason_code_still_reads_as_attempts():
    assert progress.stop_kind_for("Retry ceiling exhausted: bad take", "unclassified") == "attempts"
    assert progress.stop_kind_for("Budget exhausted: $10 of $10 spent", "unclassified") == "budget"


def test_sigterm_reads_as_stopped_by_the_operator(project):
    path, calls, monkeypatch = project
    monkeypatch.setenv("REELSMITH_UI", "1")  # the handler is only installed for UI runs

    async def interrupted_voice_stage(settings, manifest):
        os.kill(os.getpid(), signal.SIGTERM)
        return 0  # pragma: no cover - the signal unwinds first

    monkeypatch.setattr(run, "run_voice_stage", interrupted_voice_stage)
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit):
        run.main(["--project", path.name])
    assert signal.getsignal(signal.SIGTERM) == previous

    status = read_status(path)
    assert status["state"] == "stopped"
    assert status["stop_kind"] == "operator"
    assert status["steps"][3]["state"] == "failed"
    assert "production" not in calls


def test_attempts_and_shots_are_reported(tmp_path):
    status = progress.RunStatus()
    status.begin(tmp_path / "status.json")
    try:
        status.step_started(2, attempt=0, max_attempts=4)
        status.attempt(1)
        assert read_json(tmp_path)["steps"][1]["state"] == "running"
        status.attempt(2)
        step = read_json(tmp_path)["steps"][1]
        assert (step["state"], step["attempt"], step["max_attempts"]) == ("retrying", 2, 4)

        status.step_started(5)
        status.shots_planned([
            {"number": 1, "type": "video", "state": "queued", "max_attempts": 3, "prompt": "a door"},
            {"number": 2, "type": "still", "state": "approved", "preview": "generated/stills/shot_02.png"},
        ])
        status.shot(1, state="making", attempt=1)
        status.shot(1, state="reviewing")
        status.shot(1, state="redo")
        status.shot(1, state="approved", preview="generated/veo/shot_01.mp4")
        shots = read_json(tmp_path)["shots"]
        assert shots[0] == {
            "number": 1, "type": "video", "state": "approved", "attempt": 1, "max_attempts": 3,
            "preview": "generated/veo/shot_01.mp4", "prompt": "a door",
        }
        assert shots[1]["preview"] == "generated/stills/shot_02.png"
    finally:
        status.close(0)


def read_json(folder: Path) -> dict:
    return json.loads((folder / "status.json").read_text(encoding="utf-8"))


def test_an_inactive_status_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    status = progress.RunStatus()
    status.step_started(1)
    status.shot(1, state="making")
    status.halt("nope")
    assert list(tmp_path.iterdir()) == []


def test_shot_production_reports_each_shot(chain, monkeypatch, tmp_path):
    """The real production step with a fake Veo agent: each shot's states, in order."""

    import asyncio

    from orchestrator.tests.test_full_chain_resume import (
        approved_outcome_for, failed_outcome_for, sdk_result, subtitle_cues_for, veo_visual_plan_for,
    )

    source, asset_id = chain
    visual_plan = veo_visual_plan_for(asset_id, count=2)
    run.VISUAL_PLAN_FILE.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    tries = {1: 0, 2: 0}

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        tries[sequence] += 1
        shot = visual_plan.shots[sequence - 1]
        if sequence == 1 and tries[1] == 1:
            outcome = failed_outcome_for(shot, retryable=True)
        else:
            outcome = approved_outcome_for(shot, attempt=kwargs["attempt"])
        return sdk_result(outcome.model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    seen: dict[int, list[str]] = {1: [], 2: []}
    real_shot = progress.run_status.shot

    def recording_shot(number, **fields):
        if "state" in fields:
            seen[number].append(fields["state"])
        real_shot(number, **fields)

    monkeypatch.setattr(progress.run_status, "shot", recording_shot)
    progress.run_status.begin(tmp_path / "ui_status" / "status.json")
    try:
        manifest = load_run_manifest(run.RUN_STATE_DIR)
        assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 0
        status = json.loads((tmp_path / "ui_status" / "status.json").read_text(encoding="utf-8"))
    finally:
        progress.run_status.close(0)

    assert seen[1] == ["making", "redo", "making", "reviewing", "approved"]
    assert seen[2] == ["making", "reviewing", "approved"]
    assert [(s["number"], s["type"], s["state"], s["attempt"], s["max_attempts"]) for s in status["shots"]] == [
        (1, "video", "approved", 2, 3), (2, "video", "approved", 1, 3),
    ]
    assert status["shots"][0]["preview"] == "generated/veo/shot_01.mp4"
    assert status["shots"][0]["prompt"] == "Show beat 1."


def test_cover_run_writes_cover_status(tmp_path, monkeypatch):
    (tmp_path / "source_images").mkdir()
    monkeypatch.setattr(cover, "load_settings", lambda: make_settings())
    assert cover.main(["--project", tmp_path.name]) == 1  # no reel yet

    status = read_status(tmp_path, "cover_status.json")
    assert status["kind"] == "cover"
    assert status["state"] == "stopped"
    assert "make the reel first" in status["stop_reason"]
    assert "percent" in status
