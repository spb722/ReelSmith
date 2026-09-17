---
title: 'Autonomous Visual & Shot-Mode Planning'
type: 'feature'
created: '2026-09-17'
status: 'done'
route: 'full'
review_loop_iteration: 0
baseline_commit: 'fe5d624a2cf50e038f9fd2705328f9ada60c7189'
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Visual/shot planning today is `visual_director.py`, a single-pass Gemini call with no scored self-review loop, whose shot schema references subtitle-cue ids and audio-derived start/end timestamps that don't exist yet at this point in the new pipeline (subtitle cues are Story 1.5's concern) -- and it has no producer-provenance field, so its pre-existing manual output (`metadata/visual_plan.json`) would silently satisfy resume forever.

**Approach:** Add `visual_agent`, a Claude agent (same shape as `story_agent`/`asset_analyst`) that reads the validated `FinalStoryPlanContract` + `AnalyzedAssetsContract` and produces one `VisualPlanContract` shot per scene, assigning each shot's binding `generation_mode` (STILL vs VEO) plus visual direction, self-reviewing its own output before returning -- no Gemini/Veo call anywhere in this path (AD-1).

## Boundaries & Constraints

**Always:** `visual_agent` runs only once a validated `FinalStoryPlanContract` exists. Shots correspond 1:1 to `FinalStoryPlanContract`'s scenes by sequence (AD-2 -- no independent timing source exists pre-audio). Every result validates against `VisualPlanContract` -- schema shape **and** the numeric quality thresholds (`source_fidelity`, `generation_mode_appropriateness`, `visual_coherence`, `narrative_alignment`, `internal_consistency`, each required and >=8, `source_fidelity`/`internal_consistency` >=9 -- freshly designed this story to mirror `story_agent`'s pattern, since `visual_director.py` has no scored-reviewer precedent), both enforced as pydantic validators (AD-3, "passing validation is passing the quality bar") -- reusing `run_bounded_agent_stage` (Story 1.3), ceiling 4. `generation_mode` must be deterministically consistent with `visual_treatment` (AD-4: `AI_VIDEO_CANDIDATE`/`MIXED` may be `VEO`; all other treatments must be `STILL`). Ceiling exhaustion halts via the shared failure-report path (stage id `"visual_agent"`). A persisted valid, current-sources `VisualPlanContract` skips `visual_agent` entirely.

**Never:** Implement `veo_agent`/`stills_agent` (Epic 2) or `voice_agent`/full cross-stage resume (1.5). Modify `visual_director.py`, `prepare_veo_seed_images.py`, `generate_veo_clips.py`, or `prepare_remotion_stills.py` in place. Call Gemini or Veo for planning or generation -- `visual_agent` only assigns metadata (AD-1). Reference subtitle cue ids or audio-derived start/end timestamps (AD-2).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh run, valid `FinalStoryPlanContract`, no prior visual plan | narration stage already done | `visual_agent` runs, produces a validated `VisualPlanContract` | No error |
| No validated `FinalStoryPlanContract` yet | narration stage not yet passed | `visual_agent` not invoked; orchestrator halts/continues at the earlier stage | No error (existing behavior) |
| Attempt fails schema, consistency, or quality thresholds | Shot/scene count mismatch, bad generation_mode/visual_treatment pairing, a score below threshold, invalid asset id | Self-correct retry with feedback, up to 4 attempts | Logged, no halt yet |
| Retry ceiling exhausted | 4 failed attempts | Run halts at `visual_agent` | Failure report; no best-effort file, no notification |
| Persisted valid, current-sources contract exists | Re-invocation, same reel | `visual_agent` skipped | Proceeds on persisted contract, no new spend |
| Persisted contract lacks `produced_by`, or shot sequences/asset ids don't match the current `FinalStoryPlanContract` | Legacy `visual_director.py` output, or narration changed | Treated as no persisted contract | `visual_agent` runs for real |

</frozen-after-approval>

## Code Map

