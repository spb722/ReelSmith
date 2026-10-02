"""Story 4.3: per-attempt budget reservations and concurrency-safe shot plumbing.

No live vendor calls. Agents are fakes that drive the paid-tool guard the way
the CLI would; vendor clients and the Codex subprocess are fakes too. Shots
still run one at a time; the concurrency tests call the offloaded tool
handlers side by side directly. Every fake delay is short, and the tests wait
on events and peak counters rather than timing thresholds where they can.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import orchestrator.run as run
from orchestrator.agents._guard import STILL_TOOL, VEO_CLIP_TOOL, VEO_SEED_TOOL
from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.settings import DEFAULT_CLAUDE_SHOT_ATTEMPT_CAP_USD, SettingsError, load_settings
from orchestrator.state import production_assets as production_assets_module
from orchestrator.state.budget_ledger import BudgetLedger, run_ledger
from orchestrator.state.production_assets import load_production_assets, upsert_production_asset
from orchestrator.state.run_manifest import RunManifest, load_run_manifest, save_run_manifest
from orchestrator.tests.test_full_chain_resume import (  # noqa: F401 -- `chain` is a fixture
    approved_outcome_for,
    approved_still_outcome_for,
    chain,
    failed_outcome_for,
    still_visual_plan_for,
    subtitle_cues_for,
    veo_visual_plan_for,
)
from orchestrator.tests.test_gemini_tools import (
    FakeCandidate,
    FakeContent,
    FakeInlineData,
    FakePart,
    FakeResponse,
    _png_bytes,
    asset_for,
    shot_dict,
)
from orchestrator.tests.test_guard_hooks import SEED_OK, call_paid, clip_failure, reports, result_message
from orchestrator.tests.test_preflight import make_settings as _make_settings
from orchestrator.tests.test_veo_tools import seed_for, shot_for
from orchestrator.tools import codex_image_tools, gemini_tools, veo_tools, vendor_limits

# Pinned here, not taken from the shipped default, so the arithmetic below
# doesn't move whenever that default is retuned.
CAP = 1.0


def make_settings(**overrides):
    return _make_settings(**{"claude_shot_attempt_cap_usd": CAP, **overrides})

VEO_RESERVATION = CAP + 0.04 + 1.2
STILL_RESERVATION = CAP + 0.04


def write_plan(plan: VisualPlanContract) -> None:
    run.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")


def plan_modes() -> dict[int, str]:
    plan = VisualPlanContract.model_validate_json(run.VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    return {shot.sequence: shot.generation_mode for shot in plan.shots}


def refuse_agent(label: str):
    async def agent(**kwargs):
        raise AssertionError(f"{label} must not run: nothing may be paid for")

    return agent


# --- settings ----------------------------------------------------------------

def test_new_settings_have_defaults_and_env_overrides(monkeypatch):
    for name in ("CLAUDE_SHOT_ATTEMPT_CAP_USD", "VEO_CONCURRENCY", "GEMINI_IMAGE_CONCURRENCY",
                 "CODEX_IMAGE_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert (settings.claude_shot_attempt_cap_usd, settings.veo_concurrency,
            settings.gemini_image_concurrency, settings.codex_image_concurrency) == (
        DEFAULT_CLAUDE_SHOT_ATTEMPT_CAP_USD, 3, 2, 1)

    monkeypatch.setenv("CLAUDE_SHOT_ATTEMPT_CAP_USD", "0.75")
    monkeypatch.setenv("VEO_CONCURRENCY", "2")
    monkeypatch.setenv("GEMINI_IMAGE_CONCURRENCY", "4")
    monkeypatch.setenv("CODEX_IMAGE_CONCURRENCY", "2")
    settings = load_settings()
    assert (settings.claude_shot_attempt_cap_usd, settings.veo_concurrency,
            settings.gemini_image_concurrency, settings.codex_image_concurrency) == (0.75, 2, 4, 2)


@pytest.mark.parametrize("name,value", [
    ("CLAUDE_SHOT_ATTEMPT_CAP_USD", "0"),
    ("VEO_CONCURRENCY", "0"),
    ("GEMINI_IMAGE_CONCURRENCY", "two"),
    ("CODEX_IMAGE_CONCURRENCY", "-1"),
])
def test_new_settings_reject_unusable_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError):
        load_settings()


# --- reservation arithmetic ----------------------------------------------------

def test_an_attempt_reserves_the_claude_cap_plus_its_paid_calls():
    settings = make_settings(claude_shot_attempt_cap_usd=0.7)
    assert run.shot_attempt_reservation(settings, "VEO") == pytest.approx((0.7, 0.04 + 1.2))
    assert run.shot_attempt_reservation(settings, "STILL") == pytest.approx((0.7, 0.04))


def test_ledger_reserves_debits_and_releases(tmp_path):
    manifest = RunManifest(budget_spent_usd=1.0)
    ledger = BudgetLedger(max_budget_usd=5.0, manifest=manifest, state_dir=tmp_path)
    assert ledger.unreserved_usd() == pytest.approx(4.0)

    veo = ledger.reserve(label="veo", claude_cap_usd=1.0, external_usd=1.24)
    assert veo.amount_usd == pytest.approx(2.24)
    assert veo.paid_tool_allowance_usd == pytest.approx(1.24)
    assert ledger.unreserved_usd() == pytest.approx(4.0 - 2.24)
    # A second one no longer fits; nothing is held for the refusal.
    assert ledger.reserve(label="too much", claude_cap_usd=1.0, external_usd=1.24) is None
    assert ledger.unreserved_usd() == pytest.approx(4.0 - 2.24)

    # Debiting inside the reservation moves spend, not the pool.
    ledger.debit(veo, 1.5)
    assert manifest.budget_spent_usd == pytest.approx(2.5)
    assert veo.unspent_usd == pytest.approx(0.74)
    assert ledger.unreserved_usd() == pytest.approx(4.0 - 2.24)
    # Releasing hands the unspent part back.
    ledger.release(veo)
    ledger.release(veo)  # idempotent
    assert ledger.unreserved_usd() == pytest.approx(2.5)
    # The debit is durable: it went through a manifest save.
    assert json.loads((tmp_path / "config.json").read_text())["usage"]["budget_spent_usd"] == pytest.approx(2.5)


def test_equal_reservations_are_released_one_by_one(tmp_path):
    ledger = BudgetLedger(max_budget_usd=10.0, manifest=RunManifest(), state_dir=tmp_path)
    first = ledger.reserve(label="same", claude_cap_usd=1.0, external_usd=0.0)
    second = ledger.reserve(label="same", claude_cap_usd=1.0, external_usd=0.0)
    ledger.release(second)
    assert first.open and not second.open
    assert ledger.unreserved_usd() == pytest.approx(9.0)
    ledger.release(first)
    assert ledger.unreserved_usd() == pytest.approx(10.0)


def test_concurrent_reservations_never_over_commit_the_pool(tmp_path):
    ledger = BudgetLedger(max_budget_usd=10.0, manifest=RunManifest(), state_dir=tmp_path)
    barrier = threading.Barrier(20)
    granted = []

    def take():
        barrier.wait()
        reservation = ledger.reserve(label="r", claude_cap_usd=1.0, external_usd=0.0)
        if reservation is not None:
            granted.append(reservation)

    threads = [threading.Thread(target=take) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == 10
    assert ledger.unreserved_usd() == pytest.approx(0.0)


def test_planning_and_the_shot_stages_share_one_ledger_per_run():
    manifest = RunManifest()
    ledger = run_ledger(manifest, max_budget_usd=10.0)
    assert run_ledger(manifest, max_budget_usd=10.0) is ledger
    assert run_ledger(RunManifest(), max_budget_usd=10.0) is not ledger


def test_an_exact_fit_is_reserved(tmp_path):
    ledger = BudgetLedger(max_budget_usd=0.1 + 0.2, manifest=RunManifest(), state_dir=tmp_path)
    assert ledger.reserve(label="exact", claude_cap_usd=0.1, external_usd=0.2) is not None


def test_an_overshoot_comes_from_the_pool_and_a_negative_pool_is_overdrawn(tmp_path):
    manifest = RunManifest()
    ledger = BudgetLedger(max_budget_usd=2.0, manifest=manifest, state_dir=tmp_path)
    still = ledger.reserve(label="still", claude_cap_usd=1.0, external_usd=0.04)
    ledger.debit(still, 1.5)  # one Claude turn past the cap
    assert still.unspent_usd == 0.0
    assert ledger.unreserved_usd() == pytest.approx(0.5)
    assert not ledger.overdrawn()
    ledger.debit(still, 0.6)
    assert ledger.overdrawn()


# --- pre-launch demotion ------------------------------------------------------

def test_demotion_order_is_by_rank_then_unranked_first_then_later_shot():
    settings = make_settings()
    shots = [
        SimpleNamespace(sequence=1, generation_mode="VEO", video_candidate_rank=3),
        SimpleNamespace(sequence=2, generation_mode="VEO", video_candidate_rank=1),
        SimpleNamespace(sequence=3, generation_mode="STILL", video_candidate_rank=None),
        SimpleNamespace(sequence=4, generation_mode="VEO", video_candidate_rank=None),
        SimpleNamespace(sequence=5, generation_mode="VEO", video_candidate_rank=2),
    ]
    # Everything fits: nothing is demoted.
    assert run.plan_first_attempt_demotions(shots, settings, 100.0)[0] == []
    # Nothing fits, even as stills: every Veo shot is demoted, in order.
    demoted, needed = run.plan_first_attempt_demotions(shots, settings, 0.0)
    assert demoted == [4, 1, 5, 2]
    assert needed == pytest.approx(5 * STILL_RESERVATION)
    # Room for exactly one video: only the best-ranked shot keeps it.
    demoted, needed = run.plan_first_attempt_demotions(shots, settings, VEO_RESERVATION + 4 * STILL_RESERVATION)
    assert demoted == [4, 1, 5]
    assert needed == pytest.approx(VEO_RESERVATION + 4 * STILL_RESERVATION)


def test_planning_demotes_the_lowest_ranked_veo_shots_before_any_paid_call(chain, monkeypatch):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id, count=3)
    # Ranks deliberately out of sequence order: rank, not position, decides.
    for shot, rank in zip(plan.shots, (3, 1, 2)):
        shot.video_candidate_rank = rank
    write_plan(plan)
    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent("veo_agent"))
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # One video plus two stills fit; two videos plus a still don't.
    settings = make_settings(max_budget_usd=VEO_RESERVATION + 2 * STILL_RESERVATION + 0.1)

    assert asyncio.run(run.run_production_planning(settings, manifest)) == 0
    assert plan_modes() == {1: "STILL", 2: "VEO", 3: "STILL"}
    after = VisualPlanContract.model_validate_json(run.VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    assert all(shot.still_motion is not None for shot in after.shots)
    assert manifest.budget_spent_usd == 0.0

    # Deterministic and idempotent: planning again changes nothing.
    before = run.VISUAL_PLAN_FILE.read_text(encoding="utf-8")
    assert asyncio.run(run.run_production_planning(settings, manifest)) == 0
    assert run.VISUAL_PLAN_FILE.read_text(encoding="utf-8") == before


def test_a_budget_demotion_warns_loudly_with_what_keeping_the_videos_needs(chain, capsys):
    _, asset_id = chain
    write_plan(veo_visual_plan_for(asset_id, count=5))
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    budget = 2 * VEO_RESERVATION + 3 * STILL_RESERVATION + 0.1
    assert asyncio.run(run.run_production_planning(make_settings(max_budget_usd=budget), manifest)) == 0
    assert list(plan_modes().values()).count("VEO") == 2
    err = capsys.readouterr().err
    assert err.count("BUDGET: 3 video shot(s) were turned into still pictures") == 1
    assert f"about ${5 * VEO_RESERVATION:.2f} for the first tries, but only ${budget:.2f}" in err
    assert "Raise MAX_BUDGET_USD (per project in the UI) and redo the shot plan" in err

    # Nothing demoted, nothing to warn about.
    write_plan(veo_visual_plan_for(asset_id, count=5))
    assert asyncio.run(run.run_production_planning(make_settings(max_budget_usd=40.0), manifest)) == 0
    assert "BUDGET:" not in capsys.readouterr().err


def test_planning_never_demotes_a_veo_shot_that_already_has_attempts(chain):
    _, asset_id = chain
    write_plan(veo_visual_plan_for(asset_id, count=2))
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Shot 2 (the lower rank) already tried as a video in an earlier run.
    manifest.iteration_counts["veo_agent_shot_2"] = 3
    settings = make_settings(max_budget_usd=VEO_RESERVATION + STILL_RESERVATION + 0.1)
    assert asyncio.run(run.run_production_planning(settings, manifest)) == 0
    assert plan_modes() == {1: "STILL", 2: "VEO"}


def test_planning_leaves_the_plan_alone_when_every_first_attempt_fits(chain):
    _, asset_id = chain
    write_plan(veo_visual_plan_for(asset_id, count=2))
    before = run.VISUAL_PLAN_FILE.read_text(encoding="utf-8")
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_planning(make_settings(), manifest)) == 0
    assert run.VISUAL_PLAN_FILE.read_text(encoding="utf-8") == before


def test_planning_skips_shots_already_made(chain):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id, count=2)
    write_plan(plan)
    entry = ProductionAssetEntry.from_veo_result(
        approved_outcome_for(plan.shots[0]).result, source_asset_ids=plan.shots[0].source_asset_ids,
    )
    upsert_production_asset(entry, path=run.PRODUCTION_ASSETS_FILE)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Only shot 2 is pending, and one Veo reservation fits exactly.
    assert asyncio.run(run.run_production_planning(make_settings(max_budget_usd=VEO_RESERVATION), manifest)) == 0
    assert plan_modes() == {1: "VEO", 2: "VEO"}


def test_planning_halts_before_any_paid_call_when_even_stills_cannot_fit(chain, monkeypatch):
    _, asset_id = chain
    write_plan(veo_visual_plan_for(asset_id, count=3))
    before = run.VISUAL_PLAN_FILE.read_text(encoding="utf-8")
    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent("veo_agent"))
    monkeypatch.setattr(run, "generate_still_asset", refuse_agent("stills_agent"))
    manifest = load_run_manifest(run.RUN_STATE_DIR)

    # The veo stage is where shot production begins, so it plans first.
    settings = make_settings(max_budget_usd=3 * STILL_RESERVATION - 0.01)
    assert asyncio.run(run.run_veo_stage(settings, manifest)) == 1
    assert run.VISUAL_PLAN_FILE.read_text(encoding="utf-8") == before
    assert manifest.budget_spent_usd == 0.0
    [report] = reports("production_planning")
    assert (report["reason_code"], report["resumable"]) == ("budget_exhausted", True)
    assert "MAX_BUDGET_USD" in report["reason"]


# --- Veo extend: an extended shot reserves and is priced at two clips ----------

EXTENDED_RESERVATION = CAP + 0.04 + 2 * 1.2


def timed_veo_plan(asset_id: str, durations: list[float]) -> VisualPlanContract:
    plan = veo_visual_plan_for(asset_id, count=len(durations))
    start = 0.0
    for shot, duration in zip(plan.shots, durations):
        shot.start_seconds, shot.end_seconds = round(start, 2), round(start + duration, 2)
        start += duration
    return plan


def test_an_extended_shot_reserves_two_veo_calls():
    settings = make_settings(claude_shot_attempt_cap_usd=0.7)
    assert run.shot_attempt_reservation(settings, "VEO", 2)[1] == pytest.approx(0.04 + 2 * 1.2)
    assert run.shot_attempt_reservation(settings, "VEO") == pytest.approx((0.7, 0.04 + 1.2))


def test_veo_calls_for_shot():
    on = make_settings(veo_extend_long_shots=True)
    long_veo = SimpleNamespace(generation_mode="VEO", start_seconds=0.0, end_seconds=10.84)
    assert run.veo_calls_for_shot(long_veo, on) == 2
    assert run.veo_calls_for_shot(long_veo, make_settings()) == 1
    assert run.veo_calls_for_shot(SimpleNamespace(**{**vars(long_veo), "generation_mode": "STILL"}), on) == 1
    assert run.veo_calls_for_shot(SimpleNamespace(generation_mode="VEO", start_seconds=0.0, end_seconds=6.0), on) == 1
    assert run.veo_calls_for_shot(long_veo, make_settings(veo_extend_long_shots=True, veo_resolution="1080p")) == 1
    assert run.veo_calls_for_shot(SimpleNamespace(sequence=1, generation_mode="VEO"), on) == 1


def test_demotion_prices_extended_shots_at_two_calls():
    shots = [
        SimpleNamespace(sequence=1, generation_mode="VEO", video_candidate_rank=2, start_seconds=0.0, end_seconds=10.84),
        SimpleNamespace(sequence=2, generation_mode="VEO", video_candidate_rank=1, start_seconds=10.84, end_seconds=16.84),
    ]
    budget = 2 * VEO_RESERVATION + 0.1
    # Off: the long shot is priced as one clip, as today, and both fit.
    assert run.plan_first_attempt_demotions(shots, make_settings(), budget) == ([], pytest.approx(2 * VEO_RESERVATION))
    # On: one normal plus one extended shot does not fit, so the lower rank goes.
    on = make_settings(veo_extend_long_shots=True)
    assert run.plan_first_attempt_demotions(shots, on, 2 * VEO_RESERVATION + 1.3)[0] == []
    demoted, needed = run.plan_first_attempt_demotions(shots, on, budget)
    assert demoted == [1]
    assert needed == pytest.approx(STILL_RESERVATION + VEO_RESERVATION)
    assert needed == pytest.approx(
        sum(run.shot_attempt_reservation(on, "STILL")) + sum(run.shot_attempt_reservation(on, "VEO"))
    )


def counted_writes(monkeypatch) -> list:
    writes = []
    real = run._atomic_write_json

    def write(path, data):
        writes.append(Path(path))
        real(path, data)

    monkeypatch.setattr(run, "_atomic_write_json", write)
    return writes


@pytest.mark.parametrize("overrides,why", [
    ({}, "VEO_EXTEND_LONG_SHOTS is now off"),
    ({"veo_extend_long_shots": True, "veo_resolution": "1080p"}, "only used at 720p, not 1080p"),
])
def test_planning_turns_extended_shots_into_stills_when_the_setting_is_now_off(
    chain, monkeypatch, capsys, overrides, why,
):
    _, asset_id = chain
    write_plan(timed_veo_plan(asset_id, [6.0, 10.84]))
    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent("veo_agent"))
    writes = counted_writes(monkeypatch)
    manifest = load_run_manifest(run.RUN_STATE_DIR)

    assert asyncio.run(run.run_production_planning(make_settings(**overrides), manifest)) == 0
    assert plan_modes() == {1: "VEO", 2: "STILL"}
    assert writes == [run.VISUAL_PLAN_FILE]
    assert manifest.budget_spent_usd == 0.0
    err = capsys.readouterr().err
    assert err.count("Shot 2: switched from video to a still picture — it is 10.84s, which needs an 8s "
                     "clip plus a 7s extension, and ") == 1
    assert why in err
    assert "BUDGET:" not in err


def test_planning_with_setting_off_leaves_todays_plans_byte_identical(chain, monkeypatch, capsys):
    _, asset_id = chain
    write_plan(timed_veo_plan(asset_id, [6.0, 8.0]))
    before = run.VISUAL_PLAN_FILE.read_bytes()
    mtime = run.VISUAL_PLAN_FILE.stat().st_mtime_ns
    writes = counted_writes(monkeypatch)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    capsys.readouterr()

    assert asyncio.run(run.run_production_planning(make_settings(), manifest)) == 0
    assert run.VISUAL_PLAN_FILE.read_bytes() == before
    assert run.VISUAL_PLAN_FILE.stat().st_mtime_ns == mtime
    assert writes == []
    captured = capsys.readouterr()
    assert captured.err == ""
    [line] = captured.out.splitlines()
    assert line.endswith(f"The first try of 2 shot(s) needs ${2 * VEO_RESERVATION:.2f} of the $10.00 left")


# --- per-attempt reservations in the shot loops --------------------------------

def test_a_retry_that_cannot_reserve_halts_budget_exhausted_without_demoting(chain, monkeypatch):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    shot = plan.shots[0]
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        return result_message(failed_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.3)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # The first attempt fits; after it costs 0.3 + 1.24, a second one doesn't.
    assert asyncio.run(run.run_veo_stage(make_settings(max_budget_usd=3.0), manifest)) == 1

    assert calls == [1]
    assert manifest.budget_spent_usd == pytest.approx(0.3 + 1.24)
    assert manifest.iteration_counts["veo_agent_shot_1"] == 1
    assert plan_modes() == {1: "VEO"}  # no mid-run demotion
    [report] = reports("veo_agent")
    assert (report["reason_code"], report["resumable"]) == ("budget_exhausted", True)
    assert "try 2 needs $2.2400 reserved" in report["reason"]


def test_a_later_shot_whose_first_attempt_cannot_reserve_halts_too(chain, monkeypatch):
    _, asset_id = chain
    plan = still_visual_plan_for(asset_id, count=2)
    write_plan(plan)
    shot_one = plan.shots[0]
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["shot"]["sequence"])
        # Shot 1 spends its whole reservation ($1.00 + $0.04), so the $0.96
        # left can't hold shot 2's first attempt.
        return result_message(approved_still_outcome_for(shot_one).model_dump(mode="json"), cost=1.0)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(max_budget_usd=2.0), manifest)) == 1
    assert calls == [1]
    assert set(load_production_assets(run.PRODUCTION_ASSETS_FILE).shots) == {"1"}
    [report] = reports("stills_agent")
    assert report["reason_code"] == "budget_exhausted"


def test_shot_queries_get_the_claude_cap_and_the_guard_gets_the_reservation(chain, monkeypatch):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    shot = plan.shots[0]
    seen = {}

    async def agent(**kwargs):
        guard = kwargs["guard"]
        seen["max_budget_usd"] = kwargs["max_budget_usd"]
        seen["limit_usd"] = guard.limit_usd
        assert await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        assert await call_paid(guard, VEO_CLIP_TOOL, {"status": "SUCCESS"})
        # The run has $10 left, but this attempt reserved one clip: a second is denied.
        seen["second_clip"] = await call_paid(guard, VEO_CLIP_TOOL, {"status": "SUCCESS"})
        return result_message(approved_outcome_for(shot).model_dump(mode="json"), cost=0.2)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 1
    assert seen == {"max_budget_usd": CAP, "limit_usd": pytest.approx(0.04 + 1.2), "second_clip": False}
    [report] = reports("veo_agent")
    assert report["reason_code"] == "budget_exhausted"
    assert not run.PRODUCTION_ASSETS_FILE.exists()


@pytest.mark.parametrize("stage,code,veo_calls_charged", [
    (None, None, 2),
    ("veo_generation", "EXTENSION_OPERATION_ERROR", 2),
    ("seed_qa", "SEED_REJECTED", 0),
])
def test_extended_attempt_guard_and_ledger_charge_two_clips(chain, monkeypatch, stage, code, veo_calls_charged):
    _, asset_id = chain
    plan = timed_veo_plan(asset_id, [10.84])
    write_plan(plan)
    shot = plan.shots[0]
    seen = {}

    async def agent(**kwargs):
        guard = kwargs["guard"]
        seen["costs"] = guard.costs
        seen["limit_usd"] = guard.limit_usd
        if stage is None:
            outcome = approved_outcome_for(shot)
        else:
            outcome = failed_outcome_for(shot, retryable=False)
            outcome.failure.stage, outcome.failure.code = stage, code
        return result_message(outcome.model_dump(mode="json"), cost=0.2)

    async def still(**kwargs):
        return result_message(approved_still_outcome_for(plan.shots[0]).model_dump(mode="json"), cost=0.0)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    monkeypatch.setattr(run, "generate_still_asset", still)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(veo_extend_long_shots=True), manifest)) == 0

    assert seen["costs"] == {VEO_SEED_TOOL: pytest.approx(0.04), VEO_CLIP_TOOL: pytest.approx(2 * 1.2)}
    assert seen["limit_usd"] == pytest.approx(0.04 + 2 * 1.2)
    record = json.loads((run.RUN_STATE_DIR / "veo_agent_shot_1_attempt_1.json").read_text())
    assert record["claude_cost_usd"] + record["paid_tool_cost_usd"] == pytest.approx(
        0.2 + 0.04 + veo_calls_charged * 1.2
    )
    # A failed video falls back to a still, which costs its one image on top.
    still_cost = 0.0 if stage is None else 0.04
    assert manifest.budget_spent_usd == pytest.approx(0.2 + 0.04 + veo_calls_charged * 1.2 + still_cost)


def test_still_queries_get_the_claude_cap_and_one_image_of_guard_limit(chain, monkeypatch):
    _, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    seen = {}

    async def agent(**kwargs):
        seen["max_budget_usd"] = kwargs["max_budget_usd"]
        seen["limit_usd"] = kwargs["guard"].limit_usd
        return result_message(approved_still_outcome_for(plan.shots[0]).model_dump(mode="json"), cost=0.2)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 0
    assert seen == {"max_budget_usd": CAP, "limit_usd": pytest.approx(0.04)}
    assert manifest.budget_spent_usd == pytest.approx(0.2 + 0.04)


def test_a_still_crash_is_charged_its_reservation_not_the_remaining_budget(chain, monkeypatch):
    _, asset_id = chain
    write_plan(still_visual_plan_for(asset_id))

    async def crashing(**kwargs):
        raise RuntimeError("transport dropped")

    monkeypatch.setattr(run, "generate_still_asset", crashing)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 1
    assert manifest.budget_spent_usd == pytest.approx(STILL_RESERVATION)
    record = json.loads((run.RUN_STATE_DIR / "stills_agent_shot_1_attempt_1.json").read_text())
    assert record["charged_cost_usd"] == pytest.approx(STILL_RESERVATION)


def test_an_overshoot_the_pool_cannot_cover_halts_budget_exhausted(chain, monkeypatch):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)

    async def agent(**kwargs):
        # One Claude turn ran past the $1 cap.
        return result_message(approved_outcome_for(plan.shots[0]).model_dump(mode="json"), cost=1.5)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(max_budget_usd=2.3), manifest)) == 1
    assert manifest.budget_spent_usd == pytest.approx(1.5 + 1.24)
    assert not run.PRODUCTION_ASSETS_FILE.exists()
    [report] = reports("veo_agent")
    assert report["reason_code"] == "budget_exhausted" and "overshoot" in report["reason"]


def test_a_still_overshoot_the_pool_cannot_cover_halts_budget_exhausted(chain, monkeypatch):
    _, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)

    async def agent(**kwargs):
        return result_message(approved_still_outcome_for(plan.shots[0]).model_dump(mode="json"), cost=1.5)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(max_budget_usd=1.5), manifest)) == 1
    assert manifest.budget_spent_usd == pytest.approx(1.5 + 0.04)
    assert not run.PRODUCTION_ASSETS_FILE.exists()
    [report] = reports("stills_agent")
    assert report["reason_code"] == "budget_exhausted" and "overshoot" in report["reason"]


def test_a_result_with_no_usable_cost_is_charged_its_reservation(chain, monkeypatch):
    _, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)

    async def agent(**kwargs):
        return result_message(approved_still_outcome_for(plan.shots[0]).model_dump(mode="json"), cost=None)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 1
    assert manifest.budget_spent_usd == pytest.approx(STILL_RESERVATION)
    assert not run.PRODUCTION_ASSETS_FILE.exists()


def test_planning_halts_on_a_corrupt_production_manifest(chain, monkeypatch):
    _, asset_id = chain
    write_plan(veo_visual_plan_for(asset_id))
    run.PRODUCTION_ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    run.PRODUCTION_ASSETS_FILE.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(run, "generate_veo_asset", refuse_agent("veo_agent"))
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_planning(make_settings(), manifest)) == 1
    assert run.PRODUCTION_ASSETS_FILE.read_text(encoding="utf-8") == "{not json"
    [report] = reports("production_planning")
    assert report["failed_contract_name"] == "ProductionInputs"


def test_an_overshoot_the_pool_covers_is_debited_and_the_shot_is_kept(chain, monkeypatch):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)

    async def agent(**kwargs):
        return result_message(approved_outcome_for(plan.shots[0]).model_dump(mode="json"), cost=1.5)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 0
    assert manifest.budget_spent_usd == pytest.approx(1.5 + 1.24)
    assert set(load_production_assets(run.PRODUCTION_ASSETS_FILE).shots) == {"1"}


@pytest.fixture
def no_wait(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(run.asyncio, "sleep", fake_sleep)


def test_a_vendor_refusal_gives_back_its_reservation_for_the_rerun(chain, monkeypatch, no_wait):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    shot = plan.shots[0]
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        guard = kwargs["guard"]
        await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        if len(calls) == 1:
            await call_paid(guard, VEO_CLIP_TOOL, clip_failure("SDK_ERROR", "Veo request failed: 429 RESOURCE_EXHAUSTED."))
            return result_message(failed_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.01)
        await call_paid(guard, VEO_CLIP_TOOL, {"status": "SUCCESS"})
        return result_message(approved_outcome_for(shot).model_dump(mode="json"), cost=0.01)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Room for exactly one reservation at a time: the rerun only fits if the
    # refused attempt's reservation came back.
    assert asyncio.run(run.run_veo_stage(make_settings(max_budget_usd=2.3), manifest)) == 0
    assert calls == [1, 1]
    assert manifest.iteration_counts["veo_agent_shot_1"] == 1
    assert manifest.budget_spent_usd == pytest.approx(0.01 + 0.04 + 0.01 + 1.24)


def test_the_full_shot_stage_changes_no_working_directory(chain, monkeypatch):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id, count=2)
    plan.shots[1].generation_mode = "STILL"
    write_plan(plan)

    async def veo(**kwargs):
        return result_message(approved_outcome_for(plan.shots[0]).model_dump(mode="json"), cost=0.1)

    async def still(**kwargs):
        return result_message(approved_still_outcome_for(plan.shots[1]).model_dump(mode="json"), cost=0.1)

    def no_chdir(path):
        raise AssertionError("nothing may change the working directory during production")

    monkeypatch.setattr(run, "generate_veo_asset", veo)
    monkeypatch.setattr(run, "generate_still_asset", still)
    monkeypatch.setattr("os.chdir", no_chdir)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 0
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 0
    assert set(load_production_assets(run.PRODUCTION_ASSETS_FILE).shots) == {"1", "2"}


def test_no_production_module_changes_the_working_directory():
    root = Path(run.__file__).resolve().parent
    offenders = [
        str(path.relative_to(root))
        for path in [*root.glob("tools/*.py"), *root.glob("agents/*.py"), *root.glob("state/*.py"), root / "run.py"]
        if "chdir(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --- handler offloading ---------------------------------------------------------

class _Stop(RuntimeError):
    pass


def _offloaded_case(monkeypatch, name, blocking):
    if name == "generate_veo_seed":
        monkeypatch.setattr(gemini_tools, "build_seed_spec", blocking)
        return gemini_tools.generate_veo_seed.handler
    if name == "generate_still":
        monkeypatch.setattr(gemini_tools, "build_still_spec", blocking)
        return gemini_tools.generate_still.handler
    monkeypatch.setattr(veo_tools, "Shot", SimpleNamespace(model_validate=blocking))
    return veo_tools.generate_veo_clip.handler


@pytest.mark.parametrize("name", ["generate_veo_seed", "generate_still", "generate_veo_clip"])
def test_paid_handlers_leave_the_event_loop_responsive(monkeypatch, name):
    released = threading.Event()
    marker = contextvars.ContextVar("marker", default=None)
    seen = {}

    def blocking(*args, **kwargs):
        # Only the event loop can set this. If the handler ran on the loop,
        # the loop would be stuck right here and the wait would time out.
        seen["released"] = released.wait(2.0)
        seen["marker"] = marker.get()
        seen["worker_thread"] = threading.current_thread() is not threading.main_thread()
        raise _Stop("stop here")

    handler = _offloaded_case(monkeypatch, name, blocking)

    async def main():
        marker.set("shot-7")  # carried into the worker thread by to_thread
        task = asyncio.create_task(handler({"shot": {}, "asset": {}, "seed": {}, "attempt": 1}))
        await asyncio.sleep(0.01)
        released.set()
        try:
            return await task
        except _Stop:
            return None

    asyncio.run(main())
    assert seen == {"released": True, "marker": "shot-7", "worker_thread": True}


# --- the state lock ---------------------------------------------------------------

def test_concurrent_upserts_lose_no_entry(chain, monkeypatch):
    _, asset_id = chain
    plan = still_visual_plan_for(asset_id, count=8)
    entries = [
        ProductionAssetEntry.from_still_result(
            approved_still_outcome_for(shot).result, source_asset_ids=shot.source_asset_ids,
        )
        for shot in plan.shots
    ]
    real_load = production_assets_module.load_production_assets

    def slow_load(path):
        # Widen the read-merge-write window so an unlocked upsert would lose entries.
        manifest = real_load(path)
        time.sleep(0.01)
        return manifest

    monkeypatch.setattr(production_assets_module, "load_production_assets", slow_load)
    barrier = threading.Barrier(len(entries))

    def write(entry):
        barrier.wait()
        upsert_production_asset(entry, path=run.PRODUCTION_ASSETS_FILE)

    threads = [threading.Thread(target=write, args=(entry,)) for entry in entries]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(real_load(run.PRODUCTION_ASSETS_FILE).shots) == {str(n) for n in range(1, 9)}


def test_concurrent_ledger_debits_and_manifest_saves_are_serialised(tmp_path):
    manifest = RunManifest()
    ledger = BudgetLedger(max_budget_usd=100.0, manifest=manifest, state_dir=tmp_path)
    reservations = [ledger.reserve(label=str(n), claude_cap_usd=1.0, external_usd=0.0) for n in range(16)]
    barrier = threading.Barrier(len(reservations))

    def work(reservation):
        barrier.wait()
        for _ in range(10):
            ledger.debit(reservation, 0.01)
            save_run_manifest(manifest, state_dir=tmp_path)
        ledger.release(reservation)

    threads = [threading.Thread(target=work, args=(r,)) for r in reservations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert manifest.budget_spent_usd == pytest.approx(1.6)
    assert ledger.unreserved_usd() == pytest.approx(100.0 - 1.6)
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["usage"]["budget_spent_usd"] == pytest.approx(1.6)


# --- per-vendor semaphores ----------------------------------------------------------

class PeakTracker:
    """Counts how many fake vendor calls are inside at once.

    Each call lingers briefly, hoping for one peer more than the cap. Without
    the semaphore every call piles in and the peak goes past the cap; with it
    the peak reaches the cap and stops there.
    """

    def __init__(self, cap: int):
        self.cap = cap
        self.active = 0
        self.peak = 0
        self.calls = 0
        self.cond = threading.Condition()

    def visit(self):
        with self.cond:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
            self.cond.notify_all()
            self.cond.wait_for(lambda: self.active > self.cap, timeout=0.1)
            self.active -= 1


def _current_caps() -> dict:
    return {
        "veo": vendor_limits.vendor_limit(vendor_limits.VEO),
        "gemini_image": vendor_limits.vendor_limit(vendor_limits.GEMINI_IMAGE),
        "codex_image": vendor_limits.vendor_limit(vendor_limits.CODEX_IMAGE),
    }


@pytest.fixture
def vendor_caps():
    before = _current_caps()
    vendor_limits.configure_vendor_limits(veo=3, gemini_image=2, codex_image=1)
    yield
    vendor_limits.configure_vendor_limits(**before)


def test_run_stages_applies_the_configured_vendor_caps(monkeypatch, vendor_caps):
    async def stop(*args, **kwargs):
        return 1  # halt straight after the caps are set

    monkeypatch.setattr(run, "run_screenshot_stage", stop)
    settings = make_settings(veo_concurrency=1, gemini_image_concurrency=4, codex_image_concurrency=2)
    assert asyncio.run(run.run_stages(Path("source_images"), settings, RunManifest())) == 1
    assert _current_caps() == {"veo": 1, "gemini_image": 4, "codex_image": 2}


def test_vendor_caps_below_one_are_refused(vendor_caps):
    with pytest.raises(ValueError):
        vendor_limits.configure_vendor_limits(veo=0, gemini_image=2, codex_image=1)
    assert _current_caps() == {"veo": 3, "gemini_image": 2, "codex_image": 1}


async def _gather(handler, args_list):
    return await asyncio.gather(*(handler(args) for args in args_list))


def _still_args(tmp_path, count, provider):
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 40), "red").save(source)
    return [
        {
            "shot": shot_dict(sequence=n, generation_mode="STILL"),
            "asset": asset_for(source),
            "subtitle_text": "Narration.",
            "project_id": "p",
            "location": "global",
            "image_model": "m",
            "image_provider": provider,
            "codex_timeout_seconds": 5.0,
            "cost_usd": 0.04,
        }
        for n in range(1, count + 1)
    ]


def test_gemini_image_calls_never_exceed_their_cap(tmp_path, monkeypatch, vendor_caps):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gemini_tools, "GEMINI_IMAGE_MIN_INTERVAL_SECONDS", 0.0)
    tracker = PeakTracker(vendor_limits.vendor_limit(vendor_limits.GEMINI_IMAGE))
    response = FakeResponse([FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))])

    class Models:
        def generate_content(self, **kwargs):
            tracker.visit()
            return response

    monkeypatch.setattr(gemini_tools.genai, "Client", lambda **_: SimpleNamespace(models=Models()))
    results = asyncio.run(_gather(gemini_tools.generate_still.handler, _still_args(tmp_path, 5, "gemini")))
    assert all("failure" not in json.loads(r["content"][0]["text"]) for r in results)
    assert (tracker.calls, tracker.peak) == (5, 2)


def test_codex_image_calls_never_exceed_their_cap(tmp_path, monkeypatch, vendor_caps):
    monkeypatch.chdir(tmp_path)
    tracker = PeakTracker(vendor_limits.vendor_limit(vendor_limits.CODEX_IMAGE))

    def fake_codex(command, **kwargs):
        tracker.visit()
        workspace = Path(kwargs["cwd"])
        Image.new("RGB", (90, 160), "blue").save(workspace / codex_image_tools.OUTPUT_FILENAME)
        (workspace / codex_image_tools.LAST_MESSAGE_FILENAME).write_text('{"status": "generated", "reason": ""}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_image_tools, "codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_image_tools.subprocess, "run", fake_codex)
    results = asyncio.run(_gather(gemini_tools.generate_still.handler, _still_args(tmp_path, 3, "codex")))
    assert all("failure" not in json.loads(r["content"][0]["text"]) for r in results)
    assert (tracker.calls, tracker.peak) == (3, 1)


def test_veo_operations_never_exceed_their_cap(tmp_path, monkeypatch, vendor_caps):
    monkeypatch.chdir(tmp_path)
    tracker = PeakTracker(vendor_limits.vendor_limit(vendor_limits.VEO))

    class Models:
        def generate_videos(self, **kwargs):
            tracker.visit()
            return {"name": "operations/x", "done": True}

    monkeypatch.setattr(veo_tools.genai, "Client", lambda **_: SimpleNamespace(models=Models()))
    monkeypatch.setattr(
        veo_tools, "inspect_completed_operation",
        lambda operation, **kw: veo_tools._failure(
            shot=kw["shot"], attempt=kw["attempt"], code="OPERATION_ERROR", reason="fake",
            retryable=True, cost_usd=0.0,
        ),
    )
    args_list = []
    for n in range(1, 6):
        shot = shot_for(n)
        args_list.append({
            "shot": shot.model_dump(mode="json"), "seed": seed_for(shot).model_dump(mode="json"),
            "project_id": "p", "location": "global", "model": "veo", "gcs_output_uri": "gs://b/p/",
            "resolution": "720p", "duration_seconds": 8, "poll_seconds": 0.0, "max_poll_seconds": 1.0,
            "attempt": 1, "cost_usd": 1.2, "correction": "",
        })
    asyncio.run(_gather(veo_tools.generate_veo_clip.handler, args_list))
    assert (tracker.calls, tracker.peak) == (5, 3)


def test_image_pacing_spaces_concurrent_callers(monkeypatch):
    monkeypatch.setattr(gemini_tools, "GEMINI_IMAGE_MIN_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(gemini_tools, "_last_image_call_monotonic", 0.0)
    barrier = threading.Barrier(3)

    def call():
        barrier.wait()
        gemini_tools._pace_image_call()

    started = time.monotonic()
    threads = [threading.Thread(target=call) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # The first goes at once; each of the other two waits a full interval.
    assert time.monotonic() - started >= 2 * 0.05 - 0.005
