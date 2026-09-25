---
title: 'Story 4.6: Langfuse tracing for every run'
type: 'feature'
created: '2026-09-25'
status: 'done'
baseline_commit: '9a018162eeb6538071506c154ad2ff52fca605aa'
route: 'full'
review_loop_iteration: 0
context:
  - '{project-root}/docs/epics.md'
  - '{project-root}/docs/implementation-artifacts/epic-4-context.md'
  - '{project-root}/docs/implementation-artifacts/spec-4-5-fresh-context-critic-visual-qa-reviewer.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A run is visible only through its log files; nothing shows each stage's prompt and output, or every vendor call's input, provider and result.

**Approach:** When `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, one Langfuse trace (session `<project>:<run_id>`) wraps each run. Stage, shot, vendor and deterministic spans are made with the Langfuse v4 SDK. Claude turns come from OpenInference's `ClaudeAgentSDKInstrumentor`, which takes effect because every agent calls `claude_agent_sdk.query` through the module at call time. Everything is redacted at the source, fails open, and the flush at exit is bounded.

## Boundaries & Constraints

**Always:**
- Story AC (`docs/epics.md`, "Story 4.6"), FR13–FR16, NFR2, NFR3, NFR5 and the Additional Requirements are binding. All 4.1–4.5 behaviour stays.
- With no keys, `langfuse`/OpenInference are never imported. Every tracing helper is then a no-op, and the run behaves exactly as it does today.
- Every tracing call is wrapped so that an exception is swallowed and logged once. The flush runs in a daemon thread joined with a timeout. Exit never waits on Langfuse's or OTel's own atexit handlers.
- Redaction happens before any value reaches a span, for our spans and for OpenInference's alike. It removes secrets (key/token patterns plus the values of secret-named env vars), data URIs and long base64 runs, and truncates text at `LANGFUSE_MAX_CHARS` (default 20000).
- Existing tests keep monkeypatching each agent module's `query`.

**Never:** Live calls to Claude, Gemini, Veo, Codex or Langfuse. Auto-creating the Langfuse project. Touching the user's dirty-tree output. Tracing `cover_agent`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Keys absent | no env | no tracing import, identical run | none |
| Keys present | env set | one trace, stage/shot/vendor spans, session + tag | none |
| One key only | partial env | tracing off, one warning line | none |
| Export raises | exporter throws | run result unchanged | swallowed |
| Flush hangs | exporter blocks | exit after the timeout | thread abandoned |
| Halt | failure report | halted span carries `reason_code`, `resumable`, `failed_shots` | none |
| Tool returns image | base64 content block | span holds a placeholder, never bytes | none |

</frozen-after-approval>

## Code Map

- NEW `orchestrator/tracing.py` -- the whole integration. `init_run_trace(project, run_id, *, span_exporter=None)` and `end_run_trace(exit_code)` with the bounded flush. `span(name, *, as_type, input, metadata)` is a sync context manager that yields a handle with `update()`, or a no-op. It is safe across to_thread, because contextvars are copied. `record_halt(report)`, `redact(obj)`, and `agent_query(agent_name)` complete the module. The last returns a module-level `query(*, prompt, options)`; with tracing off it returns `claude_agent_sdk.query(...)` itself, and with tracing on it wraps it in an `<agent>.query` span with metadata `system_prompt`, `skills` (requested and loaded from the init message), `model` and `max_budget_usd`, plus output `structured_output`/`result`, cost and usage.
- Langfuse v4 APIs, verified in site-packages:
  - `Langfuse(public_key, secret_key, base_url, tracer_provider=, span_exporter=, should_export_span=)`, where the resource manager is a singleton per public key;
  - `start_as_current_observation(name=, as_type=, input=, metadata=)`, then `.update(output=, metadata=, level=, status_message=)`;
  - `propagate_attributes(session_id=, tags=)`;
  - `flush()`/`shutdown()` have no timeout, and `shutdown` is registered with atexit;
  - the default filter `is_default_export_span` already exports the `openinference.*` scope.
- OpenInference 0.1.18: wrapt patches `claude_agent_sdk.query.query`, resets the package attr `claude_agent_sdk.query`, and appends its PreToolUse/PostToolUse/PostToolUseFailure matchers after ours (`_merge_hooks`). Its span name is `ClaudeAgentSDK.query`.
- `orchestrator/agents/{asset_analyst,story_agent,story_critic,visual_agent,veo_agent,stills_agent,visual_reviewer}.py` -- replace the imported `query` with `query = agent_query("<agent>")`. Nothing else changes.
- `orchestrator/run.py` -- `main`: trace after `start_run` (`run_id = log_path.stem`), try/finally `end_run_trace`. `run_stages`: stage spans asset_analyst, story_agent, visual_agent, voice, production, delivery. Preflight span with its result. `critique_story` call site → `story_critic` span. `_run_shot_production.make` → `shot_NN` span. TTS/STT/subtitle spans in `run_voice_pipeline`; a delivery span with the timeline path, render command and output path; `_halt` → `record_halt`.
- `orchestrator/tools/gemini_tools.py` -- `_generate_veo_seed`/`_generate_still` tool spans (provider, model, paths, cost, failure). `run_image_edit_ladder` gets a per-rung span with the exact prompt, rung and reference paths.
- `orchestrator/tools/veo_tools.py` -- the `_generate_veo_clip` span (model, seed path, GCS URI, resolution, duration ceiling, the outcome incl. RAI/failure, cost). `run_veo_generation` gets a `veo.generate_videos` span with the final prompt, negative prompt, clip duration and 9:16.
- NEW `orchestrator/tests/test_tracing.py`; `README.md`; `ARCHITECTURE-SPINE.md` AD-19.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/tracing.py` -- the integration above.
- [x] agent modules -- `agent_query` binding.
- [x] `run.py`, `gemini_tools.py`, `veo_tools.py` -- the spans.
- [x] `orchestrator/tests/test_tracing.py` -- every item in the story's test list, using an in-memory exporter and unique public keys.
- [x] `README.md` (env vars, one-time UI setup, the exact install command and versions), plus spine AD-19.

