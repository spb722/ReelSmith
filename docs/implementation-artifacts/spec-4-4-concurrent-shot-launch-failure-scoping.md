---
title: 'Story 4.4: Concurrent shot launch and failure scoping'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: '8e0bef48b61ee10435b78c23c66f6500985f38e5'
route: 'full'
review_loop_iteration: 0
context:
  - '{project-root}/docs/epics.md'
  - '{project-root}/docs/implementation-artifacts/spec-4-3-concurrency-safe-shot-plumbing-per-attempt-reservations.md'
  - '{project-root}/docs/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `run_stages` makes every Veo shot, then every still, one at a time, and the first shot that fails calls `_halt` itself, so its siblings never run and the reel takes the sum of every shot's time.

**Approach:** One production step replaces the Veo → stills pair. After 4.3's planning, it launches every pending shot as an asyncio task under a shot-level cap (new `SHOT_CONCURRENCY`, default 4) and 4.3's vendor caps. A shot returns its failure rather than halting. A shot-specific failure lets siblings finish. A systemic one stops new launches and new attempts, but never cancels one in flight. The step then writes one AD-6 report whose additive `failed_shots` lists every failed shot.

## Boundaries & Constraints

**Always:**
- Story AC (`docs/epics.md`, "Story 4.4"), FR5, FR6, FR8, NFR4, NFR5, NFR8 and the Additional Requirements are binding. All 4.2 and 4.3 behaviour stays as it is: reservations, the guard limit, vendor refusals giving back the attempt, crash charging, stamping, the ladder and per-shot retry ceilings.
- **Scope.** A failure is shot-specific only when it is a retry-ceiling exhaustion, or a non-retryable Veo or still failure contract (that covers a likeness refused on every rung and a non-retryable seed defect). Every other shot failure is systemic: `vendor_limit`, `budget_exhausted`, a skill-isolation leak, a crash, an invalid outcome, no usable cost, or a persistence failure. When the scope is unknown, stopping is the safe direction.
- **After a systemic stop:** no shot launches, and no running shot starts another attempt. Each attempt in flight runs to its end, and is persisted if it's approved. The shots that were stopped or never started aren't failures. The report names them, and a resume runs them.
- **Report.** With one failed shot, the envelope is that shot's, as today (stage id, contract, reason, attempts, paths, `reason_code`). With several, the stage id is `production`, and the `reason_code` is the first systemic one, or else the first shot's. `failed_shots` has the entries either way.
- Resume skips every persisted shot (AD-14), and `_prior_character_rejections` keeps the ladder state.
- Leave the budget defaults alone.

**Never:** Cancel an in-flight shot. Touch the user's dirty-tree output. Make live vendor calls. Change contracts other than additively.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| All succeed | N pending shots | They overlap under the caps. Every shot is persisted. | none |
| Shot-specific failure | shot 1 is non-retryable | Siblings finish and are persisted | One report, `failed_shots=[1]` |
| Two shot-specific | shots 1 and 2 fail | The rest finish | One `production` report with two entries |
| Systemic | shot 1 hits `vendor_limit` while shot 2 is in flight | Shot 2 finishes and is persisted. Unstarted shots never run. | One report, `vendor_limit`, resumable |
| Retry can't reserve yet | siblings still hold reservations | It waits for a release, then reserves | none |
| Retry can't reserve, none held | pool < need | Shot fails | `budget_exhausted` (systemic) |
| Resume | report above | Only the failed and unstarted shots run, with their ladder rung | as before |

</frozen-after-approval>

## Code Map

- `orchestrator/run.py`
  - `run_veo_stage` / `run_stills_stage` hold per-shot loops that call `_halt`. They become `_produce_veo_shot` / `_produce_still_shot`, which return a `ShotFailure`, `"done"` or `"stopped"`.
  - Pre-launch checks move into a new `run_production_stage`, with stage id `production`: plan and manifest loading, stale, mode and missing-entry checks, inputs, and a missing source asset.
  - `run_veo_stage` / `run_stills_stage` stay as single-mode entry points for testing or running one mode. Only `run_veo_stage` plans, as in 4.3.
  - `_handle_shot_vendor_refusal` gets a `release` callable, so its release before backoff wakes waiters.
  - `run_stages` has a single step, `run_production_stage`.
