"""Story 4.4: concurrent shot launch and failure scoping.

No live vendor calls: the agents are fakes. Every fake delay is short, and
overlap and caps are proven with barriers, events and peak counters rather
than timing thresholds.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time

import pytest

import orchestrator.run as run
from orchestrator.contracts.veo import VeoFailureContract, VeoOutcomeContract, shot_fingerprint
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.settings import DEFAULT_SHOT_CONCURRENCY, load_settings
from orchestrator.state.production_assets import load_production_assets
from orchestrator.state.run_manifest import load_run_manifest, save_run_manifest
from orchestrator.tests.test_full_chain_resume import (  # noqa: F401 -- `chain` is a fixture
    approved_outcome_for,
    approved_still_outcome_for,
    chain,
    failed_outcome_for,
    failed_still_outcome_for,
    still_visual_plan_for,
    subtitle_cues_for,
    veo_visual_plan_for,
)
from orchestrator.tests.test_guard_hooks import result_message
from orchestrator.tests.test_preflight import make_settings as _make_settings
from orchestrator.tools import vendor_limits

CAP = 1.0
STILL_RESERVATION = CAP + 0.04
VEO_RESERVATION = CAP + 0.04 + 1.2


def make_settings(**overrides):
    return _make_settings(**{"claude_shot_attempt_cap_usd": CAP, "max_budget_usd": 40.0, **overrides})


def write_plan(plan: VisualPlanContract) -> VisualPlanContract:
    run.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    return plan


def mixed_plan(asset_id: str, *, veo: int, stills: int) -> VisualPlanContract:
    plan = veo_visual_plan_for(asset_id, count=veo + stills)
    for shot in plan.shots[veo:]:
        shot.generation_mode = "STILL"
    return write_plan(plan)


def success(plan, sequence: int, *, cost: float = 0.1):
    shot = plan.shots[sequence - 1]
    if shot.generation_mode == "VEO":
        return result_message(approved_outcome_for(shot).model_dump(mode="json"), cost=cost)
    return result_message(approved_still_outcome_for(shot).model_dump(mode="json"), cost=cost)


def install(monkeypatch, agent):
    """One fake for both producers; it sees the shot as the agent would."""

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    monkeypatch.setattr(run, "generate_still_asset", agent)


def all_reports() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(run.RUN_STATE_DIR.glob("failure_*.json"))]


def made() -> set[str]:
    if not run.PRODUCTION_ASSETS_FILE.exists():
        return set()
    return set(load_production_assets(run.PRODUCTION_ASSETS_FILE).shots)


def vendor_hard_refusal():
    return result_message(None, cost=0.05, is_error=True, result="You've hit your usage limit")


@pytest.fixture
def restore_vendor_limits():
    yield
    vendor_limits.configure_vendor_limits(
        veo=vendor_limits.DEFAULT_VEO_CONCURRENCY,
        gemini_image=vendor_limits.DEFAULT_GEMINI_IMAGE_CONCURRENCY,
        codex_image=vendor_limits.DEFAULT_CODEX_IMAGE_CONCURRENCY,
    )


# --- settings and wiring --------------------------------------------------------

def test_shot_concurrency_setting_has_a_default_and_an_env_override(monkeypatch):
    monkeypatch.delenv("SHOT_CONCURRENCY", raising=False)
    assert load_settings().shot_concurrency == DEFAULT_SHOT_CONCURRENCY == 4
    monkeypatch.setenv("SHOT_CONCURRENCY", "2")
    assert load_settings().shot_concurrency == 2
    monkeypatch.setenv("SHOT_CONCURRENCY", "0")
    with pytest.raises(run.SettingsError):
        load_settings()


# --- launch and caps ----------------------------------------------------------------

def test_veo_and_still_shots_overlap_and_every_upsert_survives(chain, monkeypatch):
    _, asset_id = chain
    plan = mixed_plan(asset_id, veo=3, stills=3)
    # Every shot must be in flight at once for the barrier to open; run one
    # at a time, the first shot would wait forever (the timeout fails it).
    barrier = asyncio.Barrier(6)

    async def agent(**kwargs):
        await asyncio.wait_for(barrier.wait(), timeout=2)
        return success(plan, kwargs["shot"]["sequence"])

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(shot_concurrency=6), manifest)) == 0
    assert made() == {"1", "2", "3", "4", "5", "6"}
    modes = load_production_assets(run.PRODUCTION_ASSETS_FILE).shots
    assert {key: entry.generation_mode for key, entry in modes.items()} == {
        "1": "VEO", "2": "VEO", "3": "VEO", "4": "STILL", "5": "STILL", "6": "STILL",
    }
    assert not all_reports()


def test_the_shot_level_cap_is_never_exceeded(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=6))
    active, peak = 0, 0

    async def agent(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return success(plan, kwargs["shot"]["sequence"])

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(shot_concurrency=2), manifest)) == 0
    assert peak == 2
    assert len(made()) == 6


def test_the_vendor_cap_holds_under_concurrent_shots(chain, monkeypatch, restore_vendor_limits):
    _, asset_id = chain
    plan = write_plan(veo_visual_plan_for(asset_id, count=4))
    vendor_limits.configure_vendor_limits(veo=2, gemini_image=2, codex_image=1)
    lock = threading.Lock()
    shots_active = shots_peak = veo_active = veo_peak = 0

    def blocking_veo_call():
        # What a paid handler does in its worker thread (Story 4.3).
        nonlocal veo_active, veo_peak
        with vendor_limits.vendor_slot(vendor_limits.VEO):
            with lock:
                veo_active += 1
                veo_peak = max(veo_peak, veo_active)
            time.sleep(0.05)
            with lock:
                veo_active -= 1

    async def agent(**kwargs):
        nonlocal shots_active, shots_peak
        shots_active += 1
        shots_peak = max(shots_peak, shots_active)
        await asyncio.to_thread(blocking_veo_call)
        shots_active -= 1
        return success(plan, kwargs["shot"]["sequence"])

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(shot_concurrency=4), manifest)) == 0
    assert (shots_peak, veo_peak) == (4, 2)
    assert len(made()) == 4


def test_veo_shots_launch_before_stills(chain, monkeypatch):
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id, count=3)
    plan.shots[0].generation_mode = "STILL"
    write_plan(plan)
    order = []

    async def agent(**kwargs):
        order.append(kwargs["shot"]["sequence"])
        return success(plan, kwargs["shot"]["sequence"])

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(shot_concurrency=1), manifest)) == 0
    assert order == [2, 3, 1]


# --- budget ----------------------------------------------------------------------------

def test_a_retry_waits_for_a_sibling_reservation_instead_of_failing(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=2))
    shot_one_failed = asyncio.Event()
    calls = []

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        calls.append((sequence, kwargs["attempt"]))
        if sequence == 1 and kwargs["attempt"] == 1:
            shot_one_failed.set()
            return result_message(
                failed_still_outcome_for(plan.shots[0], retryable=True).model_dump(mode="json"), cost=0.1,
            )
        if sequence == 2:
            # Still holding its reservation while shot 1 wants a retry.
            await asyncio.wait_for(shot_one_failed.wait(), timeout=2)
            await asyncio.sleep(0.01)
        return success(plan, sequence)

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Two first tries fit. Shot 1's retry can't fit beside shot 2's hold,
    # but it can once shot 2 gives back what it didn't spend.
    settings = make_settings(max_budget_usd=2 * STILL_RESERVATION + 0.05)
    assert asyncio.run(run.run_production_stage(settings, manifest)) == 0
    assert sorted(calls) == [(1, 1), (1, 2), (2, 1)]
    assert made() == {"1", "2"}
    assert manifest.budget_spent_usd == pytest.approx(3 * (0.1 + 0.04))
    assert manifest.budget_spent_usd <= settings.max_budget_usd



def test_an_overshoot_waits_for_sibling_holds_before_it_counts(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=2))
    two_started, one_done = asyncio.Event(), asyncio.Event()

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        if sequence == 1:
            await asyncio.wait_for(two_started.wait(), timeout=2)
            one_done.set()
            # One Claude turn past the cap.
            return success(plan, 1, cost=CAP + 0.3)
        two_started.set()
        await asyncio.wait_for(one_done.wait(), timeout=2)
        await asyncio.sleep(0.01)
        return success(plan, 2)

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Shot 1's overshoot only goes below zero while shot 2 still holds its
    # worst case; once shot 2 gives back what it didn't spend, it fits.
    settings = make_settings(max_budget_usd=2 * STILL_RESERVATION + 0.2)
    assert asyncio.run(run.run_production_stage(settings, manifest)) == 0
    assert made() == {"1", "2"}
    assert manifest.budget_spent_usd == pytest.approx(CAP + 0.3 + 0.04 + 0.1 + 0.04)
    assert manifest.budget_spent_usd <= settings.max_budget_usd


def test_a_stop_ends_a_retry_that_is_waiting_for_budget(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=3))
    one_failed = asyncio.Event()
    siblings_started = {2: asyncio.Event(), 3: asyncio.Event()}
    calls = []

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        calls.append((sequence, kwargs["attempt"]))
        if sequence == 1:
            for started in siblings_started.values():
                await asyncio.wait_for(started.wait(), timeout=2)
            one_failed.set()
            return result_message(
                failed_still_outcome_for(plan.shots[0], retryable=True).model_dump(mode="json"), cost=0.1,
            )
        siblings_started[sequence].set()
        await asyncio.wait_for(one_failed.wait(), timeout=2)
        if sequence == 2:
            await asyncio.sleep(0.01)
            return vendor_hard_refusal()
        await asyncio.sleep(0.03)
        return success(plan, sequence)

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Shot 1's retry can't fit beside shots 2 and 3, so it waits; the vendor
    # limit on shot 2 then ends that wait instead of starting the retry.
    settings = make_settings(max_budget_usd=3 * STILL_RESERVATION + 0.05)
    assert asyncio.run(run.run_production_stage(settings, manifest)) == 1
    assert sorted(calls) == [(1, 1), (2, 1), (3, 1)]
    assert made() == {"3"}
    [report] = all_reports()
    assert [(e["sequence"], e["reason_code"]) for e in report["failed_shots"]] == [(2, "vendor_limit")]
    assert "shot(s) 1 stopped before finishing" in report["reason"]

def _scripted_agent(plan):
    """Each shot fails once, then succeeds, spending a known amount each time."""

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        await asyncio.sleep(0.01 * sequence)
        if kwargs["attempt"] == 1:
            return result_message(
                failed_still_outcome_for(plan.shots[sequence - 1], retryable=True).model_dump(mode="json"),
                cost=0.2,
            )
        return success(plan, sequence, cost=0.3)

    return agent


def test_parallel_spend_equals_sequential_spend_and_stays_under_the_ceiling(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=4))
    install(monkeypatch, _scripted_agent(plan))
    settings = make_settings(max_budget_usd=4 * STILL_RESERVATION + 0.1)
    spends = {}
    for concurrency in (4, 1):
        shutil.rmtree(run.RUN_STATE_DIR, ignore_errors=True)
        run.PRODUCTION_ASSETS_FILE.unlink(missing_ok=True)
        manifest = load_run_manifest(run.RUN_STATE_DIR)
        assert asyncio.run(run.run_production_stage(
            make_settings(max_budget_usd=settings.max_budget_usd, shot_concurrency=concurrency), manifest,
        )) == 0
        assert made() == {"1", "2", "3", "4"}
        spends[concurrency] = manifest.budget_spent_usd
    assert spends[4] == pytest.approx(spends[1]) == pytest.approx(4 * (0.2 + 0.04 + 0.3 + 0.04))
    assert spends[4] <= settings.max_budget_usd


# --- failure scoping ---------------------------------------------------------------

def test_a_shot_specific_failure_lets_every_sibling_finish(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=3))

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        if sequence == 1:
            return result_message(
                failed_still_outcome_for(plan.shots[0], retryable=False, reason="policy blocked").model_dump(mode="json"),
                cost=0.1,
            )
        await asyncio.sleep(0.02)
        return success(plan, sequence)

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(shot_concurrency=3), manifest)) == 1
    assert made() == {"2", "3"}
    [report] = all_reports()
    assert report["stage_id"] == "stills_agent" and "policy blocked" in report["reason"]
    [entry] = report["failed_shots"]
    assert (entry["sequence"], entry["scope"], entry["failure_stage"]) == (1, "shot", "still_qa")


def test_several_shot_specific_failures_make_one_combined_report(chain, monkeypatch):
    _, asset_id = chain
    plan = mixed_plan(asset_id, veo=1, stills=2)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    manifest.max_attempts["stills_agent"] = 2
    save_run_manifest(manifest, state_dir=run.RUN_STATE_DIR)

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        shot = plan.shots[sequence - 1]
        if sequence == 1:
            return result_message(
                failed_outcome_for(shot, retryable=False, reason="seed defect").model_dump(mode="json"), cost=0.1,
            )
        if sequence == 2:
            return result_message(
                failed_still_outcome_for(shot, retryable=True, reason="still failing").model_dump(mode="json"),
                cost=0.1,
            )
        await asyncio.sleep(0.02)
        return success(plan, sequence)

    install(monkeypatch, agent)
    assert asyncio.run(run.run_production_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert made() == {"3"}
    [report] = all_reports()
    assert report["stage_id"] == "production"
    assert [(e["sequence"], e["stage_id"], e["scope"], e["reason_code"]) for e in report["failed_shots"]] == [
        (1, "veo_agent", "shot", "unclassified"),
        (2, "stills_agent", "shot", "retry_ceiling"),
    ]
    assert report["attempt_count"] == 1 + 2
    assert "seed defect" in report["reason"] and "still failing" in report["reason"]



def test_a_veo_shot_that_exhausts_its_retries_lets_its_siblings_finish(chain, monkeypatch):
    _, asset_id = chain
    plan = mixed_plan(asset_id, veo=1, stills=2)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    manifest.max_attempts["veo_agent"] = 2
    save_run_manifest(manifest, state_dir=run.RUN_STATE_DIR)
    calls = []

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        calls.append((sequence, kwargs["attempt"]))
        if sequence == 1:
            return result_message(
                failed_outcome_for(plan.shots[0], retryable=True, reason="clip QA failed").model_dump(mode="json"),
                cost=0.1,
            )
        await asyncio.sleep(0.02)
        return success(plan, sequence)

    install(monkeypatch, agent)
    assert asyncio.run(run.run_production_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert sorted(calls) == [(1, 1), (1, 2), (2, 1), (3, 1)]
    assert made() == {"2", "3"}
    [report] = all_reports()
    [entry] = report["failed_shots"]
    assert (entry["sequence"], entry["scope"], entry["reason_code"], entry["failure_stage"]) == (
        1, "shot", "retry_ceiling", "veo_generation")


def test_a_mixed_report_is_led_by_the_systemic_failure(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=3))
    two_started = asyncio.Event()

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        if sequence == 1:
            return result_message(
                failed_still_outcome_for(plan.shots[0], retryable=False, reason="policy blocked").model_dump(mode="json"),
                cost=0.1,
            )
        if sequence == 2:
            two_started.set()
            await asyncio.sleep(0.01)
            return vendor_hard_refusal()
        await asyncio.wait_for(two_started.wait(), timeout=2)
        await asyncio.sleep(0.02)
        return success(plan, sequence)

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(shot_concurrency=3), manifest)) == 1
    assert made() == {"3"}
    [report] = all_reports()
    assert report["stage_id"] == "production"
    assert (report["reason_code"], report["resumable"]) == ("vendor_limit", True)
    assert [(e["sequence"], e["scope"], e["resumable"]) for e in report["failed_shots"]] == [
        (1, "shot", False), (2, "systemic", True),
    ]
    assert "shot(s) 1 failed on their own" in report["reason"]


def test_the_production_step_plans_before_it_launches(chain, monkeypatch):
    _, asset_id = chain
    plan = mixed_plan(asset_id, veo=1, stills=1)
    made_as = {}

    async def veo(**kwargs):
        raise AssertionError("the Veo shot was demoted before launch; no Veo agent may run")

    async def still(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        made_as[sequence] = kwargs["shot"]["generation_mode"]
        return result_message(
            approved_still_outcome_for(VisualPlanContract.model_validate(
                json.loads(run.VISUAL_PLAN_FILE.read_text())).shots[sequence - 1]).model_dump(mode="json"),
            cost=0.1,
        )

    monkeypatch.setattr(run, "generate_veo_asset", veo)
    monkeypatch.setattr(run, "generate_still_asset", still)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Two stills fit; a video and a still don't.
    settings = make_settings(max_budget_usd=2 * STILL_RESERVATION + 0.1)
    assert asyncio.run(run.run_production_stage(settings, manifest)) == 0
    assert made_as == {1: "STILL", 2: "STILL"}
    assert made() == {"1", "2"}


def test_the_production_step_halts_before_any_call_when_even_stills_cannot_fit(chain, monkeypatch):
    _, asset_id = chain
    mixed_plan(asset_id, veo=1, stills=1)

    async def refuse(**kwargs):
        raise AssertionError("nothing may be paid for")

    install(monkeypatch, refuse)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(max_budget_usd=STILL_RESERVATION), manifest)) == 1
    [report] = all_reports()
    assert (report["stage_id"], report["reason_code"]) == ("production_planning", "budget_exhausted")
    assert manifest.budget_spent_usd == 0.0

def test_a_systemic_failure_stops_new_launches_but_lets_in_flight_shots_finish(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=4))
    refused, two_started = asyncio.Event(), asyncio.Event()
    calls = []

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        calls.append(sequence)
        if sequence == 1:
            await asyncio.wait_for(two_started.wait(), timeout=2)
            refused.set()
            return vendor_hard_refusal()
        # In flight when shot 1 is refused; it must not be cancelled.
        two_started.set()
        await asyncio.wait_for(refused.wait(), timeout=2)
        await asyncio.sleep(0.01)
        return success(plan, sequence)

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(shot_concurrency=2), manifest)) == 1
    assert sorted(calls) == [1, 2]
    assert made() == {"2"}
    [report] = all_reports()
    assert (report["reason_code"], report["resumable"]) == ("vendor_limit", True)
    assert [(e["sequence"], e["scope"]) for e in report["failed_shots"]] == [(1, "systemic")]
    assert "shot(s) 3, 4 stopped before finishing" in report["reason"]
    assert manifest.iteration_counts["stills_agent_shot_1"] == 0  # a vendor limit costs no attempt

    # Resume (AD-14): only the refused and the unstarted shots run again.
    calls.clear()

    async def resumed(**kwargs):
        calls.append(kwargs["shot"]["sequence"])
        return success(plan, kwargs["shot"]["sequence"])

    install(monkeypatch, resumed)
    assert asyncio.run(run.run_production_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0
    assert sorted(calls) == [1, 3, 4]
    assert made() == {"1", "2", "3", "4"}


def test_after_a_systemic_failure_a_running_shot_starts_no_new_attempt(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=2))
    refused, two_started = asyncio.Event(), asyncio.Event()
    calls = []

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        calls.append((sequence, kwargs["attempt"]))
        if sequence == 1:
            await asyncio.wait_for(two_started.wait(), timeout=2)
            refused.set()
            return vendor_hard_refusal()
        two_started.set()
        await asyncio.wait_for(refused.wait(), timeout=2)
        return result_message(
            failed_still_outcome_for(plan.shots[1], retryable=True).model_dump(mode="json"), cost=0.1,
        )

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(), manifest)) == 1
    assert sorted(calls) == [(1, 1), (2, 1)]
    assert manifest.iteration_counts["stills_agent_shot_2"] == 1
    [report] = all_reports()
    assert [e["sequence"] for e in report["failed_shots"]] == [1]
    assert "shot(s) 2 stopped before finishing" in report["reason"]


def test_a_budget_stop_is_systemic(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=3))
    calls = []

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        calls.append((sequence, kwargs["attempt"]))
        # Every try spends its whole reservation; shot 1 needs two.
        if sequence == 1 and kwargs["attempt"] == 1:
            return result_message(
                failed_still_outcome_for(plan.shots[0], retryable=True).model_dump(mode="json"), cost=CAP,
            )
        return success(plan, sequence, cost=CAP)

    install(monkeypatch, agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    # Every first try fits at planning; shot 1's retry then leaves shot 3 short.
    settings = make_settings(max_budget_usd=3 * STILL_RESERVATION + 0.5, shot_concurrency=1)
    assert asyncio.run(run.run_production_stage(settings, manifest)) == 1
    assert calls == [(1, 1), (1, 2), (2, 1)]
    assert made() == {"1", "2"}
    [report] = all_reports()
    assert (report["reason_code"], report["resumable"]) == ("budget_exhausted", True)
    assert [(e["sequence"], e["scope"]) for e in report["failed_shots"]] == [(3, "systemic")]
    assert manifest.budget_spent_usd <= settings.max_budget_usd


def test_a_resume_redoes_only_the_failed_shot_with_its_ladder_rung(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(veo_visual_plan_for(asset_id, count=3))
    shot_two = plan.shots[1]

    def likeness_rejection():
        failure = VeoFailureContract(
            produced_by="veo_agent", shot_sequence=2, shot_fingerprint=shot_fingerprint(shot_two),
            stage="veo_generation", code="RAI_FILTERED", reason="person looks real", retryable=True,
            attempt=1, cost_usd=1.2,
        )
        return VeoOutcomeContract(
            produced_by="veo_agent", status="FAILURE", shot_sequence=2,
            shot_fingerprint=shot_fingerprint(shot_two), failure=failure,
        ).model_dump(mode="json")

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        if sequence == 2:
            if kwargs["attempt"] == 1:
                return result_message(likeness_rejection(), cost=0.1)
            return result_message(
                failed_outcome_for(shot_two, retryable=False, reason="seed defect").model_dump(mode="json"), cost=0.1,
            )
        await asyncio.sleep(0.01)
        return success(plan, sequence)

    install(monkeypatch, agent)
    assert asyncio.run(run.run_production_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert made() == {"1", "3"}
    [report] = all_reports()
    assert [(e["sequence"], e["scope"]) for e in report["failed_shots"]] == [(2, "shot")]

    calls = []

    async def resumed(**kwargs):
        calls.append((kwargs["shot"]["sequence"], kwargs["attempt"], kwargs["settings"]["seed_prompt_level"]))
        return success(plan, kwargs["shot"]["sequence"])

    install(monkeypatch, resumed)
    assert asyncio.run(run.run_production_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0
    # Only shot 2, on its third try, starting past the refused rung.
    assert calls == [(2, 3, 1)]
    assert made() == {"1", "2", "3"}


def test_a_crashing_shot_does_not_tear_down_its_siblings(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=2))
    real = run._produce_still_shot
    finished = []
    two_started = asyncio.Event()

    async def producer(shot, **kwargs):
        if shot.sequence == 1:
            await asyncio.wait_for(two_started.wait(), timeout=2)
            raise OSError("disk full")
        two_started.set()
        outcome = await real(shot, **kwargs)
        finished.append(shot.sequence)
        return outcome

    async def agent(**kwargs):
        await asyncio.sleep(0.01)
        return success(plan, kwargs["shot"]["sequence"])

    install(monkeypatch, agent)
    monkeypatch.setattr(run, "_produce_still_shot", producer)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(), manifest)) == 1
    assert finished == [2] and made() == {"2"}
    [report] = all_reports()
    assert "disk full" in report["reason"]
    assert report["failed_shots"][0]["scope"] == "systemic"
