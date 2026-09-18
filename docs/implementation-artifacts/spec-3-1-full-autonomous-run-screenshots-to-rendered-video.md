---
title: 'Full Autonomous Run — Screenshots to Rendered Video'
type: 'feature'
created: '2026-09-18'
status: 'done'
route: 'full'
review_loop_iteration: 0
followup_review_recommended: true
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-3-context.md']
warnings: ['oversized']
deferred:
  - summary: >-
      Delivery completeness gate keys only on shot sequence, not shot_fingerprint
      freshness against the live visual plan.
    evidence: |-
      Veo/stills stages already halt on stale fingerprints; delivery trusts that
      earlier gate. A hand-edited or cross-plan production manifest could still
      sync. Settled by adding the same fingerprint cross-check inside
      run_delivery_stage if operators hit this.
    location: >-
      orchestrator/run.py:run_delivery_stage
    severity: medium
  - summary: >-
      Successful delivery always re-renders; no MP4-exists skip / idempotent
      delivery resume.
    evidence: |-
      Intent requires one screenshots→MP4 path and mid-Veo resume, not a
      delivery-stage skip. Re-render cost is Remotion-local, not Veo.
    location: >-
      orchestrator/run.py:run_delivery_stage
    severity: low
  - summary: >-
      Pre-Epic-3.1 persisted visual plans missing still_motion fail contract
      validation on resume.
    evidence: |-
      Decision B intentionally requires STILL still_motion. Reference reel was
      backfilled in Story 2.2; any other legacy plan needs a one-time backfill
      or regenerate via visual_agent.
    location: >-
      orchestrator/contracts/visual_plan.py
    severity: medium
  - summary: >-
      If sync_remotion_assets fails mid-copy after public timeline.json was
      written, remotion/public can hold a new timeline without a full asset set.
    evidence: |-
      Narration is now checked before the write; converter failures never write.
      A true mid-sync failure after timeline write remains. Settled by writing
      timeline only inside the sync tool or rolling back public/data on failure.
    location: >-
      orchestrator/run.py:run_delivery_stage
    severity: medium
baseline_commit: '023b1e2afaf07ced388cba055781f4fb0bdc27e1'
---

<intent-contract>

## Intent

**Problem:** The orchestrator already runs Epic 1 pre-production plus Epic 2 Veo/stills generation, but stops before calling the Story 2.1 timeline converter, syncing into `remotion/public/`, or triggering a Remotion render — so FR1's "screenshots in, finished video out" is still incomplete. Fresh visual plans also omit per-shot Ken-Burns/`fade_*` fields that the converter and Remotion require for STILL shots.

**Approach:** Extend `visual_agent` so new reels emit real per-shot `still_motion` (STILL only) and `fade_in_frames`/`fade_out_frames`. Then add a thin delivery stage after stills that gates on a complete `ProductionAssetsContract`, calls the existing `build_timeline` converter (no reimplementation), syncs validated assets into the existing `remotion/public/` layout, and subprocesses `npx remotion render BookReel` to produce an MP4. Mid-Veo resume stays intact.

## Boundaries & Constraints

**Always:** One `python -m orchestrator.run <source_images_dir>` invocation runs Epic 1 → Epic 2 generation → delivery. Shot count and duration come from the live visual plan and audio, never hardcoded 8 / rounded targets. `visual_agent` emits `still_motion` for every STILL shot and sensible `fade_in_frames`/`fade_out_frames` for every shot; VEO shots keep `still_motion` null. Call `orchestrator.tools.timeline_converter.build_timeline_data` (or `build_timeline.handler`) with live contracts + public-relative `shot_assets`. Sync into the documented Remotion public layout; trigger composition `BookReel`. Mid-Veo resume continues to skip shots already keyed in `ProductionAssetsContract`.

**Decision (operator chose option B):** Populate renderer motion/fade fields at visual-planning time via `visual_agent` — not deterministic delivery-stage defaults — so a second-book proof reel has intentional per-shot motion.

