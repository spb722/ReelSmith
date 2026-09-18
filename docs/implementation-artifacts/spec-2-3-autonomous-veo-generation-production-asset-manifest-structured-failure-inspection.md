---
title: 'Autonomous Veo Generation, Production Asset Manifest & Structured Failure Inspection'
type: 'feature'
created: '2026-09-17'
status: 'done'
route: 'full'
review_loop_iteration: 0
baseline_commit: '7a03785760f45eef420a4d1b5f1fc0e569291fc6'
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-2-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The manual Veo script can fall back to a raw screenshot, overwrite earlier shot results, and flatten failures into opaque exceptions. No authoritative production-asset contract exists for resume or downstream selection.

**Approach:** Add an autonomous `veo_agent` stage that follows `VisualPlanContract` mode assignments, creates and visually checks a contracted recomposed seed, generates and checks the clip, and returns a validated per-shot outcome. The orchestrator alone records approved clips through an atomic keyed upsert into `ProductionAssetsContract`; technical/RAI failures remain structured and diagnosable.

## Boundaries & Constraints

**Always:** Select VEO shots from the validated visual plan and never change their mode. A Veo call accepts a validated `VeoSeedContract`, whose file exists under `generated/veo_seeds/`; reject `source_images/` and uncontracted paths before a paid call. Inspect `operation.response` and `operation.result`, handle URI and inline bytes, materialize a deterministic local MP4, preserve the operation dump, and expose RAI/error details through `VeoFailureContract`. Validate seed and clip imagery before approval, account for paid calls, preserve other manifest entries, and resume without regenerating a matching approved VEO entry.

**Never:** Do not hand-edit locked narration, subtitle cues, shot timing, or generation modes. Do not infer production assets from bucket listings, filenames, timestamps, or “latest” output. Do not let an agent/tool write `metadata/production_assets.json`, invoke legacy Python scripts as subprocesses, add a suffixed script copy, generate still-mode shots, or run a real paid generation during automated tests.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Valid VEO shot and approved recomposed seed | Local deterministic MP4 plus approved video entry with seed/operation/GCS provenance | No error expected |
| Unsafe seed | Missing, outside seed directory, uncontracted, or under `source_images/` | No Veo request occurs | Immediate structured non-recoverable failure |
| SDK result variants | Videos under `response` or `result`, with URI or bytes | Extract/download exactly one usable local MP4 | Empty/malformed output becomes `VeoFailureContract` |
| RAI/operation failure | Filter metadata or operation error in either result root | Preserve operation dump and full reason/count/error | Structured failure feeds bounded retry/halt report |
| Resume/upsert | One or more approved VEO shots already recorded | Skip matching entries and merge newly approved shots | Invalid/stale manifest fails loud; never reset/overwrite |

</frozen-after-approval>

## Code Map