**Acceptance Criteria:**
- Given the full suite, when it runs, then the failing set equals the baseline (5 failed, 12 errors) and every new test passes.

## Implementation Notes

- Installed into kayak-video: langfuse 4.15.6, openinference-instrumentation-claude-agent-sdk 0.1.18 (openinference-instrumentation 0.1.66, openinference-semantic-conventions 0.1.39), opentelemetry-sdk/api 1.44.0, opentelemetry-exporter-otlp-proto-http 1.44.0.
- Baseline before the change: 778 passed, 5 failed, 12 errors, 5 skipped.
- Implemented directly by the orchestrating session, not a fresh subagent, as in 4.5: it already held the investigation, and earlier agents were killed by a no-progress watchdog.
- Progress, in order:
  - `tracing.py` done;
  - the 7 agent modules bound to `agent_query` (done);
  - run.py, gemini_tools and veo_tools spans done, with the suite still at baseline;
  - `test_tracing.py` done: 19 tests, suite 797 passed / 5 failed / 12 errors / 5 skipped, baseline set unchanged;
  - README and spine AD-19 done;
  - review pass 1 triaged and patched; suite 803 passed / 5 failed / 12 errors / 5 skipped (baseline set).
- A mutation check (tracing bound to an import-time `query`) made two tests call the real SDK `query` for about 2 minutes until they failed. No CLI session transcripts appeared under `~/.claude/projects`, so most likely no Claude API call was made. This is recorded for honesty.
- **Redaction point changed from the Design Notes' proxy idea.** A `_RedactingSpanProcessor` registered first on our provider rewrites the finished-span snapshot's attributes, events and status in `on_end`. The SDK passes that same snapshot to Langfuse's processor next, so this happens before queueing and before export media processing. OITracer calls `tracer.__class__.start_span(self, ...)`, which makes a tracer proxy fragile.

## Spec Change Log

## Review Triage Log

Pass 1 used three layers: blind hunter (13 findings), edge-case hunter (15), and verification gap (2 gaps and 3 others). Verdicts, counted once per distinct root cause: high 0, medium 7, low 12, false 4, maybe-false 0. Every route was a patch or a reject; there were no intent_gap, bad_spec or defer entries.

