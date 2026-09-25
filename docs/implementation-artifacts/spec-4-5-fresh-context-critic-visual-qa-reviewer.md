---
title: 'Story 4.5: Fresh-context critic and visual-QA reviewer'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: 'afbe8f49e8ae5ad5ef701950d7be26708e049465'
route: 'full'
review_loop_iteration: 0
context:
  - '{project-root}/docs/epics.md'
  - '{project-root}/docs/implementation-artifacts/spec-4-4-concurrent-shot-launch-failure-scoping.md'
  - '{project-root}/docs/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Narration and every generated seed, clip and still are approved only by the agent that produced them, so quality approval is the generator grading its own work.

**Approach:** Python sequences two new fresh-context `query()` agents. A story critic runs after a fresh story attempt validates, through a new `review_before_persist` callback on `run_bounded_agent_stage`; REVISE feeds the next attempt. A visual-QA reviewer runs inside each shot task after the producer approves and before the upsert; a rejection becomes that attempt's `structured_output.failure` and follows the producer-QA-failure path, with the Veo ladder advancing on a likeness `seed_qa` rejection.

## Boundaries & Constraints

**Always:**
- Story AC (`docs/epics.md`, "Story 4.5"), FR11, FR12, NFR1, NFR5, NFR8 and the Additional Requirements are binding. All 4.2–4.4 behaviour stays: reservations, guard, vendor refusals giving back the attempt, crash charging, stamping, scoping, `failed_shots`.
- The critic never runs inside `validate_contract` or on the resume-skip path. Its cost goes into `manifest.budget_spent_usd`.
- Both agents follow the 4.1 pattern: `book-reels:<skill>` names, absolute plugin path, `setting_sources=[]`, `assert_isolated`, `check_init_message`, a `PreToolUse` Read guard. Critic: story-craft + source-fidelity, Read confined to the plugin. Reviewer: visual-qa + character-likeness-qa, Read confined to the plugin plus that shot's asset files and character references.
- Vendor-refusal classification applies to both new queries; a refusal gives back the attempt and halts `vendor_limit`.
- New settings `CLAUDE_REVIEWER_CAP_USD` (default $1.00, generous, not tuned), `REVIEWER_MODEL`, `CRITIC_MODEL` (both default to the Claude model). Each shot attempt's reservation includes one reviewer call; the story agent's budget is remaining minus the cap; critic and reviewer run with `max_budget_usd` = the cap.
- Contracts change only additively (`ProductionAssetEntry.reviewer_summary`, default `""`).

**Never:** Run the critic as a subagent. Touch the user's dirty-tree output. Make live vendor calls. Tune the budget defaults.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Critic approves | valid plan | persisted; critic cost charged | none |
| Critic revises | valid plan, REVISE | not persisted; issues are next attempt's feedback | ceiling → `retry_ceiling` |
| Resume skip | valid persisted plan | no critic call | none |
| Too little budget for critic | remaining < cap | no critic call, nothing persisted | `budget_exhausted` |
| Reviewer approves | producer SUCCESS | upsert with `reviewer_summary` | none |
| Reviewer rejects Veo seed likeness | `seed_qa`, `CHARACTER_LIKENESS` | attempt record's `structured_output.failure`; rung +1; retry | ceiling → shot-specific |
| Reviewer rejects still | `still_qa` | attempt record failure; plain retry | ceiling → shot-specific |
| Reviewer vendor refusal / crash / bad verdict | — | shot fails systemic | `vendor_limit` (attempt given back) / `unclassified` |

</frozen-after-approval>

## Code Map

- `orchestrator/run.py`
  - `run_bounded_agent_stage` (173-330): add `review_before_persist` + `review_cap_usd`; the review runs after `validate_contract` passes on a fresh attempt, before `finalize_before_persist`/persist.
  - `run_narration_stage` (366): wire the critic.
  - `shot_attempt_reservation` (589): Claude share = shot cap + reviewer cap. Producers pass `settings.claude_shot_attempt_cap_usd` as the query's `max_budget_usd`, not the reservation's Claude share.
  - `_produce_veo_shot` (1695) / `_produce_still_shot` (2028): a new `_review_shot_asset` helper, called on SUCCESS after the overdraw check and before the upsert.
  - Ladder hooks, now at `_forces_simpler_character_wording` (1134), `SEED_QA_CHARACTER_MARKERS` (1122), `_prior_character_rejections` (1149). Reuse them unchanged.
