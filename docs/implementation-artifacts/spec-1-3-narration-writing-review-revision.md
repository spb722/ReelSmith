---
title: 'Autonomous Narration Writing, Review & Revision'
type: 'feature'
created: '2026-09-16'
status: 'done'
route: 'full'
review_loop_iteration: 0
baseline_commit: '7a1ec5b342d1e36a988a57fd023751cde90a5083'
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Narration today is `story_director.py` (Gemini draft) piped into `story_quality_loop.py` (separate Gemini reviewer + reviser calls, up to 4 iterations) -- two Gemini text calls per iteration, run and watched by hand, and a ceiling-exhaustion case that silently writes a "best-effort" file and exits 0 instead of halting.

**Approach:** Add `story_agent`, a Claude agent that writes, reviews, and revises narration in one self-correcting loop (reusing `asset_analyst`'s retry/halt/resume shape from Story 1.2), producing a schema-and-quality-validated `FinalStoryPlanContract`. No Gemini text call anywhere in this path (AD-1).

## Boundaries & Constraints

**Always:** `story_agent` runs only once a validated `AnalyzedAssetsContract` exists. Every result validates against `FinalStoryPlanContract` -- schema shape **and** the numeric quality thresholds (general scores >= 8, `source_fidelity`/`internal_consistency` >= 9, mirroring `story_quality_loop.py`'s proven bar) both enforced as pydantic validators, so passing validation *is* passing the quality bar (AD-4: schema validity alone is never approval). A failing attempt self-corrects with feedback, ceiling 4 (AD-5), reusing Story 1.2's exact retry/halt/resume pattern. Ceiling exhaustion halts via `build_failure_report`/`write_failure_report` (stage id `"story_agent"`) -- unlike the legacy script, it never silently writes a best-effort file and exits 0. A persisted valid, current-sources `FinalStoryPlanContract` skips `story_agent` entirely.

**Never:** Implement `visual_agent`/`voice_agent` (1.4/1.5) or full cross-stage resume (1.5). Modify `story_director.py`, `story_quality_loop.py`, `review_story_plan.py`, `revise_story_plan.py`, or `review_revised_story_plan.py` in place -- left unmigrated per the spine's Deferred decision. Call any Gemini text model for writing, reviewing, or revising. Invoke `asset_analyst` via `agents={}` + `extra_args={"agent": ...}` -- verified in Story 1.3 that this silently drops `output_format`; run `story_agent`'s prompt as the top-level session instead.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh run, valid `AnalyzedAssetsContract`, no prior narration | Ingest + asset_analyst already done | `story_agent` runs, produces a validated `FinalStoryPlanContract` | No error |
| No validated `AnalyzedAssetsContract` yet | `asset_analyst` stage not yet passed | `story_agent` is not invoked; orchestrator halts/continues at the earlier stage as today | No error (existing Story 1.2 behavior) |
| Attempt fails schema or quality thresholds | Word count/duration out of range, a score below its threshold, invalid asset id, etc. | Self-correct retry with feedback, up to 4 attempts | Logged, no halt yet |
| Retry ceiling exhausted | 4 failed attempts | Run halts at `story_agent` | Failure report (stage id, contract, attempts, timestamp, partial paths); no best-effort file, no notification |
| Persisted valid, current-sources contract exists | Re-invocation, same reel | `story_agent` skipped | Proceeds on persisted contract, no new spend |
| Persisted contract lacks `produced_by` or references stale/foreign asset ids | Legacy `story_quality_loop.py` output, or sources changed | Treated as no persisted contract | `story_agent` runs for real (same Gemini-era-skip guard as Story 1.2, AD-1) |

</frozen-after-approval>

## Code Map

- `story_director.py:140-424` -- `STORY_PLAN_SCHEMA`: title/thesis/strategy, target duration+word count, hook, full `narration_script`, `voice_direction` (persona/tone/pace/delivery notes), `story_arc` (5-beat structure), `scenes[]` (sequence, role enum, duration, narration, `source_asset_ids[]`, visual intent/treatment enum, impact text), `unused_assets[]`, `source_integrity_notes[]` -- shape reference for `FinalStoryPlanContract`; its Gemini call is never reused (AD-1).
- `story_director.py:774-864` + `story_quality_loop.py:747-841` -- mechanical checks to port as pydantic validators: word count 90-115, duration 40-50s (+/-2), continuous scene sequence, scene narration joined reconstructs `narration_script`, every `source_asset_ids` entry is a known asset id.
- `story_quality_loop.py:20-45,907-1073` -- the proven quality bar (`MAX_ITERATIONS=4`, scores >= 8, `source_fidelity`/`internal_consistency` >= 9) and the `verdict`/`ready_for_voice_generation`/`confidence`/`scores` shape -- port as validator constants and `QualityReview` shape, not re-tuned. Only the APPROVE branch matters; a REVISE-equivalent result is just a failed attempt, retried like Story 1.2's schema failures.
- `metadata/final_story_plan.json` (real, pre-existing, from a manual `story_quality_loop.py`/Gemini run, dated 2026-09-14) -- schema-shaped but has no `produced_by`; without a provenance gate this would silently satisfy resume forever, exactly Story 1.2's discovered bug. `FinalStoryPlanContract.produced_by: Literal["story_agent"]` (required, no default) closes it from the start this time.
- `orchestrator/contracts/analyzed_assets.py` -- read-only input; `story_agent` reads the persisted `metadata/analyzed_assets.json` (`AnalyzedAssetsContract`) as its source material and as the valid-asset-id set for `source_asset_ids` validation.
- `orchestrator/agents/asset_analyst.py` -- the exact pattern to mirror: `system_prompt=` (never `agents=`/`extra_args={"agent": ...}`, which silently drops `output_format` -- verified live in Story 1.2), `output_format={"type": "json_schema", ...}`, `max_buffer_size` raised proactively (a multi-scene narration-plus-quality-review payload is not tiny either), remaining-budget accounting (`settings.max_budget_usd - manifest.budget_spent_usd`).
- `orchestrator/run.py` -- `run_screenshot_stage`'s ingest/resume-check/retry-ceiling/halt shape is about to be duplicated a second time (and a third and fourth in Stories 1.4/1.5); extract the shared bounded-retry-with-resume control flow into one helper both stages call, parameterized by stage id, contract type/parser, persisted-file path, and the per-attempt agent-call function -- reduces the risk of the buffer-size/`--agent` class of bug being fixed in one copy and not the other.
- No `orchestrator/contracts/final_story_plan.py` or story-writing prompt exist yet -- created fresh here.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/contracts/final_story_plan.py` -- `FinalStoryPlanContract` (pydantic): the `STORY_PLAN_SCHEMA` shape plus `produced_by: Literal["story_agent"]` and `quality_review` (`verdict: Literal["APPROVE"]`, `ready_for_voice_generation: Literal[True]`, `confidence: float` in [0,1], `scores: dict[str, int]` each in [1,10]); validators enforce word-count/duration ranges, scene sequence continuity, narration reconstruction, and the score thresholds (`MIN_APPROVAL_SCORE`/`MIN_SOURCE_FIDELITY_SCORE`/`MIN_INTERNAL_CONSISTENCY_SCORE`) so contract validity *is* quality-bar validity (AD-4).
- [x] `orchestrator/contracts/final_story_plan.py` -- `validate_sources(analyzed_asset_ids)` method: every `source_asset_ids`/`unused_assets[].asset_id` reference must be a known current asset id (subset, not full coverage -- unlike Story 1.2, not every asset need be used) -- the resume-staleness guard.
- [x] `orchestrator/agents/story_agent.py` -- `AgentDefinition` (documentation) whose prompt/tools are lifted onto the top-level session (mirroring `asset_analyst.py` exactly); writes narration from the analyzed-assets bundle, self-reviews against the quality bar, and revises within its own reasoning before returning -- no Gemini call anywhere (AD-1).
- [x] `orchestrator/run.py` -- extract the shared bounded-retry/resume/halt helper from `run_screenshot_stage`; add `run_narration_stage` (or equivalent) using it, gated on a validated `AnalyzedAssetsContract` already existing; wire it into `main()` after the screenshot stage succeeds.
- [x] `orchestrator/tests/test_story_agent.py` -- retry-to-halt, skip-on-persisted-contract, stale/foreign-source rejection, and quality-threshold-rejection paths, SDK call mocked (mirroring `test_asset_analyst.py`'s structure).
- [x] `orchestrator/tests/test_final_story_plan_contract.py` -- each mechanical validator (word count, duration, sequence, reconstruction, asset ids) and each quality threshold, individually rejecting an invalid contract.

**Acceptance Criteria:**
- Given `MAX_BUDGET_USD` already exhausted, when the orchestrator reaches `story_agent`, then it halts via the failure-report path before any Claude call (AD-7).
- Given a `FinalStoryPlanContract` whose `verdict` is `"APPROVE"` but a score falls below its required threshold, when it is validated, then validation fails (schema validity alone is never approval, AD-4) and the attempt is treated as failed, not persisted.
- Given four consecutive invalid/failed attempts, when the ceiling is reached, then the run halts with `attempt_count == 4` and no `FinalStoryPlanContract` is persisted -- never the legacy behavior of writing a best-effort file and exiting 0.

## Implementation Notes

- **Shared retry/resume/halt helper.** Extracted `run_bounded_agent_stage` from `run_screenshot_stage` (parameterized by stage id, contract class, persisted-file path, `validate_contract` callback, attempt-file prefix, and a `call_agent` closure); both `run_screenshot_stage` and the new `run_narration_stage` call it. `validate_contract` does double duty for both the resume-staleness check on a persisted file and the post-attempt quality/source-id check -- one callback, matching how each contract's own `validate_sources` is already used in both places.
- **`story_agent` has no tools at all**, not even `Read`: narration is written entirely from the `AnalyzedAssetsContract` bundle `run_narration_stage` passes in the prompt, so the absence of any tool (rather than an allow-list) is what forecloses reaching Gemini/another model/a script (AD-1), same intent as `asset_analyst`'s `tools=["Read"]` restriction but tighter since no file access is needed here.
- **`quality_review.verdict`/`ready_for_voice_generation` are schema-locked** to `Literal["APPROVE"]`/`Literal[True]` per the spec's own contract shape. Because `output_format` forces the JSON schema, Claude cannot actually emit a dissenting `"REVISE"` through structured output -- the real gate is exclusively the nine `scores` values (all required, each >=8, `source_fidelity`/`internal_consistency` >=9). The prompt leans on this explicitly, asking Claude to self-score honestly since an honest low score just triggers the orchestrator's own retry-with-feedback loop, while an inflated score would ship ungrounded narration.
- **Existing tests needed updating, not just new ones**, as a direct consequence of wiring `run_narration_stage` into `main()` after screenshot-stage success: `test_asset_analyst.py`'s fixture now stubs `run_narration_stage` (tracking calls, asserted empty on every halt-path test) so those tests keep exercising `asset_analyst` in isolation without triggering a real Claude call for narration; `test_preflight.py`'s `test_run_main_returns_zero_when_preflight_passes` now stubs both stages.
- **Review pass 1 findings, all patched:** `QualityReview.score_thresholds` now requires all nine named `REQUIRED_SCORE_DIMENSIONS` present (not just whichever keys happen to be supplied); `target_word_count`/`target_duration_seconds` are overwritten post-validation from the actually-computed word count/scene-duration sum (`story_quality_loop.py:709-711`'s "never trust the model's own arithmetic" behavior, ported as a `model_validator`); `DURATION_TOLERANCE_SECONDS` set to `0` to match the strict `deterministic_checks` `duration_range` gate that actually governed legacy `APPROVE` (not `story_director.py`'s looser ±2 pre-review draft check); `Scene.narration` requires `min_length=1`; `validate_sources` now rejects an asset id claimed by both `source_asset_ids` and `unused_assets`.
- **Real-run verification is partial.** One real, unmocked `python -m orchestrator.run source_images` run correctly skipped `asset_analyst` (already validated), correctly rejected the pre-existing legacy `metadata/final_story_plan.json` (no `produced_by`/`quality_review`) as stale, and then exercised `story_agent`'s real retry/halt path end-to-end -- but every one of the 4 attempts came back as `"You've hit your session limit · resets 11pm (Asia/Calcutta)"` (an account-level Claude usage cap external to this repo, not a code defect), so ceiling exhaustion and the failure-report halt were proven for real while a genuine source-grounded `APPROVE` narration was not. That half of this verification step remains outstanding until the cap resets.

## Spec Change Log

## Review Triage Log

**Pass 1** (verdicts: 2 medium, 5 low, 2 defer, 3 false/reject)

- `orchestrator/contracts/final_story_plan.py:88-107` — **medium** — `QualityReview.score_thresholds` only validates dict keys actually present in `scores`; a plan reporting only `source_fidelity`/`internal_consistency` (omitting the other 7 named dimensions) passes `model_validate`, defeating AD-4's "passing validation is passing the quality bar". No test exercises a missing-dimension `scores` dict. → **patch**
- `orchestrator/run.py:268-271` — **medium** — `main()` correctly short-circuits on `screenshot_result != 0` (verified by reading the code), but no test asserts `run_narration_stage` is never invoked on that path; the halt-path tests in `test_asset_analyst.py` stub it with a no-op that doesn't track calls. A regression here would burn a real paid Claude call after a halt undetected. → **patch**
- `orchestrator/contracts/final_story_plan.py:118-119` — **low** — `target_word_count`/`target_duration_seconds` are free-form fields Claude self-reports, never cross-checked against the real narration word count / summed scene durations. `story_quality_loop.py:709-711` always overwrote these with the actually-computed values instead of trusting the model. → **patch**
- `orchestrator/contracts/final_story_plan.py:26` — **low** — `DURATION_TOLERANCE_SECONDS=2` is looser than the check that actually gated `APPROVE` in the legacy pipeline: `story_quality_loop.py`'s `deterministic_checks` `duration_range` (the one enforced at `story_quality_loop.py:1170-1179`) is strict `40<=total<=50` with no tolerance; ±2 only appears in `story_director.py`'s looser pre-review draft check. → **patch**
- `orchestrator/run.py:1-4` — **low** — module docstring still describes a single-stage pipeline; doesn't mention the narration stage `main()` now runs after screenshot success. → **patch**
- This spec's `## Implementation Notes` — **low** — left empty despite `status: in-review` and all tasks checked; the template specifies it should record implementation decisions/surprises. → **patch**
- `orchestrator/contracts/final_story_plan.py:71` — **low** — `Scene.narration` has no `min_length=1`; an empty-narration scene could pass while sequence/duration/asset-id checks still hold. → **patch**
- `orchestrator/contracts/final_story_plan.py:165-179` — **low** — `validate_sources` never checks for overlap between `source_asset_ids` and `unused_assets`; the same asset id can be claimed both used and unused with no contradiction raised. → **patch**
- `CLAUDE.md` (whole file, in diff) — **defer** — already staged (`AM`) before `baseline_commit` was captured, from an unrelated prior session; any fix means editing an agent-context file, which routes to defer regardless of severity.
- `orchestrator/run.py` (`run_bounded_agent_stage`'s `previous_output=None` on first retry after an invalid persisted contract) — **defer** — behavior inherited unchanged from Story 1.2's `run_screenshot_stage`, preserved verbatim per this spec's own instruction to reuse that exact retry pattern; not caused by this story.
- `metadata/assets.json` (timestamp diff) — **false** — `generated_at_utc` is deterministically regenerated by the existing (Story 1.2) ingest tool on every invocation; expected, not a bad outcome this story introduced.
- `orchestrator/contracts/final_story_plan.py:66-69` vs `orchestrator/contracts/analyzed_assets.py:118-121` (`Scene.role` vs `possible_story_roles` vocabulary mismatch) — **false** — no code path couples the two enums; `possible_story_roles` is advisory input data read by Claude via the prompt/schema, never programmatically mapped into `Scene.role`. No demonstrated bad outcome.
- `docs/implementation-artifacts/epic-1-context.md` (`suggested_visual_treatment` advisory-vs-binding ambiguity for a future `visual_agent`) — **false** — concerns Story 1.4, explicitly out of this story's scope per the frozen Never section; `epic-1-context.md` is a cache that 1.4's own planning will refresh.
- `orchestrator/run.py:377-390` (`run_bounded_agent_stage`'s `contract_cls: type` / `validate_contract: Callable[[object], None]` typing) — **low, rejected** — no demonstrated harm (both call sites are correctly wired and covered by passing tests; a mismatch fails loudly at runtime), and tightening to generics is more than a direct correction.

## Design Notes

**One agent, one loop, not two Gemini calls.** `story_agent` collapses `story_director.py` (draft) + `story_quality_loop.py` (separate reviewer+reviser) into one Claude session per attempt; the orchestrator's retry loop (identical shape to Story 1.2) only re-invokes when a whole attempt's output still fails validation.

**Quality bar lives in the contract.** Folding the score thresholds into `FinalStoryPlanContract`'s own validators means one `model_validate()` enforces both "shaped right" and "good enough" (AD-4) -- no separate reviewer artifact, and a REVISE-equivalent result is just a failed attempt.

## Verification

**Commands:**
- `conda run -n kayak-video python -m pytest orchestrator/tests/test_story_agent.py orchestrator/tests/test_final_story_plan_contract.py -v` -- expected: all pass, no real API calls
- `conda run -n kayak-video python -m pytest orchestrator/tests -v` -- expected: full suite still passes after the `run_screenshot_stage` refactor
- One real, unmocked `python -m orchestrator.run source_images` run (as in Story 1.3's post-review round) -- confirms the pre-existing legacy `metadata/final_story_plan.json` is correctly rejected, `story_agent` runs for real, and the produced narration is genuinely source-grounded (spot-check specific narration lines against the actual analyzed screenshots, not generic filler).
