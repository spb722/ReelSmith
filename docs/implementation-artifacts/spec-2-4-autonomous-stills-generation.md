---
title: 'Autonomous Stills Generation'
type: 'feature'
created: '2026-09-18'
status: 'done'
route: 'full'
review_loop_iteration: 0
baseline_commit: '37201ec8bae67d4c12f7f9593f9b0ddf97313143'
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-2-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `prepare_remotion_stills.py` is a manual script with a hardcoded per-shot-number prompt ladder (`if shot_sequence == N`), no visual QA, and no write path into the new `ProductionAssetsContract` -- every STILL-mode shot still requires manual invocation and manual review.

**Approach:** Add a `stills_agent` mirroring Story 2.3's `veo_agent` shape (one scoped `@tool`, structured outcome, visual QA with bounded retry) but single-stage: one Gemini image-edit call per shot (no seed/operation-dump concept -- confirmed unnecessary by `ProductionAssetEntry`'s existing STILL branch and the legacy script's own one-call flow), built from `Shot` fields dynamically rather than a shot-number ladder. `stills_agent` writes only through Story 2.3's existing `upsert_production_asset` -- no new write path.

## Boundaries & Constraints

**Always:** Select STILL-mode shots from the validated visual plan; never change a shot's mode. Build the generation prompt from `Shot` fields (`shot_goal`, `frame_composition`, `visual_treatment`, etc.), never a hardcoded shot-number branch (the exact anti-pattern `gemini_tools.py`'s existing docstring already warns against). A still fails QA (duplicate subject, UI/text artifacts, wrong scene) and retries up to the configured ceiling before halting, reusing Epic 1's kernel retry/halt machinery. Resume without regenerating a shot that already has a validated `ProductionAssetsContract` entry.

