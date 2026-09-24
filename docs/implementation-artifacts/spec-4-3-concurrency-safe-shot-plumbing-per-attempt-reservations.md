---
title: 'Story 4.3: Concurrency-safe shot plumbing and per-attempt budget reservations'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: '825167357f78588f3629bbf2bd5354a0c36c25ef'
route: 'full'
review_loop_iteration: 0
context:
  - '{project-root}/docs/epics.md'
  - '{project-root}/docs/implementation-artifacts/spec-4-2-guard-hooks-budget-guard-vendor-limit-classification.md'
  - '{project-root}/docs/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A shot query's `max_budget_usd` is nearly the whole run budget, a crash is charged the whole remaining budget, Veo shots are demoted mid-loop by rewriting `visual_plan.json` under the stills stage, the vendor handlers block the event loop, and shared writes have no lock. Story 4.4 can't turn on parallel shots on top of that.

**Approach:** Every shot attempt reserves `CLAUDE_SHOT_ATTEMPT_CAP_USD` plus its external costs from a run-level ledger before its query starts, and the query's `max_budget_usd` is the cap. A production-planning step before any shot demotes the lowest-ranked Veo shots until every first attempt fits, or halts. Paid handlers run in `asyncio.to_thread` under per-vendor semaphores. One lock serialises upserts, manifest saves and ledger changes. Shots stay sequential.

## Boundaries & Constraints

**Always:**
- Story AC `docs/epics.md:308-375`, FR6, FR7, FR9, NFR4, NFR5, NFR8 and the Additional Requirements (blocking calls, shared mutable state, demotion handoff) are binding.
- All 4.2 behaviour stays: a vendor refusal gives back the attempt and its reservation, charges only real costs, and halts `vendor_limit`; a budget denial halts `budget_exhausted`.
- Provenance stamping, per-shot retry ceilings, the likeness ladder and resume-skip stay unchanged.

**Never:** Launch shots concurrently (4.4). Touch the user's dirty-tree run output. Make live vendor calls. Change contracts other than additively.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| All first attempts fit | pool ≥ Σ reservations | No demotion, plan untouched | none |
| Veo shots don't fit | pool < Σ | Lowest-ranked Veo shots demoted to STILL in one plan write, before any paid call | logged, run continues |
| Even stills don't fit | pool < Σ with all STILL | Halt before any paid call | `budget_exhausted`, resumable |
| Retry can't reserve | pool < cap + external | Shot fails, no demotion | `budget_exhausted`, resumable |
| Attempt crashes | agent raises (not vendor) | Charged the attempt's reservation | halt as today |
| Overshoot | Claude cost > its share | Debited as reported; excess from the pool | negative pool → `budget_exhausted` |

</frozen-after-approval>

## Code Map

- `orchestrator/settings.py` -- new fields/env: `CLAUDE_SHOT_ATTEMPT_CAP_USD`, `VEO_CONCURRENCY`=3, `GEMINI_IMAGE_CONCURRENCY`=2, `CODEX_IMAGE_CONCURRENCY`=1.
- `orchestrator/state/run_manifest.py` -- `STATE_LOCK` (RLock) taken by `save_run_manifest`; `session_id` documented last-writer-wins.
- `orchestrator/state/production_assets.py` -- `upsert_production_asset` takes `STATE_LOCK`.
- `orchestrator/state/budget_ledger.py` (new) -- `Reservation`, `BudgetLedger` (reserve / debit / release / unreserved / overdrawn), all under `STATE_LOCK`.
- `orchestrator/run.py` -- `run_production_planning` (new step before Veo in `run_stages`) replaces `_demote_veo_shot_to_still`; the Veo and stills loops reserve per attempt, pass the cap as `max_budget_usd`, give the guard the reservation's paid-call share, debit through the ledger, and charge a crash its reservation; `_charge_guarded_attempt` / `_handle_shot_vendor_refusal` debit through the ledger.
- `orchestrator/tools/vendor_limits.py` (new) -- per-vendor `threading.BoundedSemaphore`s, `configure_vendor_limits`, `vendor_slot`.
- `orchestrator/tools/{gemini_tools,veo_tools,codex_image_tools}.py` -- handler bodies offloaded via `asyncio.to_thread`; semaphores around `generate_content`, Veo submit+poll, Codex `subprocess.run`; `_pace_image_call` under a lock.
- Tests: reuse `chain`, the outcome builders (`test_full_chain_resume.py`) and `call_paid`/`result_message` (`test_guard_hooks.py`).

## Tasks & Acceptance

**Execution:**
- [x] `settings.py`, `vendor_limits.py` -- settings and semaphores.
- [x] `run_manifest.py`, `production_assets.py`, `budget_ledger.py` -- lock and ledger.
- [x] tool modules -- offloading, semaphores, pacing lock.
- [x] `run.py` -- planning step, per-attempt reservations, crash charge, guard limit.
- [x] `orchestrator/tests/test_shot_reservations.py` -- every item in the story's "tests prove" list; update old-model tests.
- [x] `ARCHITECTURE-SPINE.md` -- amend AD-7, AD-10, AD-15(c).