- `orchestrator/agents/_skills.py` -- generalise the Read guard to also allow exact files. The existing `plugin_read_guard()` behaviour stays.
- `orchestrator/agents/_guard.py` -- record the ladder-outcome text that image tools return. That note tells the reviewer which rung was actually drawn.
- `orchestrator/tools/gemini_tools.py` -- reuse `character_reference_paths_for_asset`, `describe_ladder_outcome`, `CHARACTER_PROMPT_LEVELS`; no change.
- New `orchestrator/agents/story_critic.py` and `orchestrator/agents/visual_reviewer.py`, following `story_agent.py`.
- New `orchestrator/contracts/review.py` -- `StoryCritiqueContract`, `VisualQAVerdictContract`.
- `orchestrator/contracts/production_assets.py` -- `reviewer_summary`.
- `orchestrator/settings.py` -- three settings.
- `orchestrator/tests/conftest.py` -- autouse stubs that approve at $0 on `run.critique_story` / `run.review_visual_asset`, so existing stage tests never reach the SDK.

## Tasks & Acceptance

**Execution:**
- [x] settings, contracts, `_skills.py`, `_guard.py` -- the additive pieces.
- [x] the two agent modules.
- [x] `run.py` -- the bounded-stage callback, the narration wiring, the reservation, and the reviewer in both producers.
- [x] `conftest.py` stubs; `orchestrator/tests/test_reviewers.py` -- every item in the story's test list.
- [x] Fix existing tests that the new reservation or budget arithmetic breaks, and log each one.
- [x] `ARCHITECTURE-SPINE.md` AD-18; `README.md` settings.

**Acceptance Criteria:**
- Given the full suite, when it runs, then the failing set equals the baseline (5 failed, 12 errors) and the new tests pass.

## Implementation Notes

- Implemented directly by the orchestrating session, not a fresh subagent. It already held the investigation, and earlier agents were killed by a no-progress watchdog.
- **Where each reviewer runs.**
  - The story critic runs in `run_bounded_agent_stage` after `validate_contract` passes on a fresh attempt, and before `finalize_before_persist`/persist (`run_review`).
  - The shot reviewer (`_review_shot_asset`) runs in `_produce_veo_shot`/`_produce_still_shot` in this order: the producer's cost is debited, the attempt record is written, the overdraw check runs, then the review, a second overdraw check (`_review_overshoot`), and finally the upsert.
- **Budget.**
  - `shot_attempt_reservation` returns (shot cap + reviewer cap, paid calls). The producer query still gets `claude_shot_attempt_cap_usd`. The `PaidToolGuard` limit (unspent less the Claude share) excludes the reviewer's share too.
  - The story agent gets remaining minus the cap; no attempt starts if that is ≤ 0. The critic runs only when the cap is left, and its reported cost is added to `budget_spent_usd`.
  - A reviewer crash, or a reviewer with no usable cost, is charged the reviewer cap.
  - `CLAUDE_REVIEWER_CAP_USD` defaults to $1.00: generous, not tuned.
- **Expected added spend per run:** at most (1 + story retries) × critic cap, plus (shot attempts whose producer approved) × reviewer cap. Reservations hold one reviewer cap for every shot attempt.
- **Accepted cost:** the Veo producer makes the seed and the clip in one session (`veo_agent.py`), so a reviewer's `seed_qa` rejection arrives after the clip is already paid for. The producer's own seed QA still gates the clip.
- **Rung the reviewer is told.** `PaidToolGuard.image_notes` keeps the non-JSON text blocks of successful image calls. `_reviewer_character_context` matches them against `describe_ladder_outcome` to learn the rung actually drawn, falling back to the start rung. At the no-character rung, the reviewer gets no references.
- **Failure handling.**
  - A reviewer vendor refusal gives back the attempt and fails the shot with `vendor_limit` (systemic). The Claude CLI already retries 429s, so there is no extra backoff.
  - A reviewer skill leak, crash, missing cost or invalid verdict is a systemic failure. Its attempt is not given back, because the producer's paid work ran.
  - A critic crash or unusable verdict consumes the attempt, with feedback. A critic skill leak gives the attempt back and halts. A critic vendor refusal halts with `vendor_limit` and gives the attempt back.
