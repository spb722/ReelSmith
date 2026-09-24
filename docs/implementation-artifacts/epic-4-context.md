# Epic 4 Context: Book Reels runs as a skill-based, parallel, guarded and observable Claude Agent SDK pipeline

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Move book_reels to a proper Claude Agent SDK multi-agent design without changing who sequences the pipeline. Python still runs the stages in order, and each LLM stage stays its own top-level `query()` with a JSON-schema `output_format` contract. Four things change. Each agent's know-how moves into skills in one pipeline plugin. Independent Veo and still shots run concurrently inside per-attempt budget reservations. Hooks enforce the budget and classify vendor refusals, so a vendor limit never burns attempts. A fresh-context reviewer approves generated work. On top of that, every run is traced in Langfuse. The result is a faster reel, a budget that can't be broken, clean resumable halts, and full visibility into each step. There is no PRD or UX doc. The requirements come from the design agreed on 2026-09-24, within the existing architecture spine.

## Stories

- Story 4.1: Agent Know-How as Skills in a Pipeline Plugin (with SDK Spike)
- Story 4.2: Guard Hooks — Budget Guard and Vendor-Limit Classification
- Story 4.3: Concurrency-Safe Shot Plumbing and Per-Attempt Budget Reservations
- Story 4.4: Concurrent Shot Launch and Failure Scoping
- Story 4.5: Fresh-Context Critic and Visual-QA Reviewer
- Story 4.6: Langfuse Tracing for Every Run

## Requirements & Constraints

- **Parity:** existing projects still produce contracts that validate, and the regression harness still passes. Contract schemas change only by adding fields, and every new field has a default so old records still validate.
- **Test baseline:** measured with `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q`, the baseline is 565 passed, 5 failed, 12 errors, 5 skipped. The known failures and errors are listed in the epics file. Every story must leave this no worse. New tests make no live vendor calls: they mock the SDK by monkeypatching the module-level `query` with an async generator that yields a `ResultMessage`.
- **Budget:** the per-run ceiling holds under concurrency. The only allowed overshoot is a single Claude turn beyond its cap. For the same set of completed shots, running in parallel must never cost more than running sequentially.
- **Never wait live:** "pause" always means a halt with a failure report that can be resumed. Vendor-limit and budget-exhausted halts are resumable. For budget, that means resumable after raising `MAX_BUDGET_USD`, because spend accumulates across runs.
- **Vendor refusals** are classified before anything is charged:
  - `transient` is a 429 or "rate limit". It gets at most 3 backoff retries, within about 2 minutes, that consume no attempt. This only applies on paths with no existing retry; the Gemini image path already backs off.
  - `hard` is a usage, session, spend or quota limit, a 401/403, or a credentials failure. It consumes no attempt and adds no conservative charge; only real costs are charged. A failed Veo submit is free, but a poll failure after a successful submit is charged the clip cost.
  - Refusals that don't come from a vendor keep today's behaviour exactly.
- **Failure scope:**
  - A shot-specific failure (likeness refused on every rung, retry ceiling hit, non-retryable seed defect) lets sibling shots finish. The run then halts with one report listing every failure in `failed_shots`.
  - A systemic failure (vendor limit, budget exhausted, auth) stops new launches. In-flight shots are never cancelled, because a submitted Veo operation bills anyway.
- **Resume:** retry ceilings and structured failure inspection keep working unchanged with parallelism, hooks and critics in place. Resume skips every persisted shot and keeps each shot's ladder state.
- **Determinism boundary:** preflight, voice (TTS, STT, alignment, subtitle segmentation) and delivery get no skills and no LLM judgment.
- **Tracing** is optional and set only through `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL`. With no keys, the run behaves exactly as it does today.
  - It fails open, and the flush at exit has a bounded timeout.
  - It never records secrets (ADC tokens, API keys, Langfuse keys) or media bytes; paths and URIs stand in for media.
  - Text longer than `LANGFUSE_MAX_CHARS` (default 20000) is truncated.