- `orchestrator/state/run_manifest.py` -- `FailureReport.failed_shots` (default `[]`). `build_failure_report` gets a `failed_shots` kwarg.
- `orchestrator/settings.py` -- add `shot_concurrency` / `SHOT_CONCURRENCY`, default 4, positive int.
- Reuse the 4.3 test helpers: the `chain` fixture and outcome builders (`test_full_chain_resume.py`), `reports`/`result_message` (`test_guard_hooks.py`), and `make_settings`/`write_plan` (`test_shot_reservations.py`).
- `main()` tests that stub `run_veo_stage`/`run_stills_stage` must stub `run_production_stage` instead. These are in `test_asset_analyst`, `test_visual_agent`, `test_voice_agent`, `test_preflight` and `test_full_chain_resume`.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/settings.py`, `orchestrator/state/run_manifest.py` -- add the setting and `failed_shots`.
- [x] `orchestrator/run.py` -- the production step, shot producers, scoping, the combined report, and the `run_stages` change.
- [x] `orchestrator/tests/test_concurrent_shots.py` -- every item in the story's test list, plus the release wait and the stop between attempts.
- [x] Existing tests -- re-point the stage stubs, and update any assertion the new semantics changes. Log each one in the notes.
- [x] `ARCHITECTURE-SPINE.md` -- add AD-17, take the item out of Deferred, amend AD-6 and AD-15. `README.md` -- add `SHOT_CONCURRENCY`.

**Acceptance Criteria:**
- Given the full suite, when it runs, then the failing set equals the baseline (5 failed, 12 errors), and the new tests pass.

## Implementation Notes

- Implemented directly by the orchestrating session, not a fresh subagent. It already held the investigation, and earlier agents were killed by a no-progress watchdog.
- **run.py.** `run_stages` now has one step, "Making the video clips and still pictures" (`run_production_stage`), in place of the Veo and stills pair.
  - The two per-shot loops moved unchanged into `_produce_veo_shot` / `_produce_still_shot`. Their `_halt` calls became `ShotFailure`s.
  - The pre-launch checks moved into `_run_shot_production`, and a pre-launch halt now has stage id `production`.
  - A missing analyzed source asset is checked for every shot before any launch.
  - An unexpected exception inside a shot becomes that shot's systemic failure, so `gather` never tears down its siblings.
- **Waiting on sibling holds.** Concurrency changes budget checks in two places, and both now wait for sibling holds (`_ShotLaunch.reserve` / `.overdrawn`):
  - a refused reservation;
  - the overshoot check after a debit.

  Each one waits while other attempts still hold unspent reservations, and is judged only when they don't. That matches the sequential outcome. `BudgetLedger.holding(besides=)` and `overdrawn(besides=)` ignore the attempt's own reservation, and any reservation that is already fully spent.
- **A stop wakes waiters.** A stop wakes every waiter, and a woken waiter never reserves after a stop.
- **Tests changed:**
  - These tests stubbed `run_veo_stage`/`run_stills_stage` by name; they now stub `run_production_stage`. Otherwise the real production step would have run in them:
    - `main()` tests in `test_asset_analyst`, `test_story_agent`, `test_visual_agent` and `test_voice_agent`;
    - four in `test_full_chain_resume`;
    - `test_preflight::test_run_main_returns_zero_when_preflight_passes`, whose step order is now `production` in place of `veo`, `stills`;
    - `test_voice_agent::test_all_sequential_stages_share_one_event_loop`, which now expects 6 stages, not 7.
  - `test_guard_hooks::test_a_pre_change_failure_report_dict_still_builds` now expects the new default `failed_shots: []`.
  - Every `run_veo_stage`/`run_stills_stage` test passes unchanged. Both are now single-mode entry points over the same engine.
- **Suite:** 5 failed / 12 errors, the baseline set. Passed went from 723 to 743, with 20 new tests in `test_concurrent_shots.py`; 5 skipped.
- **Mutation checks.** Each of these mutants makes at least one test fail:
  - no reservation wait;
  - no overdraw wait;
  - no stop;
  - no stop on a woken waiter;
  - no shot cap;
  - no stop between attempts;
  - lead by sequence;
  - planning off;
  - Veo retry ceiling scoped as systemic.

## Spec Change Log

## Review Triage Log

Pass 1: three layers (blind, edge-case, verification-gap).

**Patched** (tests added where behaviour changed):
- **medium** -- A reservation that woke up after a systemic stop could start a new paid attempt. This was reported by all three layers, and the verification-gap layer reproduced it. `reserve` now returns None once stopped. Test: `test_a_stop_ends_a_retry_that_is_waiting_for_budget`.
- **medium** -- The overshoot check counted sibling holds, and the attempt's own unspent hold, so concurrency could turn a coverable overshoot into a `budget_exhausted` that discards a paid asset. It now waits for sibling holds and ignores its own. Test: `test_an_overshoot_waits_for_sibling_holds_before_it_counts`.
- **medium** -- A mixed report's lead was picked by sequence, and nothing tested it. It is now the failure that stopped launches (`launch.stopped_by`). The reason also names shot-specific failures that a re-run won't fix by itself. Test: `test_a_mixed_report_is_led_by_the_systemic_failure`.
- **medium** -- Planning through `run_production_stage` was untested. Tests added for a demotion and for an "even stills can't fit" halt.
- **medium** -- A Veo retry-ceiling failure alongside siblings was untested. Test added.
- **low** -- `holding()` counted fully spent reservations. It now counts only unspent ones.
- **low** -- A crash entry lost its attempt count and attempt files. They are now read from the manifest and the state dir.
- **low** -- The README and AD-17 said a re-run redoes failed shots. They now say a retry-ceiling shot needs its ceiling raised first.
- **low** -- Stray blank lines after `_produce_still_shot`.

**Rejected:**
- **false** -- "`session_id` becomes arbitrary": last-writer-wins was decided and documented in 4.3 (AD-10).
- **false** -- "AD-10 isn't amended for concurrent writers": 4.3's AD-10 amendment already puts every manifest save and upsert under `STATE_LOCK`, which `save_run_manifest` takes itself.
- **false** -- "`shot_concurrency=0` hangs": `load_settings` rejects values below 1. Only tests build `Settings` directly.
- **low** -- Stopped shots appear only in the reason text. The frozen spec says the report "names them", and it does.
- **low** -- A combined report's contract is named `ProductionAssetsContract`. That is cosmetic; the entries carry each shot's own contract.
- **low** -- A crashed lead that is `unclassified` makes the envelope non-resumable. Each entry carries its own `resumable`.
- **low** -- A wait can end in `budget_exhausted` anyway. That is the designed rule: it fails only once nothing is held.
- **low** -- A stop during a transient backoff is noticed after the sleep (at most 60 s, bounded).
- **low** -- No scope test for skill isolation and no-cost. Both are the default, systemic scope.
- Spec-text findings (Code Map helper names, the baseline wording) are rejected: the fix would edit this spec.

**Deferred:** none.

## Design Notes

- **Planning holds nothing, and concurrency needs no pre-hold.** Every attempt reserves before it starts, so the ceiling holds whatever the launch order (NFR4). Pre-holding first attempts would be a new policy: first tries would beat retries when the budget runs short. What concurrency does add is siblings' worst-case holds that they mostly give back. So a reservation that fails while other attempts hold one waits for a release and tries again. It fails only when nothing else is held, which is the same condition as sequential. There's no deadlock, because a waiting shot holds nothing.
- Veo shots launch first, then stills, each in sequence order. Veo takes the longest.

## Verification

**Commands:**
- `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q -p no:cacheprovider` -- expected: 5 failed / 12 errors (the baseline set), and more passed.