- `prepare_veo_seed_images.py::{build_seed_spec,generate_seed_image}` -- port recomposition into an in-process tool; keep the canonical CLI thin.
- `generate_veo_clips.py::{build_manifest,generate_one_clip,extract_video_outputs}` -- reuse parsing, remove raw-source fallback, and call shared tool logic.
- `orchestrator/tools/gemini_tools.py::{generate_narration_audio,gemini_server}` -- add/register contracted Veo-seed recomposition and return the generated image for agent QA.
- `orchestrator/tools/veo_tools.py` -- generation/polling, dual-root inspection, GCS/bytes materialization, operation dump, and QA previews.
- `orchestrator/contracts/veo.py` -- new seed/result/failure/outcome contracts with provenance, shot fingerprint, approval, cost, and retryability invariants.
- `orchestrator/contracts/production_assets.py`, `orchestrator/state/production_assets.py` -- partial per-shot manifest plus the orchestrator-owned atomic keyed upsert reused by Story 2.4.
- `orchestrator/agents/veo_agent.py` -- scoped tool-using agent; seed/clip semantic QA and prompt correction only, with no mode or shared-state writes.
- `orchestrator/run.py` -- stage after voice: load validated inputs, skip valid per-shot entries, enforce budget/retry/halt, and perform the sole atomic keyed upsert.
- `orchestrator/settings.py` -- image model, generation settings, polling/attempt limits, and externally configurable cost estimates; retain project/global endpoint/Veo defaults.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/contracts/veo.py`, `orchestrator/contracts/production_assets.py`, `orchestrator/state/production_assets.py` -- add provenance/fingerprint invariants and the sole atomic keyed-upsert path.
- [x] `orchestrator/tools/gemini_tools.py`, `orchestrator/tools/veo_tools.py`, `prepare_veo_seed_images.py`, `generate_veo_clips.py` -- expose registered in-process generation/inspection tools and remove raw-source fallback from the canonical CLI.
- [x] `orchestrator/agents/veo_agent.py` -- implement scoped seed/clip QA and structured success/failure output.
- [x] `orchestrator/run.py`, `orchestrator/settings.py` -- add dynamic, budgeted per-shot execution, approved-entry resume, and diagnostic halt artifacts.
- [x] `orchestrator/tests/test_veo_contracts.py`, `test_veo_tools.py`, `test_veo_agent.py`, `test_full_chain_resume.py` -- cover the matrix, budget gates, keyed upsert, and paid-call-free resume.

**Acceptance Criteria:**
- Given a validated visual plan, when the Veo stage runs, then only VEO-mode shots are generated and their mode is unchanged.
- Given two successful shots, when each result is recorded, then both remain in `ProductionAssetsContract` and only the orchestrator writes it.
- Given a completed operation in any supported SDK shape, when it is inspected, then a usable local clip is produced or a complete `VeoFailureContract` is surfaced.
- Given a resumed run, when an approved entry still matches its source shot, then that shot incurs no seed, Veo, or agent call.

## Implementation Notes

- Added strict seed, result, failure, outcome, and partial production-manifest contracts. File existence, content hashes, shot fingerprints, mode, approval, operation dumps, and QA preview provenance are revalidated at resume and immediately before paid generation.
- Moved seed recomposition and Veo generation/inspection into registered in-process SDK tools. The canonical CLIs now call those shared functions, dynamically follow VEO-mode shots, merge per-shot harness results, and have no raw-source fallback.
- Added a two-tool-only `veo_agent` that visually checks the seed and extracted clip previews. It cannot read the filesystem, run scripts, change modes, or write shared state.
- Added sequential VEO execution after the real voice-stage boundary, conservative pre-call budget reservation, per-shot bounded attempts, structured attempt/halt artifacts, stale-manifest rejection, paid-call-free approved-entry resume, and orchestrator-only atomic keyed upsert.
- Review pass 1 fixes (see Review Triage Log): `--approve-clip` added to `generate_veo_clips.py` mirroring `--approve-seed`; `inspect_completed_operation`/`generate_veo_seed` now return structured `VeoFailureContract`s instead of letting exceptions escape; the exception-charge path now charges the full per-attempt ceiling; `_json_safe` redacts string-form `video_bytes` too; an explicit GCS-bucket-configured guard was reinstated in both manual CLIs; `_operation_error` always returns a list; four test-coverage gaps closed (budget-ceiling assertion, exception-path, poll-timeout, seed-recomposition tool logic).
- Judgment call: item 8 (avoid redundant paid seed regeneration on a clip-only retry) was only partially resolved. Each `veo_agent` attempt is a stateless fresh Claude session with no plumbing to reuse a prior seed object across attempts in `run.py`; the prompt was clarified to skip re-litigating an already-approved seed's QA, but the agent still must call `generate_veo_seed` once per session. Fully eliminating the redundant paid Gemini call would need cross-attempt seed-reuse plumbing in `run.py`, which is out of scope for this patch round -- logged in `deferred-work.md`.

## Spec Change Log

- 2026-09-17: Implemented Story 2.3 and added full matrix, budget, upsert, and resume coverage.

## Review Triage Log

Review pass 1 (blind-hunter, edge-case-hunter, verification-gap; Codex not attempted given its confirmed sandbox failure on Story 2.1). This story was implemented in a separate session/tool before being handed off; verification (241 tests, CLI `--help`, `git diff --check`) was independently re-run and confirmed passing before this review. Verdict counts: high 1, medium 5, low 6, false/pre-existing 6.

- **[patch, high]** `generate_veo_clips.py` (`main`, lines ~126-150) -- blind-hunter + edge-case-hunter, same defect. `run_veo_generation`/`inspect_completed_operation` always construct `VeoResultContract` with `approved=False` (only `veo_agent`'s own LLM judgment ever sets `approved=True`, and that happens entirely inside the agent's structured output, never via a tool). The manual CLI calls the tool logic directly with no agent in the loop and has no `--approve-clip` flag mirroring the seed workflow's `--approve-seed` -- so `main()` always hits `raise RuntimeError("Veo clip requires explicit semantic QA approval before SUCCESS")` after any real, successful, paid Veo generation. Verified directly by tracing `approved` through `veo_tools.py`/`veo.py`/`veo_agent.py`. No test exercises this script's own `main()` (blind-hunter's 9th finding), which is exactly how this shipped unnoticed. Fix: add `--approve-clip` mirroring `--approve-seed`'s exact pattern, plus a test exercising `main()`'s success path.
- **[patch, medium]** `orchestrator/tools/veo_tools.py::inspect_completed_operation` -- blind-hunter + edge-case-hunter + edge-case-hunter's claim check, same defect. The `extract_video_previews` (ffmpeg subprocess) call is unguarded, unlike the neighboring `materialize_video_output` call -- a missing/broken `ffmpeg` raises an uncaught `FileNotFoundError` out of the MCP tool instead of a `VeoFailureContract`, contradicting both the module's "no bare errors" goal and the spec's own AC ("a usable local clip is produced or a complete `VeoFailureContract` is surfaced").
- **[patch, medium]** `orchestrator/tools/gemini_tools.py::generate_veo_seed` -- edge-case-hunter. `build_seed_spec`/`generate_seed_image` exceptions aren't caught; a billed Gemini failure crashes the tool with a bare `{"error": ...}` dict instead of the structured failure shape the rest of the module uses.
- **[patch, medium]** `orchestrator/run.py::run_veo_stage`'s exception handler (~line 703) -- blind-hunter, verified. On an exception from `generate_veo_asset`, only `reserved_external_cost` is charged -- but the per-attempt Claude budget ceiling passed to the agent is `remaining_budget - reserved_external_cost`, so up to that full amount of real Claude-token spend could have occurred before the crash and go uncounted. No cost value is available to charge instead (confirmed: `claude_cost` is only computed after this branch), so the fix is to charge the full per-attempt ceiling as the conservative worst-case on an unaccountable exception, not to recover a real value that was never returned.
- **[patch, medium]** `orchestrator/tools/veo_tools.py::_json_safe` -- blind-hunter. Raw `bytes` video payloads are redacted to `{"inline_bytes_length": ...}`, but a base64-encoded `video_bytes` **string** is inlined in full into the preserved `metadata/veo_operations/*.json` dump -- a real risk of multi-megabyte dump files accumulating over time, since these dumps are meant to be preserved indefinitely.
- **[patch, medium]** `generate_veo_clips.py`/`prepare_veo_seed_images.py` -- edge-case-hunter's claim check, verified. The pre-diff script had an explicit "GCS bucket not configured" guard before a paid call; the rewrite dropped it with no replacement. `orchestrator.run`'s own preflight (Story 1.1, AD-13) checks bucket *access*, not that it was explicitly configured (vs. silently using `DEFAULT_GCS_BUCKET_URI`) -- and these manual CLIs bypass that preflight entirely, since they're standalone scripts. Fix: reinstate an explicit non-default/non-empty bucket check before the paid call in the CLI path.
- **[patch, low]** `orchestrator/tools/veo_tools.py::_operation_error` -- blind-hunter. Returns a single error object when one source is found but a `list` when two are -- an inconsistent shape for downstream consumers. Fix: always return a list.
- **[patch, low]** `orchestrator/agents/veo_agent.py` prompt -- blind-hunter. Instructs "call `generate_veo_seed` exactly once" on every attempt, including a retry triggered only by a clip QA failure (seed already approved) -- forcing an avoidable paid seed regeneration, against the spec's own "account for paid calls" emphasis.
- **[patch, low]** `orchestrator/settings.py` -- blind-hunter. New string settings (`VEO_RESOLUTION`, `IMAGE_MODEL`, `VEO_MODEL`) have no validation, unlike this same diff's `_float_setting`/`_int_setting` helpers -- an empty/garbage value is accepted silently and only surfaces inside a paid API call.
- **[patch, low]** `generate_veo_clips.py::_approve_selected_seed` -- edge-case-hunter. Silently approves every entry matching a `shot_sequence` if duplicates exist in `seed_results.json`, with no way to tell which is authoritative.
- **[patch, low]** `orchestrator/tests/test_veo_contracts.py::production_entry` -- blind-hunter. Broken/inconsistent indentation inside a `ProductionAssetEntry(...)` call (syntactically harmless, a formatting slip).
- **[patch, low]** Test coverage -- verification-gap, pre-verified, four gaps grouped (same theme: paths that could regress silently). (1) `test_veo_agent.py` never asserts `options.max_budget_usd`, unlike the sibling `asset_analyst`/`story_agent`/`visual_agent` tests. (2) No test drives `run_veo_stage`'s exception-charge path (a raising `generate_veo_asset`). (3) No test drives `run_veo_generation`'s `POLL_TIMEOUT` branch. (4) `build_seed_spec`/`generate_seed_image`/`extract_generated_image` (the seed-recomposition tool logic) have zero test coverage anywhere in the repo, unlike the sibling `run_veo_generation` path which is extensively covered via fakes.

**Deferred** (real but lower-priority, pre-existing, or design-ambiguous -- see `deferred-work.md`): `generate_veo_clip`'s unguarded `Shot.model_validate(args["shot"])` (matches the existing repo-wide `@tool` convention of no defensive args validation, per Story 2.1's identical precedent); `run_veo_stage` using only the first of a shot's possibly-multiple `source_asset_ids` (not exercised by any current data, and correct multi-asset handling is a design question, not a clear bug); the retry-ceiling halt's generic failure message (consistent with the existing minimal-envelope halt convention across all stages); `extract_generated_image` only scanning the first inline image/text part of a Gemini response; a mismatched/malformed veo-outcome cross-check test gap (verification-gap's own suggested disposition: lower priority, defensive-only).

**Not this story's problem** (pre-existing, already-documented limitation): `upsert_production_asset`'s concurrent-invocation race -- the architecture spine already documents "v1 assumes sequential agent handoff... no concurrent writers to serialize yet" as an accepted scope boundary, not a defect introduced here.

## Design Notes

`ProductionAssetsContract` is partial in Story 2.3. Story 2.4 reuses its keyed-upsert path for stills and owns the final completeness check. Sequential orchestration plus atomic replacement prevents lost or torn writes.

Tool image results let the agent inspect seed/clip previews without filesystem or shell access. Dual-root operation inspection preserves compatibility with the working legacy path.

## Verification

**Commands:**
- `conda run -n kayak-video python -m pytest orchestrator/tests` -- all existing and Story 2.3 tests pass without network access.
- `conda run -n kayak-video python -m orchestrator.run source_images --help` -- CLI imports and exposes the extended pipeline without triggering generation.
- `git diff --check` -- no whitespace errors.

**Results (2026-09-17):** 241 tests passed; CLI help exited successfully; `git diff --check` reported no errors. Automated tests made no real Gemini, Veo, Claude, or GCS calls.