- **Skill hygiene:** each `name` is lowercase with hyphens, at most 64 characters, and contains neither "claude" nor "anthropic". Each description is at most 1024 characters and says what the skill does and when to use it. `SKILL.md` is at most 500 lines. Reference files sit one level deep and are cited from `SKILL.md`.
- `MAX_VEO_SHOTS=3` still applies. `cover_agent` is out of scope, apart from not breaking it.

## Technical Decisions

- **Stage model:** each stage is its own top-level `query()`, and Python sequences the stages. Nothing delegates through the Agent tool, because CLI agent delegation drops `output_format`.
- **Agent options stay fixed:** `setting_sources=[]`, `strict_mcp_config=True`, `permission_mode="dontAsk"`, `output_format`, the model from settings, and in-process SDK MCP tool servers.
- **Skills plugin:** one local plugin, named `book-reels`, at `orchestrator/skills_plugin/`. Skills are loaded per agent from an explicit list:

  | Agent | Skills |
  |---|---|
  | asset_analyst | asset-analysis |
  | story_agent | story-craft, source-fidelity |
  | visual_agent | visual-planning, source-fidelity |
  | veo_agent | veo-production, visual-qa, character-likeness-qa |
  | stills_agent | still-production, visual-qa, character-likeness-qa |

  - The plugin path is absolute, built from `__file__`, because the working directory changes per project.
  - `tools` gains bare `Skill` and `Read`.
  - `allowed_tools` gets only the plugin-scoped `Read` rule. Bare `Skill` is never added there, because `skills=[...]` injects its own `Skill(name)` rules.
  - No developer tooling may leak in: no `CLAUDE.md`, `.claude/skills` or user settings. The code asserts `setting_sources == []`, checks the skill list in the init message, and halts on any skill that isn't expected.
  - The likeness prompt ladder stays deterministic code; skills hold QA criteria only.
- **SDK spike first:** the answers to FR4 (a)–(g) in `docs/implementation-artifacts/spike-4-1-sdk-skills.md` decide:
  - the skill name format;
  - how `Read` is scoped, with a `PreToolUse` path check as the fallback;
  - init message keys;
  - the `PostToolUse` `tool_response` shape;
  - whether concurrent `query()` calls are safe.
- **Hooks enforce the budget:**
  - A `PreToolUse` hook bound to each query denies a paid MCP call when the paid cost it has already allowed in that query, plus this call, would exceed the limit. The limit is the run's remaining budget, and becomes the calling attempt's reservation from Story 4.3 on. After a denial, the orchestrator halts with `budget_exhausted`.
  - A `PostToolUse` hook parses the failure contracts that paid tools return as JSON (they never raise).
  - One classifier handles all four refusal routes: SDK exceptions, `ResultMessage.is_error`, tool failure contracts and Veo `SDK_ERROR`.
  - TTS and STT keep their existing orchestrator-side budget check.
- **Reservations:** the new setting `CLAUDE_SHOT_ATTEMPT_CAP_USD` sets each shot query's `max_budget_usd`. It replaces today's near-whole-run budget.
  - A reservation is the cap plus that attempt's external costs: image + Veo for a Veo shot, image for a still. From Story 4.5 on, it also includes one reviewer call.
  - A crash is charged the attempt's reservation.
  - Every retry re-acquires its reservation. A retry that can't reserve is a `budget_exhausted` halt, never a demotion.
  - Demotion from Veo to still happens only in a pre-launch planning step, lowest-ranked first. That step replaces the mid-loop rewrite of `visual_plan.json`.
- **Concurrency:**
  - Blocking vendor handler bodies run through `asyncio.to_thread`.
  - Per-vendor `threading.Semaphore` caps sit around each vendor call: `VEO_CONCURRENCY=3`, `GEMINI_IMAGE_CONCURRENCY=2`, `CODEX_IMAGE_CONCURRENCY=1`.
  - The image-pacing global is guarded by a lock, and nothing changes the cwd during production.
  - One orchestrator-owned lock serialises production-asset upserts, manifest saves and ledger updates. `session_id` becomes last-writer-wins.