**Acceptance Criteria:**
- Given the full suite, when it runs, then the failing set equals the baseline (5 failed, 12 errors) and the new tests pass.

## Implementation Notes

- Implemented directly by the delegated session, not a fresh subagent: it already held the investigation, and earlier agents were killed by a no-progress watchdog.
- **Planning runs at the start of `run_veo_stage`**, not as a new `run_stages` step. Every `main()` test stubs each stage by name, and a new step would have run for real in all of them. `run_veo_stage` is the first shot stage, so this is still "when shot production begins", and `run_stills_stage` reads the plan it left.
- `CLAUDE_SHOT_ATTEMPT_CAP_USD` defaults to $1.00. Live Veo and stills attempts in `orchestrator_runs/` cost $0.18–$0.59 of Claude each.
- The overshoot check (`ledger.overdrawn()`) replaces `_check_budget` in the shot loops, in the same place. It fires only when the pool is negative, so an attempt that spends exactly the last dollar no longer halts on the spot; the next attempt's reservation fails instead.
- Veo's semaphore is held from submit until the operation is done (poll included); the GCS download and ffmpeg previews run after it is released, still in the worker thread. Gemini's covers pacing plus `generate_content`, not the 429 backoff sleep. Codex's covers `subprocess.run`.
- `vendor_limits` is configured from settings at the top of `run_stages`.
- Tests changed for the new model: `test_veo_budget_gate_demotes_to_still_before_any_agent_or_paid_call` (a demotion now needs a still to fit, so the cap is lowered to $0.50), `test_veo_agent_exception_charges_full_attempt_ceiling_and_halts` (crash charge is the reservation, $2.24, not $10), `test_cumulative_denial_halts_with_budget_exhausted_whatever_the_agent_returned` (guard limit $1.24, not $10), `test_exception_after_a_tool_refusal_charges_what_billed` (the refused call now comes first: a still attempt reserves one image, so a second billed image would be denied).
- Suite: 5 failed / 12 errors, the baseline set; 723 passed (681 + 42 new in `test_shot_reservations.py`), 5 skipped. Mutation checks (to_thread removed, each semaphore removed, upsert lock removed, pace lock removed) each fail their test.

Pass 1, full-diff re-runs (blind, edge-case). New findings only; the rest repeated the rows above.

**Patched:**
- **medium** -- Planning could demote a Veo shot that already had Veo attempts. On a resume that drops its ladder state and steps around its retry-ceiling halt. Shots with Veo attempts on record are no longer demotion candidates. Test added.
- **low** -- A result with no usable Claude cost halted and charged nothing. It is now charged its reservation, like a crash. Test added.
- **low** -- The planning log said "reserved" for a check that holds nothing. Reworded; the spine now says planning checks without holding.
- **low** -- A transient backoff slept while still holding its reservation. It is released before the sleep.
- **low** -- The ProductionInputs planning halt was untested. Test added.
- **low** -- The README didn't say the new defaults can demote or stop a run before launch. Added.

**Rejected:**
- **low** -- "Planning holds nothing, so an earlier shot's retries can starve a later shot": the story says planning computes the reservations and each attempt, retries included, acquires its own. This follows the story. Now stated in AD-15's amendment.
- **false** -- A crashed attempt releasing while a worker thread still bills: the crash path debits the whole reservation first.
- **low** -- An `INVALID_OUTCOME` halt that overdraws is still `unclassified`, as before this story.
- **low** -- Gemini pacing (5 s) runs inside the slot: real calls outlast the interval, so two still overlap.
- **low** -- Stale or corrupt entries reported under `production_planning`: the halt still fires before any paid call.
- **false** -- The `session_id` "Story 4.4" comment: the decision is recorded in 4.3, and concurrency arrives in 4.4.
- **low** -- Caps are set only in `run_stages`: that is the only production entry point.

**Deferred:** an attempt whose overshoot overdraws the pool halts before upserting its already-paid asset. That order predates this story.

## Design Notes

- **Guard limit.** A reservation is `cap + external`. The query's `max_budget_usd` already holds the cap, so the guard is given the rest of the unspent reservation: `external` at query start. Giving it the whole reservation would let paid calls spend the Claude share while Claude spends it too, breaking NFR4's "one turn of overshoot" bound.
- **Ledger lifetime.** The ledger's only state beyond the manifest is its open reservations, and every reservation is released (try/finally) before its attempt ends. Each shot stage opens a ledger on the run's manifest; with shots sequential this equals one run ledger. 4.4's single production step will hold one.
- **Planning** computes first-attempt reservations but doesn't hold them; each attempt, first included, reserves when it starts.

## Verification

**Commands:**
- `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q -p no:cacheprovider` -- expected: 5 failed / 12 errors (baseline set), more passed.