**Never:** Do not reimplement timeline conversion. Do not add new generation agents (extend `visual_agent` only). Do not invent delivery-stage Ken-Burns defaults as a substitute for agent-emitted motion. Do not implement Story 3.2 approval/lock. Do not treat "V1 rendered" as artifact lock. Do not run real paid Gemini/Veo/Claude calls in automated tests. Do not invent a second production-asset write path.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Complete visual plan (with STILL `still_motion` + fades) + subtitle cues + one production entry per shot | `remotion/public/` synced, `data/timeline.json` written, MP4 under `remotion/out/` matching this reel's shot count/duration | No error expected |
| Visual plan missing still motion | STILL shot without valid `still_motion` | Plan fails contract/agent validation before Veo/stills/delivery | Halt via shared failure-report path (visual stage) |
| Incomplete production assets | Missing entry for any visual-plan shot | Delivery does not run converter/sync/render | Halt via shared failure-report path |
| Mid-Veo resume | Some Veo shots already in `ProductionAssetsContract` | Re-invoke regenerates only remaining Veo shots, then continues | N/A |
| Converter rejection | Live plan fails converter invariants (timing/motion/cues) | No partial silent public overwrite past the failure point | Halt with converter `ValueError` reason |
| Remotion/node missing | `remotion/node_modules` or `npx` unavailable | No render attempted after clear diagnostic | Halt via shared failure-report path |

</intent-contract>

## Code Map