- medium, patch -- the span processor truncated serialized `langfuse.*` JSON, which left it invalid (BH1, EC3). It now skips truncation for `langfuse.*` keys, which `redact()` already truncated string by string, and a test covers it.
- medium, patch -- `_SECRET_FIELD` missed escaped quotes in nested JSON (BH2). Backslashes are now allowed before the quotes, and a test covers it.
- medium, patch -- a halt inside a step marked only that step, and the stage span looked successful (BH3, EC13, VG-other). `record_halt` keeps the fields, and `mark_stage_result` puts them plus ERROR on the stage. A test covers a render failure under delivery.
- medium, patch -- the tracing post-processing could raise into the run, from `tool_response_for_trace` or `traced_tool_call` after a paid body (EC1, EC2, EC15). Both are now guarded, and a test covers it.
- medium, patch -- nothing tested the story_critic span (VG1), and the docs called it a stage span (BH4). A test now shows it nests inside story_agent with its verdict, and README and AD-19 are reworded.
- medium, patch -- nothing tested the failed-shot span branch (VG2). A test now covers the ERROR level, the report entry and the status message.
- medium, patch -- a vendor failure contract left its span looking successful (EC9). It now sets ERROR plus the reason, with a test.
- low, patch -- the redaction fallback kept the status description (EC4). The status is now reset.
- low, patch -- `root.update` raising skipped `exits.close()` (EC8). They are now in separate trys.
- low, patch -- the TTS span closed before the duration check, and TTS/STT cost wasn't on its span (BH9, EC10). The check and the cost moved inside, and `cost_usd` is recorded.
- low, patch -- a reused (cached) TTS had no span (EC11). A `tts` span with `cached: true` is now recorded.
- low, patch -- the render command was rebuilt by hand, with no cwd (BH8, EC12). `render_remotion` now returns its real argv and cwd, the span records those, and `test_deterministic_tools` asserts it.
- low, patch -- `veo.generate_videos` had no output, and its duration included the slot wait (BH7). The span now opens after `vendor_slot` and records `operation_name`.
- low, patch -- the install line wasn't pinned (BH10). The README pins the tested versions and notes the `>=4,<5` requirement.
- low, patch -- the `agent_query` signature was narrower than the SDK's (BH11). It now forwards `options=None, **kwargs`.
- low, patch -- the key test used pattern-shaped keys (BH12). It now uses pattern-free keys, so the env-value pass is what's proven.
- low, patch -- the README said "logged once" and implied `LANGFUSE_MAX_CHARS` lives in settings (BH5, BH13b). Both are reworded.
- low, patch -- `skills="all"` would be recorded as letters (EC6). It is now handled; no agent passes it today.
- low, reject -- env-value redaction over-matches ordinary values of 8+ characters in *KEY*/*TOKEN* vars (BH13a, EC5). Erring toward redaction is the NFR3-safe side, and entropy heuristics would add complexity for a rare case.
- low, reject -- a second `init_run_trace` with the same public key reuses Langfuse's shut-down singleton (EC7). One run per process in production; the tests use unique keys.
- false -- one session per invocation (BH6). The story requires the session to be `<project>:<run_id>` with the `start_run` stamp. The `"run"` fallback is unreachable, because `start_run(RUN_STATE_DIR)` always returns a path.
- false -- a missing test for redaction dropping everything (BH10b). `test_the_span_processor_redacts_what_the_instrumentation_records` asserts the redacted attribute values exist, so a drop-all fallback fails it.
- false -- `record_halt` with a real dataclass is untested (BH12b). The mocked run goes through the real `_halt`, which builds a `FailureReport` dataclass.
- false -- `cover_agent` is untraced (EC14, VG-other). The frozen intent says "Never: tracing `cover_agent`".

## Design Notes

- **Why redact at the source.** Langfuse's exporter runs media processing *before* `mask_otel_spans`. Our image tools return `{"type":"image","data":<base64>,"mimeType":...}` blocks, which OpenInference records as the tool output and which Langfuse would upload as media. So `_RedactingSpanProcessor`, registered first on our provider, rewrites each finished span's attributes, events and status. The SDK hands that same snapshot to Langfuse's processor next, so this happens before queueing and export. Our own spans also `redact()` before calling Langfuse, because Langfuse media-processes observation input as it is set.
- **Nesting.** The shot span is opened inside each shot's task. The SDK spawns its reader and tool-handler tasks with `loop.create_task` inside the query iteration, so they inherit the context. `asyncio.to_thread` copies it. Vendor spans therefore nest shot → `<agent>.query` → `ClaudeAgentSDK.query` → tool span without explicit propagation, and a test proves it with two concurrent shots.
- **Own TracerProvider.** It is built with `shutdown_on_exit=False` and passed to both Langfuse and the instrumentor. It is never set globally, and Langfuse's atexit shutdown is unregistered, so the bounded flush is the only exit work.

## Verification

**Commands:**
- `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q -p no:cacheprovider` -- expected: 5 failed / 12 errors (the baseline set), more passed.