- **Critics:**
  - Critics are separate Python-sequenced `query()` calls, never subagents.
  - The story critic runs through a new optional `review_before_persist` callback that fires only after a fresh attempt validates, never inside `validate_contract` and never on the resume-skip path. It returns `StoryCritiqueContract` (APPROVE or REVISE). REVISE counts toward the retry ceiling.
  - The visual-QA reviewer returns `VisualQAVerdictContract`. A rejection is written to the attempt record as `structured_output.failure` with stage `seed_qa`, `clip_qa` or `still_qa`.
  - A likeness rejection uses `failure_code` `CHARACTER_LIKENESS`, so it advances the Veo ladder rung. Stills have no ladder and simply retry.
  - `ProductionAsset` gains `reviewer_summary`.
  - New settings: `CLAUDE_REVIEWER_CAP_USD`, `REVIEWER_MODEL` and `CRITIC_MODEL`. Both models default to the Claude model.
- **Tracing:**
  - Dependencies: `langfuse>=4,<5` plus OpenInference's `ClaudeAgentSDKInstrumentor`. Instrument before the agents are imported, because each agent binds `query` at import time.
  - One trace per run opens right after `start_run`. Its session id is `<project>:<run_id>`.
  - Each stage is a span. The system prompt and the loaded skill names are added as our own span attributes, because the instrumentation doesn't capture them.
  - Vendor spans and deterministic-stage spans are created by hand, and must nest under their own shot even across threads.
  - `flush()` runs in a thread that is joined with a timeout.
- **Failure report:** `FailureReport` gains `reason_code` (`vendor_limit`, `budget_exhausted`, `retry_ceiling`, `validation`, …), `resumable` and `failed_shots`.
- **Environment:** use the `kayak-video` conda env by absolute python path; `conda run` doesn't work. There is no `pyproject.toml`, so new dependencies are documented in `README.md`.
- **Spine amendments** (each story updates the spine itself):
  - AD-2 is corrected to "each stage is its own top-level query()".
  - AD-5 and AD-6: a vendor limit consumes no attempt and ends in a resumable halt.
  - AD-7: the hook is the enforcement point, with per-attempt reservations.
  - AD-15(c): demotion happens only before launch.
  - New decisions: AD-16 (skills plugin), AD-17 (parallel shots, out of Deferred), AD-18 (independent reviewer) and AD-19 (observability).

## Cross-Story Dependencies

- Each story depends only on earlier ones: 4.1 → 4.2 → 4.3 → 4.4 → 4.5 → 4.6.
- **The 4.1 spike report decides what later stories can do:**
  - the skill name format and the `Read` scoping, used by the critics and reviewer in 4.5;
  - the `PostToolUse` response shape, used by the 4.2 hook;
  - whether concurrent `query()` calls are safe, needed by 4.4.
- **4.3 builds on the 4.2 budget guard:** it moves the guard's limit from the run's remaining budget to the attempt's reservation.
- **4.3 must land before 4.4 turns on parallelism:** it adds reservations, offloading, semaphores and the lock, but shots still run one at a time.
- **4.4 reuses 4.2's failure fields:** it combines per-shot halts into one `failed_shots` report, using 4.2's `reason_code`/`resumable` to tell systemic failures from shot-specific ones.
- **4.5 builds on 4.3 and 4.4:** it adds reviewer and critic costs to the 4.3 reservations and story budget, and runs the reviewer inside the concurrent shot task from 4.4, before the upsert.
- **4.6 traces everything before it:**
  - stages, including `story_critic` from 4.5;
  - the hooks from 4.2, which must still fire alongside the instrumentor's own hooks;
  - concurrent, offloaded shots from 4.3 and 4.4;
  - the halt fields `reason_code`, `resumable` and `failed_shots`.