- `visual_director.py:96-249` -- `RESPONSE_SCHEMA`: shape reference only. Its `visual_treatment` enum (`USE_EXISTING_ART`/`CROP_AND_RECOMPOSE`/`SUBTLE_ANIMATION`/`TEXT_LED`/`AI_VIDEO_CANDIDATE`/`MIXED`) matches `Scene.suggested_visual_treatment` exactly and is reused; its `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids`/`asset_utilization` are legacy-only and not ported (AD-2).
- `visual_director.py:374-466` (`validate_shots`) -- sequence-continuity and known-asset-id checks are the portable mechanical checks; the time-contiguity/subtitle-coverage checks are not portable (AD-2). No `MAX_ITERATIONS`/quality-score loop exists here to port (unlike `story_quality_loop.py`) -- `visual_agent`'s `quality_review` dimensions are freshly designed this story, mirroring `story_agent`'s pattern (see Boundaries & Constraints).
- `metadata/visual_plan.json` (real, pre-existing manual run, 8 shots) -- schema-shaped but no `produced_by`; same silent-resume risk Stories 1.2/1.3 already discovered. `VisualPlanContract.produced_by: Literal["visual_agent"]` (required, no default) closes it (AD-6).
- `orchestrator/contracts/final_story_plan.py` -- read-only input; `Scene.sequence`/`source_asset_ids`/`visual_intent`/`suggested_visual_treatment`/`estimated_duration_seconds` drive per-shot planning and the 1:1 shot-to-scene mapping (AD-2).
- `orchestrator/contracts/analyzed_assets.py` -- read-only input; `AnalyzedAsset.production` (`story_art_region`, `ui_regions`, `recommended_crop_strategy`, `suggested_motion`, `vertical_video_suitability`) is the deterministic visual metadata `visual_agent` reasons over for frame composition/motion.
- `orchestrator/agents/story_agent.py` -- exact pattern to mirror: zero tools, `system_prompt=` on the top-level session (never `agents=`/`extra_args`), `output_format` json_schema, `max_buffer_size` raised, remaining-budget accounting.
- `orchestrator/run.py` -- `run_bounded_agent_stage` (built in Story 1.3 for exactly this reuse) already generalizes the retry/resume/halt shape; add `run_visual_stage` using it, gated on a validated `FinalStoryPlanContract`, wired into `main()` after the narration stage succeeds.
- No `orchestrator/contracts/visual_plan.py` or visual-planning prompt exist yet -- created fresh here.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/contracts/visual_plan.py` -- `VisualPlanContract` (pydantic): `produced_by: Literal["visual_agent"]`, `overall_visual_style: str`, `shots: list[Shot]` (min_length=1), `quality_review` (mirrors `QualityReview`'s shape/threshold pattern with the five dimensions fixed in Boundaries & Constraints); validators enforce shot-sequence continuity and exact 1:1 correspondence to `FinalStoryPlanContract`'s scene count, `generation_mode`/`visual_treatment` consistency (AD-4), and per-shot `source_asset_ids` non-empty.
- [x] `orchestrator/contracts/visual_plan.py` -- `validate_sources(story_plan)` method: shot sequences must exactly match the current `FinalStoryPlanContract`'s scene sequences (not merely a subset -- one shot per scene is required), and each shot's `source_asset_ids` must be a subset of that scene's own `source_asset_ids` -- the resume-staleness guard.
- [x] `orchestrator/agents/visual_agent.py` -- `AgentDefinition` mirroring `story_agent.py` exactly (zero tools, prompt lifted onto the top-level session); reads the `FinalStoryPlanContract` + `AnalyzedAssetsContract` bundle, assigns each shot's `generation_mode`/`visual_treatment`/frame composition/motion plan, self-reviews against the quality bar, revises within its own reasoning before returning.
- [x] `orchestrator/run.py` -- add `run_visual_stage` using the existing `run_bounded_agent_stage` helper, gated on a validated `FinalStoryPlanContract`; wire it into `main()` after the narration stage succeeds.
- [x] `orchestrator/tests/test_visual_agent.py` -- retry-to-halt, skip-on-persisted-contract, stale/foreign-source rejection (mirroring `test_story_agent.py`'s structure), SDK call mocked.
- [x] `orchestrator/tests/test_visual_plan_contract.py` -- each mechanical validator (sequence/count match, generation_mode consistency, asset ids) and each quality threshold, individually rejecting an invalid contract.

**Acceptance Criteria:**
- Given `MAX_BUDGET_USD` already exhausted, when the orchestrator reaches `visual_agent`, then it halts via the failure-report path before any Claude call.
- Given a shot whose `generation_mode` is `"VEO"` but `visual_treatment` doesn't permit it, when the contract is validated, then validation fails and the attempt is treated as failed, not persisted.
- Given four consecutive invalid/failed attempts, when the ceiling is reached, then the run halts with `attempt_count == 4` and no `VisualPlanContract` is persisted.

## Implementation Notes

- `Shot` fields were scoped to what the spec/code map actually names
  (`generation_mode`, `visual_treatment`, `source_asset_ids`, plus
  `frame_composition`/`motion_plan` from the code map and `shot_goal`/
  `text_overlay`/`source_support` carried over from `visual_director.py`'s
  portable fields). Legacy-only fields excluded per AD-2:
  `narrative_function`, `subtitle_emphasis_notes`, `transition_in`/`out`,
  `asset_utilization` — none of these were requested and several
  presuppose subtitle-cue timing that doesn't exist yet.
- `QualityReview` mirrors `FinalStoryPlanContract`'s shape but renames
  `ready_for_voice_generation` to `ready_for_generation` (voice generation
  isn't what this stage gates) and swaps in the five dimensions fixed by
  Boundaries & Constraints.
- The "exact 1:1 correspondence to `FinalStoryPlanContract`'s scene count"
  requirement (Tasks bullet) is enforced by `validate_sources(story_plan)`,
  not a self-contained `VisualPlanContract` model_validator — the contract
  alone has no access to the story plan's scene count. `run_visual_stage`
  always calls `model_validate` and `validate_sources` together for every
  real attempt (persisted-file check and post-attempt check both use the
  same `validate_contract` callback), so there's no path where the count
  goes unchecked in practice.
- Wiring `run_visual_stage` into `main()` after narration surfaced a real
  bug during verification, not just a test gap: the first full-suite run
  actually invoked the live Claude Agent SDK (352s runtime) because
  `test_asset_analyst.py`, `test_story_agent.py`, and `test_preflight.py`
  each stub earlier stages to return 0 without mocking the new stage that
  now runs after them. Fixed by adding a `run_visual_stage` stub (mirroring
  each file's existing `run_narration_stage`/`run_screenshot_stage` stub
  pattern) to all three files. Re-ran the full suite afterward: 124 passed
  in under a second, confirming no further real calls.
- The spec's third verification command (a real, unmocked
  `python -m orchestrator.run source_images` run to confirm the legacy
  `metadata/visual_plan.json` is rejected and `visual_agent` runs for real)
  was not executed. The repo's current `metadata/final_story_plan.json` is
  itself a legacy `story_quality_loop.py` manifest with no `produced_by`,
  so there is no real validated `FinalStoryPlanContract` yet to satisfy
  this stage's precondition, and the spec itself flags this check as
  possibly blocked by the session-usage-cap noted in Story 1.3. Verified
  statically instead that the existing legacy `metadata/visual_plan.json`
  (8 shots, no `produced_by`) fails `VisualPlanContract` validation.

## Spec Change Log

## Review Triage Log

**Pass 1** (verdicts: 1 medium, 4 low, 2 defer, 3 false/reject)

- `orchestrator/agents/visual_agent.py` (prompt) — **medium** — has no equivalent of `story_agent`'s "SOURCE UNCERTAINTY" instruction, even though `frame_composition`/`motion_plan`/`source_support` are drawn from the same `AnalyzedAsset.analysis.uncertainties` field `story_agent` explicitly hedges against; nothing stops a shot being grounded in a visual detail the analyst flagged as uncertain. → **patch**
- `orchestrator/contracts/visual_plan.py:51-55` — **low** — `Shot.shot_goal`/`frame_composition`/`motion_plan`/`source_support` have no `min_length=1`, unlike the analogous `Scene.narration` in `final_story_plan.py`; an empty string in any of these (the actual visual-direction content) still passes validation. → **patch**
- `orchestrator/contracts/visual_plan.py:91` — **low** — `overall_visual_style` is likewise unconstrained despite being the one field that must unify every shot's style/tone. → **patch**
- `orchestrator/tests/test_visual_plan_contract.py` — **low** — no test confirms `STILL` remains valid when paired with `AI_VIDEO_CANDIDATE`/`MIXED` (the permissive, less-obvious side of AD-4's asymmetric rule — only `VEO`'s restriction is tested). → **patch**
- This spec's `## Implementation Notes` — **low** — left empty despite `status: in-review` and all tasks checked. → **patch**
- `AGENTS.md` (pipeline-order line still describes the legacy Gemini-script chain; locked-artifacts line calls the visual timing plan protected without reconciling the epic's own pre-approval self-correction carve-out) — **defer** — stale since Story 1.2/1.3, not caused by 1.4; fix requires editing an agent-context file, which routes to defer regardless of severity.
- `metadata/visual_plan.json` shared with `prepare_remotion_stills.py`/`prepare_veo_seed_images.py`/`generate_veo_clips.py`, which index shots by `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids` — fields the new `Shot` contract deliberately doesn't produce (AD-2) — **defer** — a real integration hazard for whichever future story wires the new pipeline into those scripts, but explicitly out of this story's scope per the frozen Never section (modifying those scripts is forbidden here); none of those scripts have existing test coverage either.
- `metadata/assets.json` (timestamp diff) — **false** — same as Story 1.3's identical finding: `generated_at_utc` is deterministically regenerated by the existing ingest tool on every invocation; expected, not a bad outcome this story introduced.
- `orchestrator/contracts/visual_plan.py:62` (`QualityReview.scores` as an open `dict[str, int]`, allowing extra/unknown keys) — **false** — no demonstrated harm: all five required dimensions are independently checked regardless of what else is present, and a typo'd required key is already caught by the existing "missing required dimensions" check, not silently accepted.
- `orchestrator/contracts/visual_plan.py` (scene-count-match claimed as a `VisualPlanContract` validator, but only enforced in `validate_sources`, which needs an external `story_plan` argument) — **false** — technically precise, but `run_visual_stage` always calls `model_validate` and `validate_sources` together for every real attempt, so there's no invocation path where the count actually goes unchecked; the finding's only actionable fix is rewording the spec's own Tasks bullet, which is out of scope for triage.

## Design Notes

**Shots are scenes, not independent units.** Unlike `visual_director.py`'s own shot list (driven by real subtitle timing), `visual_agent`'s shots map 1:1 onto `FinalStoryPlanContract.scenes[]` by sequence -- the only structural unit that exists before audio/word-timing/subtitle cues are generated (Story 1.5).

**`generation_mode` is the binding decision Epic 2 reads**, separate from (but constrained by) `visual_treatment`: `visual_treatment` describes the shot's visual approach; `generation_mode` is the simple `STILL`/`VEO` switch `veo_agent`/`stills_agent` key off directly.

## Verification

**Commands:**
- `conda run -n kayak-video python -m pytest orchestrator/tests/test_visual_agent.py orchestrator/tests/test_visual_plan_contract.py -v` -- expected: all pass, no real API calls
- `conda run -n kayak-video python -m pytest orchestrator/tests -v` -- expected: full suite still passes
- One real, unmocked `python -m orchestrator.run source_images` run, once a real validated `FinalStoryPlanContract` exists (may still be blocked by the session-usage-cap noted in Story 1.3's Implementation Notes) -- confirms the pre-existing legacy `metadata/visual_plan.json` is correctly rejected and `visual_agent` runs for real.