- `orchestrator/agents/visual_agent.py` -- prompt + structured output today omit `still_motion`/`fade_*` (`SkipJsonSchema` on the contract). Extend so the agent emits fades for every shot and `still_motion` for STILL shots only; keep VEO `still_motion` null.
- `orchestrator/contracts/visual_plan.py` -- `fade_in_frames`/`fade_out_frames`/`still_motion` currently default via `SkipJsonSchema` (lines ~102-111). Promote into the agent-visible schema (drop `SkipJsonSchema` for these fields, or otherwise require them on SUCCESS output). Enforce: STILL ⇒ non-null valid `StillMotion`; VEO ⇒ `still_motion is None` (VEO-forbid already exists ~173-196). Update comments that say only the reference reel is backfilled.
- `orchestrator/run.py` -- `main` ends at `run_stills_stage`; add `run_delivery_stage` after it. Reuse `_halt`, settings/manifest load, and the deterministic-stage shape of `run_voice_pipeline`. Voice-stage timing backfill (`_merge_backfilled_shot_timing`) must preserve agent-emitted motion/fades.
- `orchestrator/tools/timeline_converter.py` -- `build_timeline_data` / `@tool build_timeline` — **call only, do not fork**. Requires public-relative `shot_assets`; still rejects STILL + missing motion (lines 99-109) as a last-line guard.
- `orchestrator/state/production_assets.py` -- `load_production_assets` / `upsert_production_asset` (writer=`"orchestrator"` only). Completeness gate: every `visual_plan.shots[].sequence` present.
- `orchestrator/tools/gemini_tools.py` -- `NARRATION_WAV` (`audio/narration.wav`); stills under `generated/stills/shot_{NN}.png`.
- `orchestrator/tools/veo_tools.py` -- Veo clips under `generated/veo/shot_{NN}.mp4`.
- `orchestrator/tests/test_timeline_converter.py` -- `REFERENCE_SHOT_ASSETS` is the target public-relative `src` shape (`stills/…`, `video/…`).
- `orchestrator/tools/deterministic_tools.py` -- home for new `@tool`s `sync_remotion_assets` + `render_remotion`; mirror ingest/subtitle tool patterns.
- `remotion/src/Composition.tsx` / `BookReel.tsx` -- consume `data/timeline.json`, `audio/narration.wav`, `data/subtitle_cues.json`; composition id `BookReel`; `1080x1920` / 30fps.
- `docs/book_reels_architecture.md` / manual map §28 — public layout: `audio/`, `stills/`, `video/`, `data/{timeline,subtitle_cues,visual_plan}.json`; manual render `npx remotion render BookReel out/book_reel_v1.mp4` from `remotion/`.
- `orchestrator/tests/test_full_chain_resume.py` / `test_visual_agent.py` / `test_visual_plan_contract.py` -- mid-Veo resume already covered; extend for motion/fade contract rules + delivery wiring with mocked sync/render.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/contracts/visual_plan.py` -- require agent-emitted `fade_in_frames`/`fade_out_frames` on every shot and non-null `StillMotion` on every STILL shot; keep VEO `still_motion` null; update deferred-backfill comments
- [x] `orchestrator/agents/visual_agent.py` -- prompt + output guidance so the agent invents intentional Ken-Burns per STILL shot and fades per shot (no hardcoded shot-number ladder)
- [x] `orchestrator/tests/test_visual_plan_contract.py`, `test_visual_agent.py` -- cover STILL-requires-motion / VEO-forbids-motion and agent schema visibility
- [x] `orchestrator/tools/deterministic_tools.py` -- add `@tool sync_remotion_assets` copying narration, stills, videos, subtitle cues (+ optional visual_plan) into `remotion/public/` using the documented layout; map production `local_path` → public-relative names
- [x] `orchestrator/tools/deterministic_tools.py` (or sibling) -- add `@tool render_remotion` subprocessing `npx remotion render BookReel <out>` from `remotion/`; clear errors when node/npm/deps missing
- [x] `orchestrator/run.py` -- add `run_delivery_stage`: completeness gate → build public `shot_assets` → `build_timeline_data` → write `remotion/public/data/timeline.json` → sync → render; wire after `run_stills_stage` in `main`; ensure voice timing merge preserves motion/fades
- [x] `orchestrator/preflight.py` (minimal) -- fail loud if Remotion project / node toolchain required for delivery is absent when a full run will reach delivery
- [x] `orchestrator/tests/test_delivery_stage.py` (new) + extend `test_full_chain_resume.py` -- cover delivery matrix rows with mocked remotion/sync; assert mid-Veo resume still makes zero paid calls for approved shots; no network in CI

**Acceptance Criteria:**
- Given 6–10 screenshots from a second, different book and no prior state for that reel, when the orchestrator is invoked once, then Epic 1 + Epic 2 generation + delivery run with no manual per-stage steps
- Given that book's live visual plan and audio, when delivery runs, then shot count and duration match the live plan/audio — not the Backwards Law reel's 8-shot / rounded targets
- Given a newly generated visual plan with STILL shots, when it validates, then each STILL shot carries real `still_motion` and every shot carries fades — not SkipJsonSchema zeros/nulls left for later backfill
- Given validated `VisualPlanContract`, `SubtitleCuesContract`, and a complete `ProductionAssetsContract`, when delivery runs, then it calls Story 2.1's converter and does not reimplement conversion
- Given a real `timeline.json` and final audio/subtitle files, when delivery proceeds, then assets sync into `remotion/public/` and Remotion produces an MP4 matching this book's plan
- Given a halt mid-Veo after some shots are recorded in `ProductionAssetsContract`, when re-invoked, then only remaining Veo shots regenerate
- Given success, when the MP4 exists, then the operator supplied only screenshots (FR1)

## Implementation Notes

- **Decision B enforced in contract:** `Shot.fade_in_frames` / `fade_out_frames` / `still_motion` are agent-visible (no `SkipJsonSchema`); `VisualPlanContract.still_motion_forbidden_for_veo_shots` now requires non-null `StillMotion` on every STILL shot at parse time.
- **Voice timing backfill:** After a successful `voice_agent` run **and** on the validated-cues skip path, `_backfill_visual_plan_shot_timing_from_cues()` maps 1:1 scenes→shots onto subtitle cues by word count and persists `metadata/visual_plan.json` when timing is still at the 0.0/0.0 placeholder. `_merge_backfilled_shot_timing` also preserves prior `fade_*` / `still_motion` when `visual_agent` regenerates a plan.
- **Delivery:** `run_delivery_stage` gates production completeness, checks narration, calls `build_timeline_data`, writes `remotion/public/data/timeline.json`, then `sync_remotion_assets` + `render_remotion` (default `remotion/out/book_reel.mp4`, absolute path to Remotion). Remotion toolchain check lives in `check_remotion_delivery_toolchain()` (invoked at delivery entry, not global preflight).
- **Surprise:** `run_voice_stage` previously always called bounded-stage logic even when subtitle cues were already valid; early skip was added, then review fixed skip to still run timing backfill when placeholders remain.
- **Manual proof still required:** A real second-book run needs `npm install` in `remotion/`, Node/`npx`, and paid API calls — not exercised in CI (sync/render mocked in `test_delivery_stage.py`).
- **Review fixes (2026-09-18):** Absolute `render_remotion` output path + subprocess timeout; optional `visual_plan_path` omitted from sync tool schema; voice skip path runs shot-timing backfill; delivery checks narration before writing public timeline; render JSON parse failures halt cleanly; tests for sync/render handlers, Remotion preflight, voice backfill, and merge preserving fade/motion; fully-valid chain fixture seeds backfilled timing so resume makes zero file rewrites.

## Spec Change Log

## Review Triage Log

### 2026-09-18 — Review pass
- verdicts: 29 findings — high 1, medium 14, low 5, false 7, maybe-false 2
- findings:
  - `[high]` `[patch]` `render_remotion` cwd/path mismatch writing under `remotion/remotion/out/` — fixed: resolve absolute output path before subprocess/existence check
  - `[medium]` `[patch]` No real tests for `check_remotion_delivery_toolchain` — fixed: cases in `test_preflight.py`
  - `[false]` `[reject]` Remotion check must live in global `run_preflight` — matrix only requires halt before render; delivery-entry check satisfies intent
  - `[false]` `[reject]` Matrix “missing still_motion” lacks visual-stage halt test — contract validator is the visual-stage gate; `test_still_shot_without_still_motion_rejected` covers it
  - `[medium]` `[patch]` Voice skip skipped timing backfill — fixed: shared `_voice_shot_timing_backfill_or_halt` on skip + success
  - `[medium]` `[patch]` No tests for backfill / merge preserving fade/motion — fixed: voice + visual agent tests
  - `[medium]` `[defer]` Delivery lacks shot_fingerprint freshness gate — see frontmatter `deferred`
  - `[medium]` `[patch]` Public timeline written before narration check — fixed: narration check moved earlier
  - `[low]` `[defer]` No idempotent delivery / MP4-exists skip — see frontmatter `deferred`
  - `[low]` `[reject]` Spec status vs Auto Run Result drift — process artifact; not a product defect worth a code fix
  - `[false]` `[reject]` Default `book_reel.mp4` diverges from doc `book_reel_v1.mp4` — intentional Design Notes path
  - `[low]` `[patch]` Stale `_merge_backfilled_shot_timing` docstring — fixed
  - `[low]` `[patch]` `visual_plan_path` required in sync schema but optional in handler — fixed: removed from required schema
  - `[low]` `[reject]` Incomplete-assets test deletes whole manifest — still covers halt; one-shot omission is nice-to-have only
  - `[low]` `[reject]` `run_delivery_stage` unused settings/manifest — cosmetic; deterministic delivery has no budget calls
  - `[medium]` `[patch]` Voice skip before backfill (edge) — fixed (same as above)
  - `[medium]` `[patch]` Timeline written before narration (edge) — fixed (same as above)
  - `[medium]` `[defer]` Sync failure after timeline write leaves partial public tree — mitigated for missing narration; residual deferred
  - `[medium]` `[patch]` Render success JSON shape KeyError/IndexError — fixed: halt on parse/structure errors
  - `[false]` `[reject]` Zero `word_count` infinite backfill loop — `cue_cursor` still advances; loop terminates
  - `[medium]` `[patch]` Remotion subprocess can hang — fixed: 3600s timeout → RuntimeError
  - `[medium]` `[defer]` Legacy plans without still_motion fail resume — intentional Decision B; deferred migration note
  - `[false]` `[reject]` Spec task requires Remotion in global preflight (claim) — delivery-entry check matches Implementation Notes + matrix
  - `[medium]` `[patch]` `sync_remotion_assets` / `render_remotion` never run in tests — fixed: `test_deterministic_tools.py` handler tests
  - `[medium]` `[patch]` Post-voice timing backfill unverified — fixed: voice fresh-run + skip-path assertions
  - `[medium]` `[patch]` Merge fade/still_motion not pinned — fixed: `gemini_legacy` visual regen assertions
  - `[medium]` `[patch]` `check_remotion_delivery_toolchain` never called for real — fixed: `test_preflight.py`
  - `[maybe-false]` `[defer]` Timeline `asset_type` vs sync suffix path mapping could diverge — would need a malformed production entry; settle by passing asset_type into sync sources if seen
  - `[medium]` `[patch]` Fully-valid resume fixture lacked timing so skip rewrote visual_plan — fixed: seed backfilled timing in chain test

## Design Notes

**Decision B (resolved):** `visual_agent` owns `still_motion` + fade frames for new reels. Delivery must not paper over missing motion with canned defaults. Reference-reel backfilled plans remain valid; new agent output must satisfy the stricter contract.

Delivery stays thin for Remotion handoff: no new agents for sync/render. Public layout follows on-disk Remotion conventions (`stills/shot_NN.png`, `video/shot_NN.mp4`, `audio/narration.wav`, `data/timeline.json`, `data/subtitle_cues.json`). Automated tests mock sync/render subprocesses; a live second-book proof is a manual operator run once screenshots are available (same "no paid calls in pytest" rule as Stories 2.3/2.4).

Default render output path: `remotion/out/book_reel.mp4`.

## Verification

**Commands:**
- `conda run -n kayak-video python -m pytest orchestrator/tests` -- all pass offline
- `conda run -n kayak-video python -c "import orchestrator.run"` -- delivery stage importable without firing paid tools
- `git diff --check` -- no whitespace errors

**Manual checks (if no CLI):**
- With a clean second-book `source_images/` and no prior metadata for that reel, one orchestrator invocation yields an MP4 whose shot count equals that run's `visual_plan.json`

## Auto Run Result

Status: done

Summary: Story 3.1 wires Decision B (`visual_agent` emits fades + STILL Ken-Burns) and a delivery stage that converts the live plan, syncs `remotion/public/`, and renders `remotion/out/book_reel.mp4` after production assets are complete. Mid-Veo resume preserved.

Files changed:
- `orchestrator/contracts/visual_plan.py` — agent-visible fades/motion; STILL requires StillMotion
- `orchestrator/agents/visual_agent.py` — prompt for intentional fades/Ken-Burns
- `orchestrator/run.py` — voice timing backfill (incl. skip), delivery stage, merge preserves motion/fades
- `orchestrator/tools/deterministic_tools.py` — sync_remotion_assets + render_remotion (absolute path, timeout)
- `orchestrator/preflight.py` — check_remotion_delivery_toolchain
- `orchestrator/tests/*` — delivery, sync/render, preflight, voice backfill, visual merge, fixture updates
- `docs/implementation-artifacts/spec-3-1-full-autonomous-run-screenshots-to-rendered-video.md` — this spec

Review findings breakdown:
- Patches applied: 1 high (render path), 12 medium (backfill/skip, narration order, timeouts, parse halt, verification gaps), 2 low (docstring, schema)
- Deferred: 4 items (fingerprint at delivery, idempotent render, legacy still_motion migration, residual mid-sync partial write)
- Rejected/false: global preflight requirement, visual-stage test gap (contract covers), book_reel_v1 naming, word_count infinite loop, unused settings, incomplete-assets test shape, spec status drift

Follow-up review recommendation: true — patched 1 high + multiple mediums on first pass. Unverified residual risk: live second-book E2E still depends on Remotion `npm install` + paid APIs; delivery fingerprint freshness and mid-sync rollback remain deferred.

Verification performed:
- `conda run -n kayak-video python -m pytest orchestrator/tests` → 300 passed
- `conda run -n kayak-video python -c "import orchestrator.run"` → OK
- `git diff --check` → clean

Residual risks: no live second-book MP4 in CI; deferred delivery fingerprint / mid-sync rollback; Story 3.2 approval lock still out of scope.
