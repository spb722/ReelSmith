---
title: 'Autonomous Screenshot Understanding + Single-Stage Resume'
type: 'feature'
created: '2026-09-16'
status: 'done'
route: 'full'
review_loop_iteration: 0
baseline_commit: '5d9b63c5e4bcecaf28eba787e14edc760fc344c9'
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Screenshot understanding today is two manually-run scripts (`ingest_assets.py`, `analyze_assets.py`'s Gemini call), no automatic retry, no schema-enforced contract, no awareness of a prior successful run.

**Approach:** Add an in-process `ingest` tool (Story 1.1's tool-wrapping pattern) and a new `asset_analyst` Claude agent that understands each screenshot via its own vision reasoning, producing a schema-validated `AnalyzedAssetsContract` with bounded self-correcting retry, hard-halt-plus-report on exhaustion, and a skip-if-already-validated resume check.

## Boundaries & Constraints

**Always:** `ingest` before `asset_analyst`, every invocation. `asset_analyst` understands images only via its own reasoning (`Read` on each screenshot) — no Gemini/`google-genai` call (AD-1). Every result validates against `AnalyzedAssetsContract` before acceptance; a failing attempt self-corrects, ceiling 4 (AD-5). Ceiling exhaustion halts via `build_failure_report`/`write_failure_report` (stage id `"asset_analyst"`) — no substitute asset, no live prompt, no notification (NFR6). Preflight (Story 1.1) still runs first. A persisted valid contract for this run skips `asset_analyst` entirely.

**Never:** Implement `story_agent`/`visual_agent`/`voice_agent` (1.3+) or full cross-stage resume (1.5). Modify `ingest_assets.py`/`analyze_assets.py` in place. Call `analyze_assets.py`'s Gemini client or its `RESPONSE_SCHEMA` object (mirror shape only). Invent a new credential mechanism — `claude-agent-sdk` shells out to the already-authenticated `claude` CLI.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh run, no prior contract | 6-10 screenshots in `source_images/` | `ingest` runs, then valid contract produced | No error |
| Attempt fails schema validation | Malformed/incomplete output | Self-correct retry, up to 4 attempts | Logged, no halt yet |
| Retry ceiling exhausted | 4 failed attempts | Run halts at `asset_analyst` | Failure report (stage id, contract, attempts, timestamp, partial paths); no substitute/notification |
| Persisted valid contract exists | Re-invocation, same reel | `asset_analyst` skipped | Proceeds on persisted contract, no new spend |
| No persisted contract | Any invocation | Runs normally | No error |

</frozen-after-approval>

## Code Map

- `ingest_assets.py` -- `sha256_file`/`inspect_image`/`discover_images` (asset_id = `img_{sha256[:12]}`) -- port as-is into the `ingest` tool, keeping the ID scheme.
- `analyze_assets.py:129-563` -- `RESPONSE_SCHEMA` (8 fields: content_type, source_text, visual, semantic_summary, named_entities, possible_story_roles, production, uncertainties) -- shape reference only; its Gemini call is never reused (AD-1).
- `orchestrator/state/run_manifest.py` -- reuse `iteration_counts["asset_analyst"]`, `build_failure_report`/`write_failure_report`/`RUN_STATE_DIR` verbatim.
- `orchestrator/run.py` -- extend `main()` past the preflight-pass branch (currently prints, returns 0) to run `ingest`, check `metadata/analyzed_assets.json` for a persisted valid contract, then the retry loop.
- `orchestrator/settings.py` -- reuse `Settings.max_budget_usd`; each `asset_analyst` attempt's `ClaudeAgentOptions.max_budget_usd` is the *remaining* budget (`settings.max_budget_usd - manifest.budget_spent_usd`), not the raw ceiling, so a fresh SDK session can't re-spend what earlier attempts already used (AD-7). No new settings fields.
- `claude-agent-sdk` (0.2.152) -- `AgentDefinition` holds `asset_analyst`'s prompt/tools for documentation (AD-2), but is invoked by lifting `.prompt`/`.tools` onto the **top-level** `ClaudeAgentOptions` (`system_prompt=`, `tools=`), never via `agents={}` + `extra_args={"agent": ...}`. Verified live: the CLI's `--agent <name>` delegation mode does not honor `output_format`/`--json-schema` at all -- `structured_output` silently comes back `None` and Claude free-forms JSON in `result` instead. Also verified live: the transport's default 1MB `max_buffer_size` is too small for a real multi-screenshot structured batch (with `Read`-returned image bytes in the transcript) -- raised to 20MB.
- Persistence: `ingest` writes `metadata/assets.json`; the validated contract is written to `metadata/analyzed_assets.json` (the path `story_director.py`/Story 1.3's `story_agent` reads). Resume requires the file to parse as `AnalyzedAssetsContract` **and** carry `produced_by: "asset_analyst"` **and** cover every currently-ingested source exactly -- a schema-shaped legacy `analyze_assets.py` (Gemini) manifest lacks `produced_by` and fails closed rather than silently satisfying resume (AD-1's boundary must hold even for pre-existing Gemini-era files, not just new calls).

## Tasks & Acceptance

**Execution:**
- [x] `pip install claude-agent-sdk==0.2.152` into `kayak-video`.
- [x] `orchestrator/contracts/__init__.py`, `analyzed_assets.py` -- `AnalyzedAssetsContract` (pydantic): the 8-field shape plus `asset_id`/`source_path`/`original_filename` -- the AD-4 gate.
- [x] `orchestrator/tools/__init__.py`, `deterministic_tools.py` -- `@tool`-wrapped `ingest` in `create_sdk_mcp_server(name="deterministic")`, porting `ingest_assets.py` -- replaces the manual script (AD-3).
- [x] `orchestrator/agents/__init__.py`, `asset_analyst.py` -- `AgentDefinition(tools=["Read"])`, reads each screenshot, emits one contract-shaped result via `output_format` -- no Gemini call (AD-1).
- [x] `orchestrator/run.py` -- wire ingest -> resume-check -> retry loop (ceiling 4) -> validate -> on exhaustion halt via `build_failure_report`/`write_failure_report` (`stage_id="asset_analyst"`); fold `total_cost_usd` into `RunManifest.budget_spent_usd`.
- [x] `orchestrator/tests/test_deterministic_tools.py`, `test_asset_analyst.py` -- ingest asset_id/hash stability + discovery; retry-to-halt and skip-on-persisted-contract paths (SDK call mocked).

**Acceptance Criteria:**
- Given `MAX_BUDGET_USD` already exhausted, when the orchestrator reaches `asset_analyst`, then it halts via the failure-report path before any Claude call (AD-7 applies here too, not only Gemini/Veo).
- Given a valid `metadata/analyzed_assets.json` with `produced_by: "asset_analyst"` that covers every currently-ingested screenshot, when the orchestrator re-invokes, then `asset_analyst` is skipped, no Claude call is made, and the file is left byte-identical.
- Given a `metadata/analyzed_assets.json` that is schema-valid but lacks `produced_by` (a legacy `analyze_assets.py`/Gemini manifest) or covers a different source set, when the orchestrator invokes, then it is treated as no persisted contract and `asset_analyst` runs for real -- a Gemini-era file must never silently stand in for Claude's own analysis (AD-1).
- Given `asset_analyst`'s output fails contract validation (schema or source-coverage), when it retries, then the next attempt's prompt includes the validation feedback and the previous output, and the run's persisted `iteration_counts["asset_analyst"]` increments per attempt, surviving a process restart.
- Given four consecutive invalid/failed attempts, when the ceiling is reached, then the run halts via `build_failure_report`/`write_failure_report` with `attempt_count == 4`, no `AnalyzedAssetsContract` is persisted, and a restart does not grant four more attempts.

## Implementation Notes

- Installed `claude-agent-sdk==0.2.152` in `kayak-video`; `google-genai` remains `2.23.0`. Verified SDK APIs through Context7 and the installed SDK/CLI signatures.
- Preserved the existing `assets[].analysis` envelope and all eight nested analysis fields. Existing `metadata/analyzed_assets.json` validates against the new contract and matches all six ingested source identities. Additive legacy provenance fields remain accepted.
- Ingest runs deterministically through the registered SDK tool's in-process handler before every resume check. Resume also checks complete source coverage, using hash-derived IDs and normalized paths, so an unrelated or incomplete contract cannot skip analysis.
- The named `asset_analyst` definition runs directly via the SDK's `--agent` option with only `Read` available, no external MCP configuration, and no permission prompts. Structured output is gated again by Pydantic and source coverage.
- Attempts and reported costs persist in the existing run manifest. Each fresh SDK session receives only the remaining run budget. Failed structured output and validation feedback feed the next attempt; the four-attempt ceiling survives restarts. Partial SDK results are retained in `orchestrator_runs/asset_analyst_attempt_N.json` for failure reports.
- No legacy scripts, locked artifacts, or production metadata were modified. Later agents and full-chain resume remain out of scope.

**Post-review fixes (2026-09-16), triggered by a live-run requirement before acceptance:**
- Corrects the note above: letting a legacy Gemini manifest "validate against the new contract" was the bug, not a feature. Added `AnalyzedAssetsContract.produced_by: Literal["asset_analyst"]` (required, no default). The real, pre-existing `metadata/analyzed_assets.json` (from a manual `analyze_assets.py` run, dated 2026-09-14) now correctly fails validation and can no longer silently satisfy resume.
- Corrects the note above about `--agent`: a real (unmocked) run against the installed `claude` CLI (2.1.273) showed `--agent asset_analyst` combined with `--json-schema` returns `structured_output=None` on every attempt -- the schema constraint is silently dropped under agent delegation. `analyze_assets()` now runs `asset_analyst`'s prompt/tools as the **top-level** session (`system_prompt=`, no `agents=`/`extra_args=`), which does honor `output_format`. Confirmed with an isolated probe before and after, and now with the full pipeline.
- The same live run first hit `CLIJSONDecodeError: ... exceeded maximum buffer size of 1048576 bytes` -- the transport's default 1MB buffer, too small once `Read`'s image bytes and a 6-asset structured batch are in the transcript. Set `ClaudeAgentOptions.max_buffer_size=20 * 1024 * 1024`.
- One real, unmocked end-to-end run (`python -m orchestrator.run source_images`) after both fixes: `asset_analyst` correctly rejected the persisted legacy file, ran for real, and produced a validated, schema-conformant `AnalyzedAssetsContract` -- see Verification for the transcribed content proving genuine per-image understanding (not schema-shaped filler).
- Added a `gemini_legacy` case to the stale-persistence parametrized test, and a `max_buffer_size`/no-`--agent` assertion to the fresh-run test. All 57 orchestrator tests still pass (mocked).

## Spec Change Log

## Review Triage Log

## Design Notes

`asset_analyst` uses the built-in `Read` tool, not hand-built base64 image blocks -- simpler, and Claude's own reasoning is the point (AD-1). `output_format={"type": "json_schema", "schema": AnalyzedAssetsContract.model_json_schema()}` makes the SDK self-correct toward validity; the orchestrator's pydantic check is still the final gate.

**Explicit decision -- Gemini-era files never satisfy resume.** The repo already had a real `metadata/analyzed_assets.json` from a manual `analyze_assets.py` (Gemini) run before this story. Its shape happens to match `AnalyzedAssetsContract`'s 8 analysis fields closely enough that, without a marker, it would validate and silently skip `asset_analyst` forever -- meaning Claude's own understanding would never actually run, contradicting AD-1 by omission even though no new Gemini call is made. `produced_by: Literal["asset_analyst"]` (required, no default) closes this: a legacy manifest lacks the field and fails validation, so it can never stand in for a Claude-produced contract.

**`--agent` delegation silently drops `output_format`.** Verified live (not just unit-tested): invoking `asset_analyst` via `agents={}` + `extra_args={"agent": "asset_analyst"}` returns `structured_output=None` every time -- Claude follows the agent's prompt faithfully but free-forms its own JSON shape in `result` instead of the requested schema, and the CLI gives no error indicating the schema was dropped. Running the same prompt/tools as the **top-level** session's `system_prompt` (no `agents`/`extra_args`) does honor `output_format`. The unit tests could not catch this because they mock `ResultMessage` directly at the shape the code expects; only an unmocked run against the real `claude` CLI surfaced it.

## Verification

**Completed 2026-09-16 (post-review round):**
- `conda run -n kayak-video python -m pytest orchestrator/tests -v` — **57 passed** (added a `gemini_legacy` stale-persistence case and a no-`--agent`/`max_buffer_size` assertion). SDK calls still mocked here; this is necessarily the wrong tool to catch what the two live runs below caught.
- **Live run 1** (pre-fix, `python -m orchestrator.run source_images`): correctly rejected the pre-existing legacy `metadata/analyzed_assets.json` (missing `produced_by`) as expected, then failed all 4 `asset_analyst` attempts with `CLIJSONDecodeError: ... exceeded maximum buffer size of 1048576 bytes` — a real defect, not a mock artifact. Halted per spec (failure report, `attempt_count: 4`, no contract written). Led to the `max_buffer_size=20MB` fix.
- **Live run 2** (after the buffer fix, before the `--agent` fix): transport succeeded, but every attempt's `ResultMessage.structured_output` was `None` (`--agent` delegation silently drops `output_format`) — isolated and confirmed with two standalone probe scripts (with and without `--agent`, with and without `Read`+images) before touching `asset_analyst.py`. Led to the top-level-`system_prompt` fix.
- **Live run 3** (both fixes applied): `python -m orchestrator.run source_images` — real legacy file correctly rejected again, one `asset_analyst` attempt (no retry needed), cost **$0.570431** (`orchestrator_runs/run_manifest.json`), wrote a validated `metadata/analyzed_assets.json` with `produced_by: "asset_analyst"`. Per-screenshot content is specific and mutually distinct — e.g. asset `img_dcfce1645810` transcribes the actual visible heading "The Backwards Law" and body text "Starting with the 1st idea: The Backwards Law. Smart people love complexity. Brains don't.", with a correctly-placed heading/body `region` and a described "orange flame-shaped cartoon mascot ... pointing upward"; asset `img_ef0dc599dff0` instead transcribes "Lifting the concept from philosopher Alan Watts, Manson calls this the Backwards Law." This is real per-image transcription and description, not schema-shaped filler.

**Commands:**
- `conda run -n kayak-video python -m pytest orchestrator/tests/test_deterministic_tools.py orchestrator/tests/test_asset_analyst.py -v` -- expected: all pass, no real API calls
- `conda run -n kayak-video python -c "import orchestrator.agents.asset_analyst, orchestrator.tools.deterministic_tools, orchestrator.contracts.analyzed_assets"` -- expected: clean import