- **Tests changed:**
  - `test_preflight.make_settings` now pins `claude_reviewer_cap_usd=0.0`, so every earlier story's budget arithmetic is unchanged. With the $1 default, 23 reservation, demotion and story-budget tests failed only on arithmetic. The reviewer's share is tested with a real cap in `test_reviewers.py`.
  - `conftest.py` gains an autouse fixture that stubs `run.critique_story`/`run.review_visual_asset` to approve at $0, so no existing stage test reaches the SDK through them.
- **Suite:** 5 failed / 12 errors, the baseline set. Passed went from 743 to 772, with 29 new tests in `test_reviewers.py`; 5 skipped.
- **Mutation checks.** Each of these mutants makes at least one test fail:
  - the reviewer taken out of the reservation;
  - every reviewer verdict treated as an approval;
  - the critic's cost not charged.

## Spec Change Log

## Review Triage Log

The build session hit the usage limit during review, so triage was finished by the orchestrating session from the blind-hunter layer's findings. The other layers didn't complete.

- **The critic saw the writer's `quality_review` (contradicted AD-18).** Fixed. The draft is dumped with `exclude={"quality_review"}`, the prompt line about it is gone, and a test covers it.
- **A reviewer skill leak kept the shot's attempt.** Fixed. The attempt goes back as on the story path, with a test.
- **A free-text likeness code could leave the ladder stuck.** Fixed. `_reviewer_failure_code` pins any code naming likeness, character or face to `CHARACTER_LIKENESS`, with a test.
- **The visual reviewer's `max_turns=16` was tight** for seed + previews + references + skill loads. Raised to 24; spend is still bounded by `CLAUDE_REVIEWER_CAP_USD`.
- **The prompt invited minor notes on an APPROVE, but they were discarded.** Fixed by dropping that line from the prompt.
- **README warning** that the per-attempt review reservation can demote Veo shots: added.
- **Rejected: critic crash / no-cost charging $0 and halting.** This matches the bounded stage's existing treatment of the story agent itself (a plain exception costs nothing; no usable cost halts). The shot path charges the cap because its reservation model does.
- **Rejected: reviewers read `load_settings()`.** Every pipeline agent does, and `load_settings` defaults both reviewer models to `CLAUDE_MODEL`.
- **Deferred** (in `deferred-work.md`): persisting reviewer-pending output so a resume reruns only the review; frames-only clip review; structured ladder-rung reporting from the image tool.

## Design Notes

- **What the reviewer sees.** It gets the asset files, which are the seed and the preview frames for Veo, or the still. It also gets the character references (`character_reference_paths_for_asset`) and the ladder note of the rung the image tool actually used. The guard captures that note from the tool response. The fallback is the start rung for Veo and 0 for stills. At the no-character rung, it gets no references. It never sees the producer's `qa_summary`, so its context stays fresh.
- **Rejection record.** The attempt file is rewritten:
  - `structured_output` becomes a FAILURE outcome, whose failure has `produced_by="orchestrator"`, the reviewer's stage and code, and `retryable=True`;
  - the producer's own outcome is kept under `producer_outcome`, next to `review` and `reviewer_cost_usd`;
  - `seed_prompt_level` stays, so `_prior_character_rejections` sees the rejection on resume.
- **Critic failure.** A crash or an unusable verdict consumes the attempt with feedback, like a bounded-stage exception today. A skill leak halts and gives the attempt back. A vendor refusal halts with `vendor_limit` and gives the attempt back.

## Verification

**Commands:**
- `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q -p no:cacheprovider` -- expected: 5 failed / 12 errors (the baseline set), more passed.
