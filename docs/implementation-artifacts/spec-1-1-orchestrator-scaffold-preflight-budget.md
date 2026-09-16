---
title: 'Orchestrator Scaffold, Credential Preflight & Budget Ceiling'
type: 'feature'
created: '2026-09-15'
status: 'done'
route: 'full'
review_loop_iteration: 1
baseline_commit: 'f3c17dfb511b2feacf5942eeb0e6440a9e314fc2'
context: ['{project-root}/AGENTS.md', '{project-root}/docs/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The pipeline is entirely manual today — every stage is a standalone script the operator runs and verifies by hand — and there is no shared infrastructure to safely automate it: no credential/environment check before spending, no budget guard, and no central config (PROJECT_ID/region/model duplicated across 9+ scripts).

**Approach:** Stand up the `orchestrator/` package skeleton with a centralized settings module and a preflight check verifying Google Cloud ADC/Vertex auth, GCS bucket access, and remaining budget before any stage runs — the foundation every later agent/tool reuses.

## Boundaries & Constraints

**Always:** Preflight runs before any Gemini/Veo/STT API call, on both a fresh and a resumed invocation, never skipped because a resume isn't literally "stage 1" (AD-13). Settings module is the single source for `PROJECT_ID`, region, and model names. Budget ceiling is configurable, not hardcoded, and checked before preflight passes (AD-7). A preflight/budget failure halts and writes a failure report using the shared minimal envelope (stage id, failed contract name, attempt count, timestamp, partial-artifact paths — AD-6/NFR5).

**Never:** Implement any agent/tool for an actual pipeline capability (asset understanding, story writing, etc.) — that starts at Story 1.2. Modify any of the 14 existing root scripts — untouched until their own migration story. Invent a new credentials mechanism — reuse the existing `google.auth.default(scopes=[...])` ADC pattern already used in `extract_word_timing.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Missing ADC | No `gcloud auth application-default login` configured | Preflight fails immediately | Failure report written; no API call attempted |
| Bucket inaccessible | Valid ADC, configured GCS bucket unreachable/unpermitted | Preflight fails the same way, before stage 1 | Failure report written |
| Budget already exhausted | `max_budget_usd` config already fully spent | Orchestrator halts before starting | Same failure-report path as a mid-run budget halt |
| All checks pass | Valid ADC, bucket access, budget remaining | Preflight passes; stage 1 allowed to begin | No error |
| Resumed invocation | Run re-invoked after a prior halt (not literally "stage 1") | Preflight still executes before the first stage that will actually run | No error; never skipped |

</frozen-after-approval>

## Code Map

- `generate_veo_clips.py:22-50` -- existing pattern reading `PROJECT_ID`/`LOCATION`/bucket URI from env vars with hardcoded fallbacks (`PROJECT_ID` default `gen-lang-client-0240752803`, region default `global`, bucket `gs://sachin-kayaking-video-test/book_reels/veo/`); source of real values the new settings module centralizes.
- `extract_word_timing.py:10-11,104-106` -- existing ADC pattern (`google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])`); reuse this exact call for the preflight credential check.
- `analyze_assets.py:19-20`, `story_director.py:18-19`, and 6 other scripts -- each hardcodes `PROJECT_ID`/`LOCATION` as a duplicated constant; not touched in this story, but the settings module's field names must match so later per-story migrations are a drop-in.
- `generate_veo_clips.py:32` -- `MODEL = "veo-3.1-fast-generate-001"` (Veo model, hardcoded). `analyze_assets.py:21` and `story_director.py:20` -- both `MODEL = "gemini-2.5-flash"` (Gemini model, hardcoded, duplicated across 2+ scripts). The frozen "Always" constraint requires the settings module to be the single source for these too -- settings needs distinct fields for the Veo model and the Gemini model (they are different model families/values), defaulting to these exact literals.
- No `orchestrator/` directory exists yet -- created from scratch by this story.
- `kayak-video` conda env (Python 3.11.16, `/opt/homebrew/anaconda3/envs/kayak-video/bin/python`) does not have `claude-agent-sdk` installed -- confirmed via `pip show`.
- `docs/architecture/architecture-book_reels-2026-09-15/ARCHITECTURE-SPINE.md` AD-7 (budget), AD-13 (preflight), Structural Seed -- authoritative shape for `orchestrator/run.py` and `orchestrator/state/run_manifest.py`.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/__init__.py` -- create empty package marker -- makes `orchestrator` importable
- [x] `orchestrator/settings.py` -- centralized settings reading `PROJECT_ID`, `LOCATION`, GCS bucket URI, `max_budget_usd`, a Veo model name, and a Gemini model name from env vars with the existing scripts' values as defaults (see Code Map for the exact literals/locations) -- single source every tool/agent reads, replacing per-script duplication. `float()`-parsed env vars (`max_budget_usd`) must raise a clear, caught error path rather than an uncaught `ValueError` propagating out of `load_settings()` or module import.
- [x] `orchestrator/preflight.py` -- calls `google.auth.default(scopes=[...])`, verifies configured GCS bucket reachability, and checks remaining budget against `max_budget_usd`; returns a structured pass/fail result -- the AD-13 credential/env check and AD-7 budget gate. The budget check must treat a non-finite (`NaN`/`inf`) `max_budget_usd` or `budget_spent_usd` as a failure, not as "budget remaining."
- [x] `orchestrator/state/__init__.py`, `orchestrator/state/run_manifest.py` -- tracks budget spent and writes the minimal failure-report envelope (stage id, failed contract name, attempt count, timestamp, partial-artifact paths) -- the shared structure every later agent's halt reuses (AD-6/NFR5). `write_failure_report` must write atomically (temp file + `replace()`, matching `save_run_manifest`'s existing pattern) so a crash mid-write can't leave a truncated report. `load_run_manifest` must not raise on a corrupt/invalid `run_manifest.json` (bad JSON, non-numeric `budget_spent_usd`) -- fall back to a fresh `RunManifest()` in that case so a corrupt manifest can never block preflight from running.
- [x] `orchestrator/run.py` -- CLI entrypoint (`python -m orchestrator.run <source_images_dir>`) that runs preflight first and halts via the failure-report path on any failure -- the top-level driver later stories extend
- [x] `orchestrator/tests/test_preflight.py` -- unit tests covering all 5 I/O matrix scenarios -- proves the AC directly rather than by inspection. Bucket-related tests must assert on the actual bucket name passed to the storage client (e.g. `"test-bucket"` parsed from `"gs://test-bucket/..."`), not only on pass/fail, and must cover `parse_bucket_name`'s two `ValueError` branches (non-`gs://` URI, empty bucket). The failure-report test must load and assert on the written JSON's fields (`failed_contract_name`, `reason`, `attempt_count`, `partial_artifact_paths`), not just the file's existence.
- [x] `orchestrator/tests/test_settings.py` -- unit tests for `orchestrator/settings.py` covering: each field's default value, each field's env-var override, and a malformed `MAX_BUDGET_USD` producing the clear caught error path (not an uncaught `ValueError`)
- [x] A test exercising the `save_run_manifest` / `load_run_manifest` round trip directly (write, then read back, assert equality) -- currently only monkeypatched managers are exercised

**Acceptance Criteria:**
- Given a missing ADC credential, when `orchestrator.run` is invoked, then preflight fails immediately with a failure report and no API call is made.
- Given valid ADC but an inaccessible configured bucket, when preflight runs, then it fails the same way, before stage 1 begins.
- Given a budget ceiling already exhausted, when the orchestrator attempts to start, then it halts via the same failure-report path before any paid tool call.
- Given valid credentials, bucket access, and remaining budget, when preflight runs, then it passes and stage 1 is allowed to begin.
- Given the run is re-invoked after a prior halt, when preflight runs, then it still executes before the first stage that will actually run.
- Given the centralized settings module, when any future tool needs `PROJECT_ID`/region/the Veo model name/the Gemini model name, then it reads from that one shared source.
- Given a corrupt `run_manifest.json` or a malformed `MAX_BUDGET_USD` env var, when the orchestrator starts, then it does not crash with an uncaught exception -- it fails safe (fresh manifest, or a caught/reported config error) rather than skipping preflight or silently disabling the budget ceiling.

## Implementation Notes

## Spec Change Log

- **Iteration 1 (bad_spec loopback).** Triggering finding: the frozen "Always" constraint requires the settings module to be "the single source for `PROJECT_ID`, region, and model names," but the original Tasks/Code Map never named the model constants or a task to centralize them, so the first implementation's `orchestrator/settings.py` had no model field at all. Amended: Code Map now cites the three hardcoded `MODEL` locations (`generate_veo_clips.py:32` Veo, `analyze_assets.py:21`/`story_director.py:20` Gemini); Tasks now require distinct Veo-model and Gemini-model fields on `Settings`; the last Acceptance Criterion now names the model fields explicitly. Bundled in in the same pass (to avoid a second loopback): five `patch`-level robustness findings from this review round (malformed `MAX_BUDGET_USD` crash, NaN budget silently disabling the ceiling, unguarded corrupt-manifest load, non-atomic failure-report write, and three test-strength gaps — bucket-name assertions, failure-report content assertions, settings/manifest-round-trip tests) — see Review Triage Log for each finding's evidence. KEEP for re-derivation: `Settings` dataclass shape and its four existing fields/env-var names/defaults; `PreflightResult`'s pass/fail/reason shape and the ordered credentials→bucket→budget check sequence that stops at the first failure; the injectable `credentials_factory`/`storage_client_factory` pattern for testability; `RunManifest`/`FailureReport` dataclass shapes and `build_failure_report`/`write_failure_report` function signatures; `run.py`'s CLI shape and preflight-first-then-halt-via-failure-report flow; all 7 existing test scenarios in `test_preflight.py` (extend, don't remove). Known-bad state avoided: a settings module that silently omits model-name centralization despite the frozen requirement, and four unguarded exception paths that could crash before the AD-6 failure-report mechanism runs.

## Review Triage Log

- **bad_spec / high** — `orchestrator/settings.py` has no field for model names, but the frozen "Always" constraint requires the settings module to be "the single source for `PROJECT_ID`, region, and model names." Verified: `generate_veo_clips.py:32` (`MODEL = "veo-3.1-fast-generate-001"`), `analyze_assets.py:21` and `story_director.py:20` (`MODEL = "gemini-2.5-flash"`) each hardcode a model constant untouched by the new settings module. Root cause is in the non-frozen Code Map/Tasks (they never named these locations or a model-field task), not the frozen intent itself. Routes to bad_spec loopback. (edge-case-hunter)
- **patch / medium** — `load_settings()`'s `float(os.getenv("MAX_BUDGET_USD", ...))` has no error handling; a malformed value raises an uncaught `ValueError` at import time or inside `run.py:main()`, bypassing the AD-6 failure-report path the rest of this story is built around. (blind-hunter, edge-case-hunter x2)
- **patch / medium** — `_check_budget`'s `remaining <= 0` is always `False` when an operand is NaN (`float("nan")` parses without raising), silently disabling the AD-7 budget ceiling instead of failing safe. (edge-case-hunter)
- **patch / medium** — `load_run_manifest`/`RunManifest.from_dict` has no error handling for a corrupt/invalid `run_manifest.json` (bad JSON, non-numeric `budget_spent_usd`); it's called unconditionally in `main()` before preflight, so corruption crashes before the resumed-invocation preflight guarantee (AD-13) can run. (edge-case-hunter)
- **patch / low** — `write_failure_report` writes directly via `path.open("w")` instead of the tmp-file-plus-`replace()` atomic pattern `save_run_manifest` already uses in the same module, risking a truncated failure report on a crash mid-write. (blind-hunter)
- **patch / medium** — pre-verified (verification-gap): none of the 4 bucket-related tests assert on `FakeStorageClient.requested_bucket_names`, and `parse_bucket_name`'s two `raise ValueError` branches are untested; a regression that mis-parses the bucket name would pass every current test while breaking real preflight against GCS. (verification-gap)
- **patch / medium** — pre-verified (verification-gap): `test_run_main_writes_failure_report_on_preflight_failure` only checks that a `failure_preflight_*.json` file exists, never parses its content; a swapped/dropped `failed_contract_name`/`reason`/`attempt_count` field would go undetected. (verification-gap)
- **patch / low** — no tests exist for `orchestrator/settings.py` itself (defaults, env-var overrides). (blind-hunter)
- **patch / low** — `save_run_manifest`/`load_run_manifest` round trip is never exercised by any test (only monkeypatched managers are used in `test_preflight.py`). (blind-hunter)
- **false** — claim: `RUN_STATE_DIR = Path("orchestrator_runs")` is a bare CWD-relative path that risks a disconnected state tree if invoked from another directory. Disproved: `AGENTS.md:27` documents "All scripts run from the repo root" as an existing, established project convention every other script already relies on; this introduces no new risk. (blind-hunter)
- **false** — claim: no dependency-manifest update for the new `google-cloud-storage` import. Disproved: the repo has no `requirements.txt`/`pyproject.toml`/any dependency manifest at all (confirmed absent), so there is nothing this story failed to update. (blind-hunter)
- **defer** — no `.gitignore` entry for `orchestrator_runs/`. Real but pre-existing: the repo has no `.gitignore` at all, and `metadata/`, `generated/`, etc. are already untracked the same way — not caused by this story. (blind-hunter)
- **false** — claim: argparse-level behavior (missing arg, `-h`, exit code 2) is untested. This is Python stdlib `argparse` behavior, not code this story wrote; no demonstrated bad outcome. (blind-hunter)
- **low, rejected** — `write_failure_report`'s `output_dir.mkdir(...)` is unguarded; if directory creation fails, the failure report is lost to an unhandled traceback. Rejected: unlikely in everyday local use, and the fix (try/except plus a stderr fallback) adds a guard branch, not a direct correction. (edge-case-hunter)
- **low, rejected** — `SETTINGS` is computed once at import time as a module singleton, which is order-dependent for tests overriding env vars. Rejected: no current test hits this, and a real fix changes public surface rather than being a direct correction. (blind-hunter)

**Iteration 2 (post-re-derivation review):**

- **patch / medium** — pre-verified (verification-gap): `run.py:main()` wires `manifest.budget_spent_usd` into `run_preflight`, but no test exercises the real (unmocked) `run_preflight` through `main()` with a manifest carrying nonzero/exhausted spend; hardcoding `budget_spent_usd=0.0` in `run.py` would pass every current test while silently defeating the resumed-invocation budget gate (AD-13/AD-7). (verification-gap)
- **patch / medium** — verified by direct execution: `load_run_manifest` crashes with an uncaught `AttributeError` when `run_manifest.json`'s top-level JSON is not an object (e.g. `null`, a number, or a list) — `data.get(...)` is called before checking `data` is a dict, and the surrounding `except` clause doesn't catch `AttributeError`. This directly violates this iteration's own newly-added task requirement that `load_run_manifest` must not raise on a corrupt/invalid manifest. (edge-case-hunter)
- **patch / medium** — verified by direct execution: `run_preflight(settings=None)` (its own documented default) raises an uncaught `SettingsError` when `MAX_BUDGET_USD` is malformed, contradicting `run_preflight`'s own contract of always returning a structured `PreflightResult` rather than raising. `run.py`'s current caller isn't affected (it pre-loads settings and catches the error itself), but any future caller (e.g. a Story 1.2 agent) using the documented `settings=None` default would crash instead of getting a result. (edge-case-hunter)
- **patch / low** — `load_run_manifest`'s `path.exists()` check runs outside the function's own `try/except` block; an `OSError` from that stat call (e.g. a permissions problem) would propagate uncaught, contradicting the "never raise" fail-safe guarantee. Fix is trivial (move the check inside the guarded block). (blind-hunter)
- **defer** — `AGENTS.md:32` ("Config ... is hardcoded per-script ... not centralized") is now stale/misleading given this story's centralized `orchestrator/settings.py`; `AGENTS.md:26`'s dependency list (`google-genai`, `google-auth`, `Pillow`) omits the new `google-cloud-storage` and `pytest` dependencies this story relies on. Both fixes edit an agent-context file (AGENTS.md), which routes to defer regardless of severity. (blind-hunter)
- **defer** — `orchestrator_runs/` (run manifest + failure reports) is unmentioned in `AGENTS.md`'s "Where things are" section, unlike `metadata/`/`generated/`. Overlaps with the already-deferred `.gitignore` gap; fix would also touch AGENTS.md, routing to defer. (blind-hunter)
- **false** — claim: `run.py`'s `main()` should validate `source_images_dir` exists and should call `save_run_manifest` after a successful preflight. Disproved: this story implements no pipeline stage that consumes the directory or spends budget (explicitly out of scope per the frozen "Never" boundary — that starts at Story 1.2), so there is nothing yet to validate or to persist; the save/load round trip itself is already directly tested. (blind-hunter)
- **low, rejected** — `RunManifest.from_dict` doesn't validate that `iteration_counts`'s values are ints (only that the field itself is a dict), and no test exercises the "not a dict" fallback. Rejected: nothing in this story writes to `iteration_counts` yet, and the fix adds validation branches rather than being a direct correction. (blind-hunter, edge-case-hunter)
- **low, rejected** — `write_failure_report`'s output filename is built from `stage_id`/`failed_contract_name` with no sanitization; a future caller passing path-unsafe characters could write outside `RUN_STATE_DIR`. Rejected: today's only call sites pass hardcoded safe literals ("settings", "preflight"), and the fix adds a sanitization guard rather than being a direct correction. (blind-hunter, edge-case-hunter)
- **low, rejected** — `_atomic_write_json` has no cleanup if `json.dump` fails partway (e.g. disk full), leaving a stray `.tmp` file. Rejected: unlikely in everyday local use, and the fix (try/finally with cleanup) adds a guard rather than being a direct correction. (blind-hunter)
- **low, rejected** — a negative `budget_spent_usd` (e.g. from a hand-edited manifest) inflates `remaining` and lets preflight pass despite broken spend accounting. Rejected: nothing in this story's code can produce a negative value yet, and the fix adds a guard branch. (edge-case-hunter)
- **low, rejected** — `google.auth.default()`/`bucket.exists()` calls have no explicit timeout, so a network stall could hang preflight indefinitely instead of failing fast. Rejected: not called for by the I/O matrix, uncommon in everyday local use, and the fix adds a parameter/guard rather than being a direct correction. (edge-case-hunter)
- **low, rejected** — concurrent orchestrator processes writing to the same `state_dir` would race on `_atomic_write_json`'s fixed `.tmp` filename. Rejected: this is a single-operator, sequential-use tool (per AGENTS.md's "one engineering change at a time" convention), and the fix (a unique temp-file name) adds complexity for a scenario this tool doesn't support. (edge-case-hunter)

## Design Notes

Preflight returns a structured result (pass/fail + reason) rather than raising bare exceptions, so `run.py` can route any failure through the same failure-report writer every later agent's halt uses (AD-6/NFR5) -- one code path for "preflight failed" and "an agent exhausted retries," not two.

## Verification

**Commands:**
- `conda run -n kayak-video python -m pytest orchestrator/tests/test_preflight.py -v` -- expected: all 5 I/O-matrix scenarios pass
- `conda run -n kayak-video python -c "import orchestrator.settings"` -- expected: imports cleanly, no per-script literal duplication
