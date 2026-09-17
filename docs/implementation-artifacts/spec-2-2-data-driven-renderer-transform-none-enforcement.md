---
title: 'Data-Driven Renderer Core + Structural transform:none Enforcement'
type: 'feature'
created: '2026-09-17'
status: 'done'
route: 'full'
review_loop_iteration: 0
baseline_commit: '3a1ec5ba81eb95cf6be1853e22cbca7ae2e388ea'
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-2-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `remotion/src/timeline.ts`/`BookReel.tsx` hand-authors the "Backwards Law" reel's shot list, transitions, and Ken-Burns motion as hardcoded arrays/tables; a new reel would require a coding agent to hand-write these files again. Story 2.1 built `timeline.json` and a regression harness but did not touch the renderer.

**Approach:** Rewrite `timeline.ts`/`BookReel.tsx`/`Composition.tsx` to load `timeline.json` + `subtitle_cues.json` at render time (the `fetch`+`delayRender` pattern `Subtitles.tsx` already uses, plus Remotion's `calculateMetadata` for dynamic duration/fps/dimensions) instead of hardcoded constants. Extend `VisualPlanContract.Shot` and `SubtitleCuesContract.Cue` additively with the per-shot transition/motion and per-cue `style_hint` fields the renderer needs, backfilling the reference reel's real, already-known values (from `BookReel.tsx`'s current tables and `metadata/subtitle_cues.json`, which already carries real `style_hint` values). Extend `timeline_converter.py` to pass these through into `timeline.json`. Preserve the existing structural boundary that already enforces "no transform on video" (`ShotVideo`'s props type carries no transform field) rather than re-deriving it.

## Boundaries & Constraints

**Always:** The rewritten renderer must consume `timeline.json` + `subtitle_cues.json` with zero hand-authored code changes per new reel (only new JSON + new asset files). `transform` must remain structurally unavailable on the video-shot component (no transform prop in its type) -- Ken-Burns/transform stays still-only. Shot transitions stay opacity-only for video, matching today. Running Story 2.1's harness with the reconstructed Backwards Law `timeline.json` through the new renderer must pass (not merely fail-differently).

**Decision (following Story 2.1's established pattern for this exact kind of gap):** `timeline.json` today (Story 2.1) has no per-shot transition (fade in/out frames) or Ken-Burns motion (scale/translate/easing) data, and `SubtitleCuesContract.Cue` has no `style_hint` -- both exist today only as hardcoded tables in `BookReel.tsx`/`Subtitles.tsx`. Rather than inventing a generic motion/transition algorithm (which risks not reproducing the reference reel closely enough to pass Story 2.1's pHash-based harness), this story extends `VisualPlanContract.Shot` additively (`fade_in_frames`, `fade_out_frames`, `still_motion` -- null for VEO shots) and `SubtitleCuesContract.Cue` additively (`style_hint`), backfills the reference reel's persisted contracts with the exact real values already sitting in `BookReel.tsx`'s tables and `metadata/subtitle_cues.json`, and has the converter/renderer read them from there. Future reels populating these fields for real is out of scope (same deferral shape as Story 2.1's shot-timing gap).

**Never:** This story does not implement Veo/stills generation (Story 2.3/2.4). It does not change `AnimatedStill`/`ShotTransition`/`ShotVideo`/`ImpactTypography`'s own internal behavior -- only where their input data comes from. It does not add a generic/algorithmic motion-generation system for reels that don't have backfilled data.

**Token budget:** spec kept at full length (accepted over the 1600-token guideline) -- the renderer rewrite, its structural enforcement, and the contract extensions it depends on are one cohesive, sequenced deliverable per `epics.md`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Reconstructed Backwards Law `timeline.json` + `subtitle_cues.json` (with backfilled transition/motion/style_hint) | Rendered video passes Story 2.1's regression harness (exact duration/fps/frame count, pHash match at all 24 cue centres) | No error expected |
| Video shot receives no transform | Any video-type shot in `timeline.json` | Rendered with opacity-only transition, never scale/translate/pan | Structurally impossible via the type system, not merely untested |
| Missing/malformed `timeline.json` at render time | `timeline.json` absent or fails to parse | Render fails loudly (Remotion's own error surface) rather than silently rendering a blank/default video | Render process exits non-zero with a clear error |

</frozen-after-approval>

## Code Map

- `remotion/src/timeline.ts:1-93` -- current hardcoded `shotDefinitions`/`shots`/`FPS`/`VIDEO_WIDTH`/`VIDEO_HEIGHT`/`DURATION_IN_FRAMES`. To be replaced by data loaded from `timeline.json` at render/metadata-calculation time. `toFrame()`'s `Math.round(seconds * FPS)` rounding convention should carry over for consistency with the existing reference reel's frame boundaries.
- `remotion/src/BookReel.tsx:1-112` -- `SHOT_TRANSITIONS` (fadeInFrames/fadeOutFrames per shot id, lines 15-24), `STILL_MOTION` (Ken-Burns params per shot id, lines 35-72), final-shot end-darken constants (lines 29-31, keep as-is per Never -- this is a one-off closing effect, not a per-shot data field pattern; if it must move, it belongs in the backfilled `Shot` data too, decide during implementation). These are the exact values to backfill into the new `Shot` fields.
- `remotion/src/Composition.tsx:1-16` -- currently reads static `DURATION_IN_FRAMES`/`FPS`/etc. at module-eval time via a plain import. A data-driven duration needs Remotion's `calculateMetadata` composition prop (async, computes duration/fps/dimensions from loaded JSON before render) -- look this up via the `remotion-docs` skill or `find-docs`/ctx7 at implementation time for the exact current API shape; do not guess the signature.
- `remotion/src/components/Subtitles.tsx:129-148` -- the exact `fetch(staticFile(...))` + `useDelayRender` pattern already proven in this codebase; reuse this shape for loading `timeline.json` too rather than inventing a new data-loading convention.
- `remotion/src/components/ShotVideo.tsx:20-32` -- **the structural transform:none boundary already exists correctly**: `ShotVideoProps` has no transform field, and the hardcoded `transform: "none"` inline style has no prop path to override it. Preserve this exact shape; do not add a transform/motion prop to this component.
- `remotion/src/components/AnimatedStill.tsx`, `ShotTransition.tsx` -- Ken-Burns and opacity-transition components; their `motion`/`fadeInFrames`/`fadeOutFrames` props already accept the right shape, just need to be sourced from JSON instead of the hardcoded tables.
- `orchestrator/contracts/visual_plan.py:52-84` -- `Shot` model; Story 2.1 already added `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids` as `SkipJsonSchema` additive fields with the exact same rationale/pattern this story's new fields should follow.
- `orchestrator/contracts/subtitle_cues.py:37-52` -- `Cue` model; add `style_hint` the same way. Real values already exist in `metadata/subtitle_cues.json` (verified: all 24 cues already have `style_hint`, and this file is byte-identical to `remotion/public/data/subtitle_cues.json`) -- likely zero backfill work needed here, just contract validation to pick up what's already there.
- `orchestrator/tools/timeline_converter.py:39-90` -- `build_timeline_data`; extend the per-shot output dict to include the new fields, following the same "traceable 1:1 to a contract field" rule Story 2.1 established.
- `orchestrator/tests/test_timeline_converter.py` -- extend to cover the new fields; `orchestrator/tests/fixtures/reference_reel_metrics.json` and the regression harness itself (`orchestrator/tools/regression_harness.py`, `orchestrator/tests/test_regression_harness.py`) are Story 2.1's, reused unchanged as this story's own acceptance gate.
- `metadata/visual_plan.json`, `metadata/subtitle_cues.json` -- backfill targets (same files Story 2.1 backfilled).

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/contracts/visual_plan.py` -- add `fade_in_frames: SkipJsonSchema[int] = 0`, `fade_out_frames: SkipJsonSchema[int] = 0`, `still_motion: SkipJsonSchema[dict | None] = None` to `Shot` (additive)
- [x] `orchestrator/contracts/subtitle_cues.py` -- add `style_hint: SkipJsonSchema[Literal["NORMAL", "IMPACT", "EMPHASIS", "REFLECTION"]] = "NORMAL"` to `Cue` (additive)
- [x] `metadata/visual_plan.json` -- backfill each shot's `fade_in_frames`/`fade_out_frames`/`still_motion` from `BookReel.tsx`'s `SHOT_TRANSITIONS`/`STILL_MOTION` tables (null `still_motion` for VEO shots 2/5/6)
- [x] `orchestrator/tools/timeline_converter.py` -- pass the new fields through into `timeline.json`'s per-shot output
- [x] `orchestrator/tests/test_timeline_converter.py` -- cover the new fields (present for stills, null/absent handling for video shots)
- [x] `remotion/src/timeline.ts` -- replace hardcoded shot/constant data with types matching `timeline.json`'s real shape (converter output, not invented independently)
- [x] `remotion/src/Composition.tsx` -- compute duration/fps/dimensions from loaded data via `calculateMetadata` instead of static imports
- [x] `remotion/src/BookReel.tsx` -- load `timeline.json` (`fetch`+`delayRender`, matching `Subtitles.tsx`'s pattern) and render each shot using its own `fade_in_frames`/`fade_out_frames`/`still_motion` instead of the hardcoded `SHOT_TRANSITIONS`/`STILL_MOTION` tables; preserve `ShotVideo`'s no-transform boundary unchanged
- [x] `remotion/src/components/Subtitles.tsx` -- read `style_hint` from `subtitle_cues.json` as today (already works once the Python contract carries it through) -- confirm no change needed, or make the minimal change if the file this reads diverges from `metadata/subtitle_cues.json`

**Acceptance Criteria:**
- Given the reconstructed Backwards Law `timeline.json` (with backfilled transition/motion) and `subtitle_cues.json` (with `style_hint`), when the new renderer renders it, then Story 2.1's regression harness passes (duration/fps/resolution/frame count exact match, pHash ≤ 5/64 at all 24 cue centres)
- Given any video-type shot, when the renderer builds it, then no code path can apply a `transform` to it (structural, not conventional)
- Given a still shot, when the renderer builds it, then its Ken-Burns motion comes from `timeline.json`'s `still_motion`, not a hardcoded table
- Given `timeline.json`/`subtitle_cues.json` are absent or malformed, when Remotion renders, then it fails loudly rather than producing a blank/default video

## Implementation Notes

- `metadata/visual_plan.json`'s backfill and `remotion/public/data/timeline.json` (new file) use snake_case `still_motion` keys (`scale_from`, `scale_to`, `translate_x_from`/`_to`, `translate_y_from`/`_to`, `easing`) -- traced 1:1 to the Python contract/converter's own naming, mapped to `AnimatedStill`'s camelCase `StillMotion` prop shape by a small `toStillMotion()` helper in `BookReel.tsx` (the only place the two naming conventions meet).
- Decision on the Code Map's flagged final-shot end-darken question: kept as a renderer-level constant (`FINAL_SHOT_END_DARKEN_FRAMES`/`_OPACITY` in `BookReel.tsx`), per the Design Notes' "leave as-is" option -- but generalized from a hardcoded shot id 8 to "whichever shot has the highest `sequence` in the loaded `timeline.json`" (`Math.max(...timeline.shots.map(s => s.sequence))`), so a future reel's own last shot gets the same closing treatment with no renderer code change, satisfying the "zero hand-authored code changes per new reel" boundary without adding a per-shot data field for a one-off effect.
- `remotion/src/Composition.tsx`'s `calculateMetadata` computes only `durationInFrames` from `timeline.json` (the last shot's `end_seconds`); `fps`/`width`/`height` stay fixed renderer-level constants in `timeline.ts` (`orchestrator/tools/timeline_converter.py`'s own docstring already frames these as "Remotion composition-level constants owned by Story 2.2's renderer, not shot data") -- this pipeline's reels are all fixed 1080x1920@30fps (Epic 2 Context), so only duration genuinely varies per reel.
- No orchestrator entrypoint yet writes `remotion/public/data/timeline.json` for a fresh run (confirmed: no caller of `build_timeline`/`build_timeline_data` exists outside tests) -- wiring that is Epic 3's job per `epic-2-context.md` ("Epic 3 integrates end-to-end; Epic 3 does not build new generation or conversion logic"). For this story's own acceptance/verification, `remotion/public/data/timeline.json` was generated once via the same converter + envelope-filling logic `test_timeline_converter.py` already uses (real backfilled `metadata/visual_plan.json` + `metadata/subtitle_cues.json` + the reference reel's known asset paths), matching `timeline.ts`'s previous hardcoded shape exactly.
- `orchestrator/tests/test_visual_agent.py`'s `stamped()` helper needed a matching update (add `fade_in_frames: 0`, `fade_out_frames: 0`, `still_motion: None` to its expected-persisted-shot dict) -- a freshly-generated `visual_agent` shot now round-trips these three additional `SkipJsonSchema`-defaulted fields too, the same way it already did for Story 2.1's `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids`.
- Post-review hardening (external review pass): added a `StillMotion` pydantic sub-model (required `scale_from`/`scale_to`, optional translate/`easing`) so a malformed `still_motion` shape fails Python validation, not just a render-time crash. Its companion presence invariant ("`still_motion` iff `generation_mode == STILL`") is split across two enforcement points rather than one hard `Shot`-level validator, deliberately: the VEO-forbids-`still_motion` half is enforced immediately at `VisualPlanContract` parse time (`still_motion_forbidden_for_veo_shots`, mirroring `generation_mode_matches_visual_treatment`'s pattern) because `generation_mode` is always set from `visual_agent`'s first output; the STILL-requires-`still_motion` half is enforced later, in `timeline_converter.build_timeline_data`, because `still_motion` is deferred backfill data exactly like Story 2.1's `start_seconds`/`end_seconds` -- a freshly generated STILL shot legitimately has no `still_motion` yet (`visual_agent` never sees or sets it, `SkipJsonSchema`), and enforcing full presence at parse time would reject every pre-backfill `VisualPlanContract` in the repo, breaking `test_visual_plan_contract.py`, `test_visual_agent.py`, `test_full_chain_resume.py`, and `test_voice_agent.py`'s existing fixtures (all use `generation_mode: STILL` shots without `still_motion`, matching the established staged-backfill convention). Verified this is the actual failure mode by trying the hard parse-time version first and observing the breakage before choosing this split.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind-hunter, edge-case-hunter, verification-gap; Codex not attempted this round given its confirmed sandbox failure on Story 2.1). Verdict counts: high 0, medium 2, low 6, false 3, maybe-false 0.

- **[patch, medium-high]** `orchestrator/tests/test_regression_harness.py` -- verification-gap, pre-verified. The story's own acceptance criterion ("the new renderer's output passes Story 2.1's harness") was verified manually (by hand, and independently by the reviewing session) but is never wired into an automated test -- every existing harness test points only at the pre-existing `book_reel_v1.mp4` (the *old* renderer's output). A future regression in `BookReel.tsx`/`timeline.ts`/`Composition.tsx` would go undetected by `pytest`. Fix: add a `skipif`-guarded test (matching `requires_reference_video`'s pattern) that points the harness at a new-renderer render output path.
- **[patch, medium]** `remotion/src/BookReel.tsx` (`requireStillMotion`), `orchestrator/contracts/visual_plan.py` (`Shot.still_motion`), `orchestrator/tools/timeline_converter.py` -- blind-hunter + edge-case-hunter, grouped (same defect: `still_motion` has no structural validation anywhere in the pipeline). The Python contract types it as a bare `dict | None` (unlike this same diff's `style_hint: Literal[...]`), the converter passes it through without checking a STILL shot actually has one, and `BookReel.tsx`'s `requireStillMotion` only checks truthiness, not shape -- a malformed or empty `still_motion` object would pass Python validation silently and only surface as `NaN` transforms or a render-time crash. Fix: add a small `StillMotion` sub-model (required `scale_from`/`scale_to: float`, optional translate/easing fields) and a `model_validator` on `Shot` requiring `still_motion` iff `generation_mode == "STILL"` (mirroring the existing `generation_mode_matches_visual_treatment` validator pattern already in this file).
- **[patch, low]** `remotion/src/BookReel.tsx:84` (edge-case-hunter) -- `shot.type === "still" ? ... : ...` silently treats any non-`"still"` value as video (TypeScript's compile-time union type doesn't validate JSON fetched at runtime). Fix: an explicit type check that throws on an unrecognized `shot.type`.
- **[patch, low]** `remotion/src/Composition.tsx`, `remotion/src/BookReel.tsx` (blind-hunter) -- `calculateMetadata` and the component each independently `fetch`+parse `timeline.json`, a real double fetch/parse Remotion's `calculateMetadata` `props` return exists specifically to avoid. Fix: return the parsed timeline as `props` from `calculateMetadata` and consume it in the component instead of fetching twice.
- **[patch, low]** `orchestrator/tests/test_timeline_converter.py::test_visual_plan_without_backfilled_fields_still_parses_with_defaults` (blind-hunter) -- docstring claims to guard "a payload that predates this story," but only deletes Story 2.1's fields, not this story's `fade_in_frames`/`fade_out_frames`/`still_motion`. Fix: delete the three new fields too.
- **[patch, low]** `orchestrator/tests/test_subtitle_cues_contract.py`, `test_visual_plan_contract.py` (blind-hunter) -- the spec's own Verification section lists both, but neither has a dedicated unit test for the new `style_hint` Literal validation or the new `Shot` fields' defaults in isolation from the timeline-converter integration tests. Fix: add small direct unit tests.
- **[patch, low]** `remotion/src/components/ShotVideo.tsx` (blind-hunter) -- stale comments reference fixed "Shots 2, 5, 6," now inaccurate since shot layout is data-driven. Fix: generalize the wording, keep the `OffthreadVideo`-vs-`Video` rationale.
- **[defer, low]** `remotion/src/BookReel.tsx` (edge-case-hunter) -- a shot whose `toFrame(end_seconds) - toFrame(start_seconds)` rounds to 0 would hit an unrelated Remotion internal error instead of a clear message. Not reachable by any current data (the converter already rejects `end_seconds <= start_seconds`); only a hypothetical future reel with a sub-frame-length shot.
- **[defer, low]** `remotion/src/components/Subtitles.tsx`, `BookReel.tsx` (blind-hunter) -- neither `fetch` checks `response.ok` before `.json()`; a 404 produces an opaque `SyntaxError` rather than a clear message. `Subtitles.tsx`'s identical pre-existing gap predates this story; fixing only the new call site would be inconsistent.

Rejected/false (summarized): fps/dimensions-not-computed-from-JSON claim (reject -- the actual behavior is correct and already justified in Implementation Notes; the "fix" would only be editing this spec's own imprecise task wording, which the triage rules exclude); duplicate-max-sequence claim (false -- `VisualPlanContract.shot_sequence_is_continuous` already guarantees unique sequential sequences, unreachable through the real production path); two-different-last-shot-computations claim (false -- the converter's own shot-contiguity check guarantees "highest sequence" and "highest end_seconds" are always the same shot for any `timeline.json` this converter can produce); empty-Spec-Change-Log claim (false -- that section is explicitly scoped to `bad_spec` review loopbacks per the template, not implementation-time decisions; the final-shot decision was correctly recorded in Implementation Notes instead).

## Design Notes

The reference reel's exact current values to backfill (`BookReel.tsx`): `SHOT_TRANSITIONS` -- shot 1: {0,6}, 2: {6,6}, 3: {6,4}, 4: {4,0}, 5: {0,4}, 6: {4,6}, 7: {6,8}, 8: {8,0} (fadeInFrames, fadeOutFrames). `STILL_MOTION` -- shot 1: scale 1.0→1.07, linear; shot 3: scale 1.02→1.07, translateY 0→-2, ease; shot 4: scale 1.0→1.06, translateX 0→1.5, ease; shot 7: scale 1.02→1.04, translateX 0→1.2, ease; shot 8: scale 1.0→1.015, easeOut. Shots 2/5/6 are VEO (no still_motion). The final-shot end-darken effect (shot 8, last 10 frames, opacity 0.72) is currently a separate hardcoded constant, not per-shot data -- Code Map flags this as a decision point for implementation (fold into backfilled data vs. leave as a renderer-level constant since only one reel-ending convention exists today).

Residual risk: pHash tolerance (Hamming ≤ 5/64) was calibrated only against the current renderer's own re-encodes (Story 2.1), not against a renderer swap. If backfilled values reproduce the reference exactly, pixel output should be effectively unchanged, but this is unverified until actually rendered -- if the harness fails narrowly, recalibrating the threshold is Story 2.1's own "flagged for calibration" note, not a sign this story's approach is wrong.

## Verification

**Commands:**
- `python -m pytest orchestrator/tests/test_timeline_converter.py orchestrator/tests/test_visual_plan_contract.py orchestrator/tests/test_subtitle_cues_contract.py` -- expected: all pass
- `npx remotion render BookReel out/book_reel_v2.mp4` (from `remotion/`) -- expected: renders without error
- `python -m pytest orchestrator/tests/test_regression_harness.py` (with `remotion/out/book_reel_v1.mp4` present and the new render's output pointed at the harness) -- expected: pass, where Story 2.1 recorded fail/inconclusive against the old renderer

**Manual checks (if no CLI):**
- Visually compare the new render against the existing reference render side by side for a few shots (especially shot 8's end-darken) to confirm the backfilled motion looks the same