**Decision (following Story 2.3's established path-safety precedent):** `ProductionAssetEntry.asset_is_materialized`'s STILL branch currently has no directory constraint (unlike VEO's enforced `generated/veo/`/`generated/veo_seeds/`/`generated/veo/previews/` prefixes). This story adds the analogous constraint for stills (approved still assets must live under `generated/stills/`), matching VEO's existing safety pattern rather than leaving stills as the one asset type with no structural path enforcement.

**Never:** Do not implement a second write path into `ProductionAssetsContract` -- only `upsert_production_asset` writes it. Do not generate VEO-mode shots. Do not invoke `prepare_remotion_stills.py` as a subprocess. Do not run a real paid Gemini call during automated tests.

**Token budget:** spec kept at full length (accepted over the 1600-token guideline) -- one cohesive story per `epics.md`, matching Story 2.3's own scale for the sibling VEO stage.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | A validated STILL-mode shot with its cited analyzed asset | Approved still recorded via keyed upsert into `ProductionAssetsContract`, with `generation_mode="STILL"` | No error expected |
| QA rejection | Generated still has a duplicate-subject or UI/text defect | Retry with a specific correction, up to the configured ceiling | Ceiling exhausted halts via the shared failure-report path |
| Resume | A STILL shot already has a validated production entry | No Gemini/agent call for that shot | N/A |
| Epic completion | All shots (VEO + STILL) have validated entries | `ProductionAssetsContract` has exactly one entry per visual-plan shot | N/A (Epic 2's own completion criterion, verified not enforced as a runtime gate by this story alone) |

</frozen-after-approval>

## Code Map

- `prepare_remotion_stills.py:43,115-188,195-347,354-378` -- legacy script: `TARGET_SHOTS`/hardcoded `if shot_sequence == N` prompt ladder (the anti-pattern to avoid), `build_manifest()` (reads legacy `start_seconds`/`primary_subtitle_cue_ids` fields -- do not port this indexing, use `VisualPlanContract`'s real fields directly per Story 2.1/2.2), `generate_one()` (one `client.models.generate_content(..., response_modalities=[TEXT, IMAGE])` call, model `gemini-3.1-flash-image`), CLI flags `--plan-only`/`--shot`/`--all` (no approval flag -- single-stage, generate-and-done).
- `orchestrator/contracts/veo.py:52-213` -- `VeoSeedContract`/`VeoResultContract`/`VeoFailureContract`/`VeoOutcomeContract` shapes to mirror structurally for `StillResultContract`/`StillFailureContract`/`StillOutcomeContract` (new file, e.g. `orchestrator/contracts/stills.py`) -- drop the seed/operation-dump fields entirely; a still result needs only `shot_sequence`, `shot_fingerprint`, `local_image_path`, `image_sha256`, `model`, `approved`, `qa_summary`, `cost_usd`.
- `orchestrator/contracts/production_assets.py:22-73` -- `ProductionAssetEntry`; STILL branch (lines 71-72) currently has no path constraint (Decision: add `generated/stills/` prefix check, mirroring VEO's `generated/veo/` check at line 61). Add a `from_still_result` classmethod mirroring `from_veo_result` (lines 76-96).
- `orchestrator/state/production_assets.py` -- `upsert_production_asset`/`load_production_assets`, reused unchanged. No new write path.
- `orchestrator/tools/gemini_tools.py:269-313,324-385` -- `generate_seed_image` (generic Gemini image-edit call + PNG write, reusable) and the VEO-specific `@tool generate_veo_seed` wrapper. Factor the raw Gemini-call mechanics out of `generate_seed_image` into a shared helper, then add a new `@tool generate_still` built the same way but constructing `StillResultContract` and writing under `generated/stills/`.
- `orchestrator/tools/veo_tools.py` -- entirely video-specific (GCS, polling, video previews); nothing reusable for stills.
- `orchestrator/agents/veo_agent.py:1-104` -- the exact pattern to mirror for `orchestrator/agents/stills_agent.py`: one/two tools via `AgentDefinition`, structured JSON-schema output, per-attempt fresh session, prompt-driven QA. `stills_agent` needs only one tool (`generate_still`) and one QA step (no seed step).
- `orchestrator/run.py:576-832` (`run_veo_stage`) -- the orchestration pattern to mirror for `run_stills_stage`: load+validate visual plan, filter by mode, cross-check stale entries, compute pending shots, per-shot bounded retry loop with budget reservation, keyed upsert on SUCCESS, halt on ceiling exhaustion. Insert `run_stills_stage` in `main()` alongside the existing `run_veo_stage` call (no data dependency between STILL and VEO shots -- disjoint shot sets, order is arbitrary).
- `orchestrator/settings.py:61,67,88` -- `image_model`/`image_call_cost_usd`/`_str_setting` already exist and are directly reusable for stills' cost estimate (same Gemini image-call type as seed generation); no new setting needed.
- `docs/implementation-artifacts/deferred-work.md:17` -- legacy script's field mismatch already flagged; this story must not reintroduce it.

## Tasks & Acceptance

**Execution:**
- [ ] `orchestrator/contracts/stills.py` (new) -- `StillResultContract`/`StillFailureContract`/`StillOutcomeContract`, mirroring `veo.py`'s shape minus seed/operation-dump fields
- [ ] `orchestrator/contracts/production_assets.py` -- add `generated/stills/` path constraint to the STILL branch; add `ProductionAssetEntry.from_still_result`
- [ ] `orchestrator/tools/gemini_tools.py` -- factor the raw Gemini image-edit call out of `generate_seed_image` into a shared helper; add `@tool generate_still` building a dynamic (non-hardcoded) prompt from `Shot` fields
- [ ] `orchestrator/agents/stills_agent.py` (new) -- `stills_agent` `AgentDefinition` + `generate_still_asset()`, mirroring `veo_agent.py`'s shape with one tool and one QA step
- [ ] `orchestrator/run.py` -- add `run_stills_stage`, mirroring `run_veo_stage`'s structure; wire into `main()`
- [ ] `prepare_remotion_stills.py` -- rewrite as a thin CLI over the shared tool logic, matching Story 2.3's `generate_veo_clips.py`/`prepare_veo_seed_images.py` treatment; remove the hardcoded shot-number prompt ladder
- [ ] `orchestrator/tests/test_stills_contracts.py`, `test_gemini_tools.py` (extend), `test_stills_agent.py`, `test_full_chain_resume.py` (extend) -- cover the matrix, budget gates, keyed upsert, resume, and QA retry

**Acceptance Criteria:**
- Given a validated visual plan, when the stills stage runs, then only STILL-mode shots are generated and their mode is unchanged
- Given a validated still result, when it is recorded, then it is written into `ProductionAssetsContract` via `upsert_production_asset` -- `stills_agent` implements no write path of its own
- Given a still fails visual QA, when `stills_agent` retries, then it self-corrects up to the configured ceiling before the run halts via the shared failure-report path
- Given all shots (VEO and STILL) have validated entries, when Epic 2 concludes, then `ProductionAssetsContract` has exactly one approved entry per visual-plan shot

## Implementation Notes

## Spec Change Log

## Review Triage Log

Review pass 1 (blind-hunter, edge-case-hunter, verification-gap; Codex not attempted given its confirmed sandbox failure on Story 2.1). Verdict counts: high 0, medium 1, low 5, false/pre-existing 6.

- **[patch, medium]** `orchestrator/contracts/stills.py::StillResultContract` -- blind-hunter + edge-case-hunter + verification-gap's "other findings", corroborated by all three layers. Unlike its sibling `VeoSeedContract` (which persists `source_asset_id` and is cross-checked by `run_veo_stage`), `StillResultContract` carries no `source_asset_id` field at all, so `run_stills_stage` has no way to verify a still's final self-reported outcome actually corresponds to one of `shot.source_asset_ids`. Verified: `build_still_spec` does validate asset membership at the tool-call *input* boundary, and `StillResultContract`'s own file-existence/hash validators make fabricating an unrelated image practically impossible (the agent has no filesystem tool) -- so the practical blast radius is narrow (mainly an audit-trail gap for a shot citing more than one source asset, not exercised by any current data) rather than an easy exploit. Still, worth closing for consistency with the sibling contract and defense-in-depth. Fix: add `source_asset_id` to `StillResultContract`, populate it from the validated asset in `generate_still_image`, and cross-check it in `run_stills_stage` mirroring `run_veo_stage`'s equivalent check.
- **[patch, low]** `prepare_remotion_stills.py`'s `main()` results-merge -- edge-case-hunter, verified against the real, committed `metadata/remotion_still_results.json` (a genuine pre-rewrite entry: `output_image_path: "generated/remotion_stills/shot_08_still.png"`, no `image_sha256`/`approved`). The rewritten CLI's merge logic carries old-schema entries forward verbatim when a run doesn't re-select every shot. Not consumed by the autonomous pipeline (this file is the manual CLI's own bookkeeping, mirroring the Veo CLIs' equivalent results files) but a real data-hygiene gap for whoever runs this CLI next. Fix: validate each existing entry against the current schema when merging, dropping ones that don't conform.
- **[patch, low]** `orchestrator/run.py` -- blind-hunter + edge-case-hunter, same defect, present in **both** `run_veo_stage` (pre-existing, Story 2.3) and the newly-mirrored `run_stills_stage`. The retry-ceiling-exhausted halt omits the last failure's own `failure.partial_artifact_paths` (e.g. the rejected image), unlike the non-retryable-failure halt immediately above it in the same function, which includes them. Fix in both stages for consistency, since it's cheap and now duplicated a second time.
- **[patch, low]** `orchestrator/agents/stills_agent.py` prompt -- blind-hunter. Unlike `veo_agent`'s prompt, which explicitly names "Include the seed's cost and path in partial_artifact_paths," this prompt only says to preserve "the image path, cost, and a concrete correction reason" without naming the actual contract fields -- easy for the model to omit them on a `still_qa` failure. Fix: name the fields explicitly.
- **[patch, low]** Test coverage -- blind-hunter. No test exercises the stills-stage equivalents of the "no usable Claude cost" halt or the "Invalid stills agent outcome" `ValueError` paths (mismatched shot/fingerprint, over-ceiling cost), even though the equivalent veo branches are load-bearing safety checks.
- **[patch, trivial]** `prepare_remotion_stills.py` -- blind-hunter. The old implicit "no `--shot`/`--all` → generate shot 1" default was replaced with a hard error -- a reasonable change, but undocumented. Add a one-line help/docstring note.

**Deferred** (real but lower-priority, pre-existing, or design-ambiguous -- see `deferred-work.md`): a shot citing a subtitle cue id absent from `SubtitleCuesContract` silently truncates narration context rather than halting (no current data triggers this); `_atomic_write_json` for the attempt record isn't wrapped in try/except (matches the existing, unwrapped pattern in `run_veo_stage`); the deterministic still filename is overwritten on each retry, weakening the audit trail for a rejected attempt's image (the attempt's own state JSON still records the rejection reason); nothing structurally limits the agent to one `generate_still` call per session beyond the prompt's instruction (matches `veo_agent`'s identical reliance on prompt compliance); `run_veo_stage`/`run_stills_stage` duplicate significant scaffolding with no shared helper (a real DRY concern, but a larger refactor of already-shipped, tested code, not a surgical fix for this round); seed and still generation share the same `image_call_cost_usd` cost estimate despite being different-effort Gemini calls; no end-of-run assertion compares `ProductionAssetsContract`'s shot count to the visual plan's (the module docstring already discloses this is intentionally not a runtime gate here -- likely Epic 3's integration concern, not this story's).

**Rejected/false**: `StillFailureContract.produced_by`'s unused `"orchestrator"` literal option (false -- `VeoFailureContract.produced_by` has the identical unused option; intentional sibling-contract symmetry, not copy-paste residue); `MAX_STILLS_ATTEMPTS` being a plain module constant rather than a `Settings` field (false -- matches this spec's own explicit Code Map decision and most other stages' established pattern; only `veo_agent` gets an env-configurable ceiling, and that was Story 2.3's own deliberate choice for its higher-cost paid calls).

## Design Notes

`stills_agent`'s prompt should explicitly instruct against duplicating the exact subject/scene twice in frame (the concrete "duplicate-subject defect" epics.md names) -- the legacy script's prompt text asked the model this directly but had no QA step to catch a failure; this story's QA step is the actual enforcement, not the prompt request alone.

## Verification

**Commands:**
- `conda run -n kayak-video python -m pytest orchestrator/tests` -- all existing and new tests pass without network access
- `conda run -n kayak-video python -m orchestrator.run source_images --help` -- CLI imports and exposes the extended pipeline without triggering generation
- `git diff --check` -- no whitespace errors
