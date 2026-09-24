---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: ['docs/architecture/architecture-book_reels-2026-09-15/ARCHITECTURE-SPINE.md', 'docs/book_reels_architecture.md', 'docs/book_reels_manual_interventions_and_automation_map.md', 'docs/book_reels_troubleshooting_agent_playbook.md', 'docs/vendor-limit-fix.md', 'docs/architecture/agent-sdk-target.html', 'AGENTS.md']
---

# book_reels — Claude Agent SDK Multi-Agent Design — Epic Breakdown

## Overview

This document breaks the move of book_reels to a proper Claude Agent SDK multi-agent design into one epic and its stories. There is no PRD. The requirements come from the design decisions agreed on 2026-09-24 (target diagram: `docs/architecture/agent-sdk-target.html`), constrained by the existing architecture spine (`ARCHITECTURE-SPINE.md`, AD-1..AD-15), which Epics 1–3 already implemented. Epics 1–3 are complete; their breakdown is in git history.

**Design stance:** Python keeps sequencing the pipeline; each LLM stage stays its own `query()` with a JSON-schema contract; agent know-how moves into skills; only independent work runs in parallel; hooks enforce budget and vendor-limit rules; fresh-context critics are added only where they don't break structured output; every run is traced in Langfuse.

## Requirements Inventory

### Functional Requirements

FR1: Each LLM agent's domain know-how (asset analysis, story craft, visual planning, Veo prompting, still prompting, character-likeness ladder, visual QA, house style) lives in `SKILL.md` files with bundled reference files, packaged in one local plugin folder in the repo. The agent definition keeps only its role, tool scope, boundaries and the contract it must return.
FR2: Each stage's `query()` loads only the skills that agent needs, from an explicit per-agent list. Shared skills (character-likeness, visual-qa, house-style) exist once and are referenced by several agents.
FR3: Pipeline agents load no developer tooling: not the repo `CLAUDE.md`, not `.claude/skills/` (bmad-*, lossless-teacher), and no user or project settings.
  - Skills are verified at runtime from the SDK init message's skill/plugin lists.
  - CLAUDE.md exclusion is guaranteed structurally, by asserting `setting_sources == []` on every pipeline agent's options. The SDK documents that this excludes CLAUDE.md (`claude_agent_sdk/types.py:2225-2227`).
  - The spike proves it once, with `ClaudeSDKClient.get_context_usage()` (`memoryFiles`); `query()` doesn't expose that.
FR4: Before any skill migration, a spike on the installed SDK (claude-agent-sdk 0.2.152) records a yes/no answer, with evidence, for each of:
  (a) plugin skills load while `setting_sources=[]`;
  (b) `ClaudeAgentOptions.skills=[...]` restricts the skills available to a top-level `query()`, and the exact skill name format (bare or `book-reels:<skill>`);
  (c) `structured_output` still comes back when the same query also delegates to a subagent through the Agent tool (informational only; no story depends on it);
  (d) several `query()` calls run concurrently in one process without interfering, including when they share one module-level SDK MCP server instance;
  (e) a skill's reference files can be read with `Read` limited to the plugin folder under `permission_mode="dontAsk"`, trying `allowed_tools` rules such as `Read(//abs/path/**)`, with and without `add_dirs=[plugin]` (the plugin folder is outside the agent's cwd);
  (f) the init message's keys that list the loaded skills and plugins, and zero `memoryFiles` from `get_context_usage()` when `setting_sources=[]`;
  (g) the shape of `tool_response` that a `PostToolUse` hook receives for an in-process SDK MCP tool.
  Later stories choose their path from this report.
FR5: After the voice stage and the orchestrator's `generation_mode` assignment (AD-15), Veo shots and still shots are produced concurrently rather than one after another, capped by per-vendor concurrency limits set in config.
FR6: The orchestrator merges concurrent shot results into `production_assets.json` through its keyed upsert, with merges serialised so no update is lost (AD-10). Run-manifest writes (budget spent, iteration counts) are serialised the same way.
FR7: Every shot attempt runs inside a reservation drawn from the run budget.
  - A new setting `CLAUDE_SHOT_ATTEMPT_CAP_USD` (env-overridable) caps Claude spend per shot attempt. Today there is no per-attempt Claude cap: each query gets `max_budget_usd = remaining_budget - reserved_external_cost` (`run.py:1186,1501`), which is nearly the whole run budget.
  - An attempt's reservation is the Claude cap plus that attempt's external costs. The query's `max_budget_usd` is the Claude cap, never the run's remaining budget.
  - Actual Claude cost is debited as reported. The exception path charges the attempt's reservation, not `remaining_budget` (`run.py:1199,1510`).
  - Before launch, Veo shots that can't be reserved are demoted to stills (AD-15(c)). If even the still reservations can't fit, the run halts before any paid call.
  - Each retry attempt re-acquires its reservation. A retry that can't reserve fails the shot with `budget_exhausted`, which is systemic and resumable. There is no demotion mid-run.
  - Total spend never exceeds the ceiling, except by the overshoot of a single Claude turn beyond its cap. The CLI checks `--max-budget-usd` between turns, so that overshoot is also possible today. An overshoot that drives the unreserved pool negative is a systemic `budget_exhausted`.
FR8: A shot failure is classified by scope.
  - A **shot-specific** failure (likeness refused on every ladder rung, QA or retry ceiling exhausted, a non-retryable seed defect) doesn't stop independent sibling shots. All remaining shots run to completion and are persisted. Then the run halts with **one** AD-6 failure report that lists every failed shot in a new additive `failed_shots` field.
  - A **systemic** failure (vendor limit, budget exhausted, auth/credentials) stops new shot launches. In-flight shots finish and are persisted. Then the run halts.
  - In-flight shots are never cancelled, because a submitted Veo operation bills even if polling stops.
  - A resumed run skips every persisted shot (AD-14).
FR9: A `PreToolUse` hook bound to each agent query denies a paid MCP tool call made by an agent (the image or Veo tools) when the paid costs it has already allowed in that query, plus this call's cost, would exceed the limit. The limit is the run's remaining budget, and becomes the calling shot's reservation once reservations exist. The denial follows AD-7's halt path. TTS and STT are called directly by the orchestrator (`run.py:651`, `:677`), not inside an agent query, so they keep the existing `_check_budget` gate.
FR10: A vendor refusal is classified before any attempt or conservative cost is charged. It can arrive by four routes:
  - a Claude SDK exception (`except` sites `run.py:218,1190,1505`);
  - a `ResultMessage` with `is_error` (`run.py:239,1225,1536`), classified by `api_error_status` (429 → transient; 401/403 → hard) plus the text of `result`/`errors`. This is how 4 `visual_agent` attempts burned in 6 seconds;
  - a paid tool's failure contract, returned as normal JSON text by handlers that never raise (`gemini_tools.py:1026-1055,1107-1128`, `veo_tools.py:595-633`) and parsed by a `PostToolUse` hook from `failure.reason`/`failure.code`. `PostToolUseFailure` covers only unexpected handler crashes;
  - Veo's `SDK_ERROR retryable=True` from submit or poll (`veo_tools.py:503-510`), which today charges the clip cost and consumes an attempt.

  How each class is handled:
  - **Transient** refusals (HTTP 429, "rate limit") get a bounded in-process backoff retry, at most 3 retries in about 2 minutes, which consumes no attempt. This applies only on paths with no existing retry: the Gemini image path already backs off with `QUOTA_RETRY_SECONDS`.
  - **Hard** refusals (usage limit, session limit, spend limit, quota, credentials/unauthorized), and transient refusals still failing after backoff, consume no attempt. They are charged the Claude cost reported, plus the configured cost of every paid tool call the `PostToolUse` hook saw succeed in that query. A Veo poll failure after a successful submit is charged the clip cost; only a failed Veo submit is free. The run halts with a failure report marked `reason: vendor_limit` and resumable, and re-invoking the run resumes (AD-14).
  - The run never waits live for a human or for a limit reset (AD-6).
FR11: Before the story stage's output is accepted, the draft narration is reviewed by a fresh-context story critic: a separate critic `query()` sequenced by Python (evaluator-optimizer in code), which sees only the draft, the analyzed assets and the story-craft and source-fidelity skills. A REVISE verdict feeds its issues into the story agent's next attempt, within AD-5's ceiling. A subagent-inside-the-query variant is not used, because a Python-sequenced critic guarantees a fresh context and a structured verdict whatever FR4(c) finds.
FR12: The QA verdict on a generated Veo seed/clip or still comes from a fresh-context visual-QA reviewer, not from the agent that generated the asset. The reviewer is a separate Python-sequenced `query()` using the visual-qa and character-likeness-qa skills.
  - An asset counts as approved only when both the producer and the reviewer approve.
  - A reviewer rejection is written into that attempt's record as `structured_output.failure`, with `stage` `seed_qa`, `clip_qa` or `still_qa`, so resume and ladder logic see it.
  - It is handled like a producer QA failure. For Veo only, a likeness-related `seed_qa` rejection advances the ladder rung, via `_forces_simpler_character_wording` (`run.py:887`) and `SEED_QA_CHARACTER_MARKERS` (`run.py:875`). Stills have no ladder state.
  - The Veo producer makes the seed and the clip in one session (`veo_agent.py:31-68`), so a reviewer seed rejection arrives after the clip is paid for. This is an accepted, documented cost, because the producer's own seed QA still gates the clip.
FR13: When Langfuse credentials are present, every run creates one Langfuse trace in the manually created Langfuse Cloud project.
  - The trace opens right after `start_run` (`run.py:1781`), so it wraps settings, preflight and `run_stages`.
  - Traces are grouped into a session per project name and run id.
  - Each stage is a span.
  - Claude turns capture the system prompt, the input, the output text, tool calls, token usage and cost. The system prompt is added by our own span attribute, because the instrumentation doesn't capture it.
FR14: Each vendor tool call is a span recording:
  - the provider (`codex` or `gemini`) and the model;
  - the final prompt exactly as sent, after the likeness-ladder rung is applied, and the rung itself;
  - input and output file paths and the GCS URI;
  - Veo parameters: model, duration, aspect ratio;
  - any `VeoFailureContract` or RAI result;
  - the cost charged.
FR15: Deterministic stages are spans too:
  - preflight: its result;
  - TTS: input text, voice and model;
  - STT: result summary and alignment ratio;
  - subtitle cues: the count;
  - delivery: the timeline path, the Remotion render command and the output path.
FR16: Tracing is configured only by environment variables: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL`. With no keys, tracing is off and the run behaves exactly as it does today.

### NonFunctional Requirements

NFR1: Behaviour parity. After the skills migration, a run on an existing project produces contracts that pass validation, and the regression harness passes. Contract schemas change only by adding fields.
NFR2: Tracing fails open. If Langfuse is down or misconfigured, the run never fails and is never materially slowed. The flush at exit has a bounded timeout.
NFR3: No secrets in traces: no ADC tokens, API keys or Langfuse keys. No media bytes: no base64 or raw image, video or audio, only paths and URIs. Long text is truncated at a configured limit.
NFR4: The per-run budget ceiling (AD-7) holds under concurrency, within FR7's single-turn overshoot allowance. For the same set of completed shots, parallelism must not raise the spend of a run compared with running it sequentially.
NFR5: No test regresses. The baseline on 2026-09-24 is 565 passed, 5 failed, 12 errors, 5 skipped, measured with `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q`.
  - The 12 errors in `test_timeline_converter.py` come from a fixture that reads the working tree's `metadata/` (`KeyError: 'visual_concept'`).
  - The 5 known failures are 2 in `test_alignment_recovery`, 1 in `test_regression_harness`, 1 in `test_visual_agent` and 1 in `test_voice_agent`.
  - Each story must leave that set no worse, and add its own unit tests that make no live vendor calls. Tests mock the SDK by monkeypatching the module-level `query` with an async generator yielding `ResultMessage`, as in `orchestrator/tests/test_veo_agent.py:58-71`.
NFR6: Skill hygiene:
  - `name` is lowercase with hyphens, at most 64 characters, and doesn't contain "claude" or "anthropic";
  - `description` is at most 1024 characters and says what the skill does and when to use it;
  - `SKILL.md` is at most 500 lines;
  - reference files sit one level deep and are cited from `SKILL.md`.
NFR7: The determinism boundary is preserved. Preflight, voice (TTS, STT, alignment, subtitle segmentation) and delivery get no skills and no LLM judgment (AD-1, AD-4).
NFR8: Resume (AD-14), retry ceilings (AD-5) and structured failure inspection (AD-12) keep working unchanged with parallelism, hooks and critics in place.

### Additional Requirements

- **Spine amendment.** Epic 4 changes or un-defers these spine decisions, so the spine must be amended alongside:
  - AD-2's diagram says "delegates via Agent tool". In fact each stage is its own top-level `query()`, because CLI `--agent` delegation drops `output_format` (`orchestrator/agents/asset_analyst.py:43-52`).
  - "Parallel stage execution" moves out of Deferred.
  - The hooks become AD-7's single enforcement point.
  - Vendor-limit classification amends AD-5 and AD-6.
  - New decisions are needed for the skills plugin and for observability.
- **Constraints the stories must respect:** AD-1, AD-4, AD-5, AD-6 (never wait live, so "pause" means a halt that can be resumed), AD-7, AD-9, AD-10, AD-11, AD-12, AD-13, AD-14 and AD-15, including `MAX_VEO_SHOTS=3` (`orchestrator/run.py:119`).
- **How the agents are built today:**
  - Every agent builds `ClaudeAgentOptions` with `setting_sources=[]`, `strict_mcp_config=True`, `permission_mode="dontAsk"`, `output_format` (JSON schema from the pydantic contract), a per-attempt `max_budget_usd`, and a model from `settings.claude_model`.
  - Tools are in-process SDK MCP servers (`gemini`, `veo`, `deterministic`, `timeline_converter`).
  - Retries go through `run_bounded_agent_stage` (`orchestrator/run.py:144-257`); the Veo and stills loops run per shot.
- **Skills need tool access to load their reference files.** The agents that currently have no tools (story, visual) must gain `Skill` and a `Read` limited to the plugin folder. How to scope that is settled by the spike (FR4(e)).
- **`cover_agent`** (a separate CLI with 3 turns and no tools) keeps its inline prompt. It's out of scope apart from not breaking.
- **Every vendor call blocks the event loop today.** Tool handlers are `async def`, but underneath they block:
  - Veo: sync `client.models.generate_videos`, `operations.get` and `time.sleep` polling (`veo_tools.py:487,532,539`);
  - Gemini image: sync `generate_content` and `time.sleep` (`gemini_tools.py:616,627,646`);
  - Codex: a blocking `subprocess.run` (`codex_image_tools.py:303`).
  So `asyncio.gather` alone gives no concurrency. Blocking calls must be offloaded (`asyncio.to_thread`, or the async client).
- **Shared mutable state:**
  - `_pace_image_call` mutates the module global `_last_image_call_monotonic` (`gemini_tools.py:503-512`);
  - `progress._log_file` and `_run_start` are globals;
  - `enter_project` changes the process-wide cwd (`workspace.py:96-100`).
  Codex uses an isolated temporary workspace per call (`codex_image_tools.py:266`).
- **Demotion handoff.** `_demote_veo_shot_to_still` (`run.py:516-530`) rewrites `visual_plan.json` in the middle of the Veo loop (`run.py:1161-1175`), and `run_stills_stage` re-reads the plan from disk. Running the two concurrently breaks this handoff.
- **The likeness ladder is deterministic code, not prompt text.** It's built by `build_prompt_ladder` (`gemini_tools.py:547-578`), run by `run_image_edit_ladder` (`:799`), and its start rung comes from `_prior_character_rejections` (`run.py:902`). Skills hold only the QA criteria; the ladder stays in code.
- **Existing 429 retries:** only the Gemini image path backs off (`QUOTA_RETRY_SECONDS`, `gemini_tools.py:616-627`). Veo's `retryable=True` (`veo_tools.py:297+`) is not a retry. It hands the failure back to the orchestrator, which spends an attempt, so FR10 route 4 does apply to Veo `SDK_ERROR`.
- **Installed SDK 0.2.152:**
  - `ClaudeAgentOptions` has `skills`, `plugins`, `agents`, `hooks`, `add_dirs`, `setting_sources`, `output_format` and `max_budget_usd`.
  - `HookEvent` includes `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `Stop` and `PermissionRequest`.
- **No per-attempt Claude cap exists today.** Shot queries get `max_budget_usd = remaining_budget - reserved_external_cost` (`run.py:1186,1501`). The exception path charges `remaining_budget` (`run.py:1199,1510`). In the bounded stage, a plain exception costs nothing and consumes the attempt (`run.py:218-220`).
- **`tools` and `allowed_tools` must differ once skills are added.**
  - `tools` (`--tools`) needs bare `Skill`/`Read`.
  - Path rules like `Read(//abs/**)` go only in `allowed_tools`.
  - Don't add bare `Skill` to `allowed_tools`. The SDK deprecates it, and `skills=[...]` already injects `Skill(name)` rules (`subprocess_cli.py:549-555`, `types.py:1962`).
  - The `plugins` path must be absolute and built from `__file__`, because `enter_project` changes the cwd.
- **More blocking calls** inside the paid tools: the ffmpeg preview `subprocess.run` (`veo_tools.py:231`) and the GCS `download_to_filename` (`veo_tools.py:145`). `progress.log` is already thread-safe (`progress.py:29,72-77`).
- **The OpenInference instrumentation won't take effect by default.** `openinference-instrumentation-claude-agent-sdk` (Python, 0.1.18) patches `claude_agent_sdk.query`, but every agent module binds `query` at import time (e.g. `veo_agent.py:8`), before `main()` runs. The instrumentation captures the prompt input, result, tokens, `total_cost_usd` and tool spans (through injected PreToolUse/PostToolUse hooks it merges with ours). It does not capture the system prompt. `LANGFUSE_BASE_URL` is the current env var; `LANGFUSE_HOST` is the legacy one. Langfuse's `flush()` takes no timeout.
- **The critic must not run inside `validate_contract`.** That callback also runs on the resume-skip path (`run.py:174-176`). The bounded stage charges only `result.total_cost_usd`.
- **Environment:**
  - Use the `kayak-video` conda env, invoking its python by absolute path; `conda run` doesn't work here.
  - There's no `pyproject.toml` or `requirements.txt`. New dependencies (`langfuse>=4,<5`, OpenInference's Claude Agent SDK instrumentation) are installed into the env and documented in `README.md`.
- **Langfuse setup:**
  - The Cloud project is created once by hand, in the UI (Option A); no project is auto-created.
  - The base URL is either `https://cloud.langfuse.com` (EU) or `https://us.cloud.langfuse.com` (US).
  - Claude turns are traced automatically by OpenInference's `ClaudeAgentSDKInstrumentor` (per Langfuse's Claude Agent SDK integration). Vendor spans and deterministic-stage spans are added by hand with the Langfuse SDK.

### UX Design Requirements

None. This epic has no user interface.

### FR Coverage Map

FR1: Epic 4 (Story 4.1) - Agent know-how moves into SKILL.md + reference files in one plugin
FR2: Epic 4 (Story 4.1) - Per-agent skill lists; shared skills exist once
FR3: Epic 4 (Story 4.1) - No dev tooling leaks into pipeline agents
FR4: Epic 4 (Story 4.1) - SDK spike (a)-(g) with a recorded report
FR5: Epic 4 (Story 4.4) - Veo and still shots run concurrently under per-vendor caps
FR6: Epic 4 (Stories 4.3, 4.4) - Serialized keyed-upsert merges, manifest writes and budget ledger
FR7: Epic 4 (Story 4.3) - Per-attempt Claude cap and reservations; pre-launch demotion
FR8: Epic 4 (Story 4.4) - Shot-specific vs systemic failure handling; one combined report
FR9: Epic 4 (Stories 4.2, 4.3) - Cumulative PreToolUse budget guard; reservation-scoped in 4.3
FR10: Epic 4 (Story 4.2) - Vendor-limit classification on all four routes, transient backoff, resumable halt
FR11: Epic 4 (Story 4.5) - Fresh-context story critic
FR12: Epic 4 (Story 4.5) - Fresh-context visual-QA reviewer
FR13: Epic 4 (Story 4.6) - One Langfuse trace per run with Claude turn capture
FR14: Epic 4 (Story 4.6) - Vendor tool spans (provider, final prompt, rung, paths, cost)
FR15: Epic 4 (Story 4.6) - Deterministic stage spans
FR16: Epic 4 (Story 4.6) - Env-var-only configuration; off without keys

## Epic List

### Epic 4: Book Reels runs as a skill-based, parallel, guarded and observable Claude Agent SDK pipeline

A reel runs end to end with each agent's know-how in maintainable skills, shots produced in parallel inside an unbreakable budget, vendor limits stopping cleanly without burning attempts, generated work checked by a fresh reviewer, and every step — prompt, output, vendor call, provider — visible in Langfuse.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR8, FR9, FR10, FR11, FR12, FR13, FR14, FR15, FR16

Story order (each depends only on earlier ones): 4.1 Skills plugin + SDK spike → 4.2 Guard hooks → 4.3 Concurrency-safe shot plumbing + per-attempt reservations (still sequential) → 4.4 Concurrent shot launch + failure scoping → 4.5 Critic / visual-QA reviewer → 4.6 Langfuse tracing.

## Epic 4: Book Reels runs as a skill-based, parallel, guarded and observable Claude Agent SDK pipeline

A reel runs end to end with each agent's know-how in maintainable skills, shots produced in parallel inside a reserved budget, vendor limits stopping cleanly without burning attempts, generated work checked by a fresh reviewer, and every step — prompt, output, vendor call, provider — visible in Langfuse. Python keeps sequencing the pipeline; every LLM stage stays its own `query()` with an `output_format` contract (AD-2 as practised, AD-4).

### Story 4.1: Agent Know-How as Skills in a Pipeline Plugin (with SDK Spike)

As a book_reels maintainer,
I want each agent's domain know-how moved out of inline Python strings into skills packaged in one pipeline plugin, loaded per agent,
So that rules shared between agents exist once, prompts are maintainable, and pipeline agents never pick up developer tooling.

**Acceptance Criteria:**

**Given** the installed `claude-agent-sdk` 0.2.152 in the `kayak-video` env (`/opt/homebrew/anaconda3/envs/kayak-video/bin/python`)
**When** the spike runs (a throwaway script, no production code changes, only small Claude calls against a minimal test plugin)
**Then** `docs/implementation-artifacts/spike-4-1-sdk-skills.md` records yes/no, with evidence (init-message excerpts, exception text, `get_context_usage()` output), for each of FR4 (a)–(g)
**And** if (a) is "no", the report records which proven fallback the story uses (skills folder via `add_dirs` with only the `"project"` source, or a dedicated `cwd`); if (e) finds no working `Read` restriction, the fallback is a `PreToolUse` path check that denies `Read` outside the plugin folder

**Given** the spike results
**When** the plugin is created at `orchestrator/skills_plugin/` (with `.claude-plugin/plugin.json`, plugin name `book-reels`)
**Then** it contains exactly these skills, each a `SKILL.md` with reference files one level deep:
  - `asset-analysis`, from `agents/asset_analyst.py:18-35`;
  - `story-craft`, from `agents/story_agent.py:20-107`: voice, hook, 90–115 words, scenes with verbatim impact_text, and the 9-dimension self-review rubric;
  - `visual-planning`, from `agents/visual_agent.py:21-142`: shots are scenes, planning each shot, video nominations, overall style, and the 5-dimension self-review;
  - `source-fidelity`: the fidelity and uncertainty rules repeated across story and visual;
  - `veo-production`, from `agents/veo_agent.py:23-86` minus the shared QA blocks;
  - `still-production`, from `agents/stills_agent.py:22-70` minus the shared QA blocks;
  - `visual-qa`: the shared reject list (app UI, visible text, extra or duplicate subjects, photorealistic drift);
  - `character-likeness-qa`: the ~16-line block duplicated in `veo_agent.py:45-65` and `stills_agent.py:42-60`
**And** a unit test parses every `SKILL.md` frontmatter and checks NFR6: the name format, a description of at most 1024 characters stating what and when, and a `SKILL.md` of at most 500 lines
**And** the likeness prompt ladder (`build_prompt_ladder`, `gemini_tools.py:547-578`) stays in code; skills carry QA criteria only

**Given** each agent module
**When** its `ClaudeAgentOptions` is built (`asset_analyst.py:64`, `story_agent.py:132`, `visual_agent.py:171`, `veo_agent.py:113`, `stills_agent.py:97`)
**Then** the inline prompt shrinks to its role, boundaries and the contract it must return, plus an instruction to use its skills
**And** `plugins` points at the plugin through an absolute path built from `__file__`, since `enter_project` changes the cwd (`workspace.py:96-100`)
**And** `skills` lists only that agent's skills, in the name format the spike proved:

  | Agent | Skills |
  |---|---|
  | asset_analyst | asset-analysis |
  | story_agent | story-craft, source-fidelity |
  | visual_agent | visual-planning, source-fidelity |
  | veo_agent | veo-production, visual-qa, character-likeness-qa |
  | stills_agent | still-production, visual-qa, character-likeness-qa |

**And** `tools` and `allowed_tools` become two separate lists:
  - `tools` (`--tools`) gains bare `Skill` and `Read`. Story and visual agents, which have no tools today, gain only these. `asset_analyst` keeps its existing `Read`, which it needs for the screenshots, and gains `Skill`.
  - `allowed_tools` gains the plugin-scoped `Read` rule the spike proved. Bare `Skill` is never added to `allowed_tools`, because `skills=[...]` injects `Skill(name)` rules itself.
**And** `setting_sources=[]`, `strict_mcp_config=True`, `permission_mode="dontAsk"`, `output_format` and `max_budget_usd` stay unchanged, and `cover_agent` is untouched

**Given** a pipeline agent query is being built or started
**When** its options are constructed, and when its init `SystemMessage` arrives
**Then** the code asserts `setting_sources == []`, which structurally excludes CLAUDE.md (FR3)
**And** the orchestrator logs the loaded skill and plugin names from the init keys the spike identified, and halts the stage with a failure report if any skill isn't in that agent's list (for example `bmad-*` or `lossless-teacher`)
**And** if spike (f) shows that the init message lists every discovered skill rather than the filtered set, the check changes: every listed skill must come from the `book-reels` plugin, with none sourced from `.claude/skills`. The per-agent restriction is then proven only by spike result (b).

**Given** the migrated agents
**When** the asset_analyst, story and visual stages run live once on an existing project (a Claude-only run, capped by `MAX_BUDGET_USD`)
**Then** their outputs validate unchanged against `AnalyzedAssetsContract`, `FinalStoryPlanContract` and `VisualPlanContract` (NFR1), and the run log records the skills each stage loaded
**And** unit tests assert each agent's options carry exactly its skill list, the absolute plugin path, and the split `tools`/`allowed_tools`, with the suite no worse than the NFR5 baseline

**Given** the spine
**When** this story completes
**Then** `ARCHITECTURE-SPINE.md` gains AD-16: agent know-how lives in skills in one pipeline plugin, loaded per agent, never from `.claude/`
**And** AD-2's diagram and wording are corrected to "each stage is its own top-level `query()`; the orchestrator sequences stages in code", citing the `asset_analyst.py:43-52` rationale

### Story 4.2: Guard Hooks — Budget Guard and Vendor-Limit Classification

As a book_reels operator,
I want paid tool calls blocked when the budget can't cover them and vendor refusals recognised as "not the reel's fault",
So that a run never overspends inside an agent's own loop and a vendor limit never burns attempts or charges money for work that never ran.

**Acceptance Criteria:**

**Given** a Veo or stills agent query
**When** the agent calls a paid MCP tool (`mcp__gemini__generate_veo_seed`, `mcp__gemini__generate_still`, `mcp__veo__generate_veo_clip`)
**Then** a `PreToolUse` hook bound to that query checks: the paid cost it has already allowed in this query, plus this tool's configured cost (`settings.image_call_cost_usd`, `settings.veo_call_cost_usd`; Codex is `0.0`), against the run's remaining budget (`MAX_BUDGET_USD` minus `RunManifest.budget_spent_usd`)
**And** when the total wouldn't be covered, the hook denies the call with `hookSpecificOutput.permissionDecision="deny"` and a `permissionDecisionReason`, so an agent can't bypass the check by calling `generate_veo_seed` twice
**And** a denial is recorded on the hook's closure, and after the query returns the orchestrator halts through `_halt` (`run.py:125-141`) with reason code `budget_exhausted`, never letting the agent's own output mask it (AD-7, FR9)

**Given** `orchestrator/run.py`
**When** vendor classification is added
**Then** one classifier, next to `LIKENESS_REJECTION_CODES` (`run.py:856`) and following `docs/vendor-limit-fix.md` §1, takes the error text and an optional HTTP status. It returns:
  - `transient` for 429 or "rate limit";
  - `hard` for usage limit, session limit, monthly spend limit, quota, 401/403, or credentials/unauthorized;
  - `none` otherwise.
**And** unit tests cover each marker, each status and non-matching text

**Given** any of the four vendor-refusal routes in FR10
**When** a refusal is found
**Then** each route is classified before charging:
  - the `except` sites: `run.py:218` (bounded stage), `:1190` (Veo), `:1505` (stills);
  - the `ResultMessage.is_error` paths: `run.py:239`, `:1225`, `:1536`, using `api_error_status` plus the text of `result` and `errors`;
  - paid-tool failure contracts: a `PostToolUse` hook parses the JSON text in `tool_response` for `failure.reason` and `failure.code`, in the shape the spike recorded as FR4(g). `PostToolUseFailure` covers only unexpected handler crashes;
  - Veo `SDK_ERROR`: the reason from submit and poll (`veo_tools.py:503-510`) is classified, and a hard submit failure charges no clip cost.
**And** a `hard` result gives back the pre-incremented attempt (`manifest.iteration_counts[stage_id]` decremented and saved) and charges no conservative cost. It is charged the Claude cost reported, plus the configured cost of every paid tool call the `PostToolUse` hook saw succeed in this query. A Veo poll failure after a successful submit is charged the clip cost; only a failed Veo submit is free. The run halts with reason code `vendor_limit`, whatever outcome contract the agent returned. This covers the case of 4 `visual_agent` attempts in 6 seconds, and the Codex usage limit that burned 2 of shot 3's 3 attempts.
**And** a refusal that isn't from a vendor keeps today's behaviour exactly:
  - a bounded-stage exception costs nothing and consumes the attempt (`run.py:218-220`);
  - the Veo and stills loops apply their conservative charge (`run.py:1199-1200`, `1510-1511`).

**Given** a `transient` classification
**When** it arises on a path with no existing retry (the Gemini image path already backs off with `QUOTA_RETRY_SECONDS`)
**Then** the orchestrator retries the same attempt at most 3 times with backoff, within about 2 minutes, without consuming an attempt, and escalates to the `hard` path if it still fails
**And** a transient retry consumes no attempt but is charged the costs it actually incurs, including a redrawn seed, because rerunning a Veo attempt starts a new agent session
**And** the story's notes record which client paths already retry 429s (Anthropic SDK/CLI, google-genai, Codex CLI), so retries are never doubled

**Given** any halt
**When** the failure report is written
**Then** `FailureReport` (`state/run_manifest.py:172-184`) gains the additive fields `reason_code` (`vendor_limit`, `budget_exhausted`, `retry_ceiling`, `validation`, …) and `resumable: bool` (true for `vendor_limit` and `budget_exhausted`), both with defaults
**And** for `budget_exhausted`, "resumable" means "resumable after raising `MAX_BUDGET_USD`", because spend accumulates across runs in the manifest. The halt message says so.
**And** a test proves `FailureReport(**old_dict)` built from a pre-change report dict still validates
**And** re-invoking after a `vendor_limit` halt resumes from that stage (AD-14), re-runs preflight (AD-13), and never waits live (AD-6)

**Given** the change
**When** tests run with a mocked `query`
**Then** new unit tests cover:
  - the cumulative budget denial and its halt;
  - each exception site and each `is_error` path on the hard path;
  - the tool failure-contract flag;
  - Veo `SDK_ERROR` classification;
  - transient backoff escalation;
  - report back-compat.
  The suite ends no worse than the NFR5 baseline.
**And** `ARCHITECTURE-SPINE.md` amends AD-5/AD-6 (a vendor limit consumes no attempt and ends in a resumable halt) and AD-7 (the `PreToolUse` hook is the pre-call enforcement point)
**And** `docs/vendor-limit-fix.md` is marked as superseded by this story

### Story 4.3: Concurrency-Safe Shot Plumbing and Per-Attempt Budget Reservations

As a book_reels operator,
I want every shot attempt to run inside its own reserved budget and the vendor tools made safe to run side by side, while shots still run one at a time,
So that parallel production (Story 4.4) can be switched on without any budget hole or shared-state race, and this change is verifiable on its own.

**Acceptance Criteria:**

**Given** settings
**When** this story is implemented
**Then** new env-overridable settings exist:
  - `CLAUDE_SHOT_ATTEMPT_CAP_USD`;
  - per-vendor caps: `VEO_CONCURRENCY` (default 3), `GEMINI_IMAGE_CONCURRENCY` (default 2), `CODEX_IMAGE_CONCURRENCY` (default 1).
**And** each shot attempt's reservation is `CLAUDE_SHOT_ATTEMPT_CAP_USD` plus that attempt's external costs:
  - a Veo attempt reserves `image_call_cost_usd` + `veo_call_cost_usd`;
  - a still attempt reserves `image_call_cost_usd`.
  This matches today's `reserved_external_cost` (`run.py:1121`, `1454`).
**And** each shot query's `max_budget_usd` becomes the Claude cap, replacing `remaining_budget - reserved_external_cost` (`run.py:1186`, `1501`)

**Given** voice timing is backfilled and `_promote_video_candidates` (`run.py:472`) has assigned `generation_mode` (AD-15)
**When** shot production begins
**Then** a new production-planning step computes every pending shot's first-attempt reservation before any shot starts
**And** it demotes the lowest-ranked Veo shots to stills (AD-15(c)) until all reservations fit the remaining budget
**And** it performs every `visual_plan.json` demotion write at this point, replacing the mid-loop `_demote_veo_shot_to_still` handoff (`run.py:516-530`, `1161-1175`)
**And** if even the still reservations can't fit, the run halts with `budget_exhausted` before any paid call (AD-7)

**Given** a shot attempt
**When** it starts, finishes or fails
**Then** it acquires its reservation from a run-level budget ledger, owned by the orchestrator, before the query starts. Every retry attempt re-acquires its own reservation.
**And** a retry that can't reserve fails the shot with `budget_exhausted`. That is systemic and resumable, and there is no mid-run demotion.
**And** the actual Claude cost is debited as reported. A crash is charged the attempt's reservation, never `remaining_budget` (`run.py:1199`, `1510`). Unused reservation is released.
**And** an overshoot beyond the reservation comes from the unreserved pool, and a negative pool is a systemic `budget_exhausted` (FR7)
**And** the Story 4.2 `PreToolUse` guard now checks the calling attempt's unspent reservation, not the whole run's remaining budget

**Given** the paid tool handlers `generate_veo_seed`, `generate_still` and `generate_veo_clip`
**When** they run
**Then** each handler's whole synchronous body runs through `asyncio.to_thread`, which carries contextvars across. That covers:
  - Veo `generate_videos`, `operations.get` and `time.sleep` polling (`veo_tools.py:487,532,539`);
  - the GCS `download_to_filename` (`veo_tools.py:145`);
  - the ffmpeg preview `subprocess.run` (`veo_tools.py:231`);
  - Gemini `generate_content` and `time.sleep` (`gemini_tools.py:616,627,646`);
  - Codex `subprocess.run` (`codex_image_tools.py:303`).
**And** the per-vendor caps are `threading.Semaphore`s acquired around the vendor call inside the offloaded code
**And** `_pace_image_call`'s module global (`gemini_tools.py:503-512`) is guarded by a lock, and nothing changes the working directory during production. `progress.log` is already thread-safe (`progress.py:29,72-77`).

**Given** shared writes
**When** a shot result is upserted, the manifest is saved, or the ledger changes
**Then** `upsert_production_asset` (`state/production_assets.py:21`), run-manifest saves and ledger updates all go through one orchestrator-owned lock (AD-10: single writer, keyed upsert)
**And** `manifest.session_id` is documented as last-writer-wins once shots run concurrently

**Given** shots still run sequentially in this story
**When** the full shot stage runs on mocked tools
**Then** its behaviour is unchanged apart from the new budget model:
  - provenance stamping stays as it is (`_stamp_veo_outcome` `run.py:1008`, `_stamp_still_outcome` `:954`);
  - per-shot retry ceilings stay as they are;
  - resume still skips persisted shots (AD-14).
**And** tests prove:
  - reservation arithmetic;
  - deterministic pre-launch demotion by rank;
  - a retry that can't reserve ends in `budget_exhausted`;
  - crash charging;
  - the reservation-scoped guard;
  - handler offloading, shown by the event loop staying responsive while a fake blocking tool sleeps;
  - lock serialisation of concurrent upserts;
  - per-vendor semaphore caps never exceeded, tested by calling the offloaded handlers concurrently even though shots are still sequential.
  The suite ends no worse than the NFR5 baseline.
**And** `ARCHITECTURE-SPINE.md` amends AD-7 (per-attempt reservations) and AD-15(c): demotion is decided only before launch, and a retry that can't reserve is a `budget_exhausted` halt, not a demotion

### Story 4.4: Concurrent Shot Launch and Failure Scoping

As a book_reels operator,
I want independent Veo and still shots to run at the same time, with one shot's failure never throwing away the others' work,
So that a reel finishes much faster and a resumed run redoes only what actually failed.

**Acceptance Criteria:**

**Given** Story 4.3's planning step has fixed each shot's mode and reservation
**When** production runs
**Then** one production step replaces the sequential `run_veo_stage` → `run_stills_stage` pair in `run_stages` (`run.py:1823-1851`)
**And** that step launches the Veo and still shot tasks concurrently with `asyncio`, under a shot-level cap and Story 4.3's per-vendor caps

**Given** one shot fails
**When** the failure is shot-specific (likeness refused on every ladder rung, attempt ceiling exhausted, or a non-retryable seed defect, as classified by the existing `LIKENESS_REJECTION_CODES` / `VEO_SEED_ONLY_FAILURE_STAGES` logic)
**Then** all the other shots continue to completion and are persisted. The run then halts with **one** failure report, whose new additive `failed_shots` field lists every failed shot with its stage and reason. Today, each shot calls `_halt` on its own.
**And** when the failure is systemic (`vendor_limit`, `budget_exhausted`, auth), no new shots start. Shots already running finish and are persisted, then the run halts. In-flight shots are never cancelled, because a submitted Veo operation bills even if polling stops (FR8).

**Given** a halted parallel run
**When** it is re-invoked
**Then** every shot already in `production_assets.json` is skipped (AD-14), and only the failed or unstarted shots run again, with their ladder state (`_prior_character_rejections`, `run.py:902`) intact

**Given** the change
**When** tests run with fake tools that sleep
**Then** tests prove:
  - shots overlap in wall-clock time;
  - the per-vendor and shot-level caps are never exceeded;
  - concurrent upserts lose no entry;
  - total charges stay within the FR7 allowance;
  - shot-specific and systemic failure behaviour;
  - one combined `failed_shots` report;
  - a resume redoes only the failed shots.
  The suite ends no worse than the NFR5 baseline.
**And** `ARCHITECTURE-SPINE.md` moves "Parallel stage execution" out of Deferred into a new AD-17: independent shots run concurrently under per-attempt reservations and per-vendor caps; the orchestrator's lock serialises AD-10 writes; failure scope decides whether siblings continue
**And** it amends AD-6 and AD-15 so that a shot-specific hard failure defers the run's halt until sibling shots finish

### Story 4.5: Fresh-Context Critic and Visual-QA Reviewer

As a book_reels operator,
I want narration and every generated asset judged by a reviewer that didn't produce it,
So that quality approval isn't the generator grading its own work.

**Acceptance Criteria:**

**Given** `run_bounded_agent_stage` (`run.py:144-257`)
**When** this story is implemented
**Then** it gains an optional `review_before_persist` callback. The callback:
  - runs only after a **fresh** attempt validates, never on the resume-skip path (`run.py:174-176`, where `validate_contract` also runs);
  - returns APPROVE, or REVISE with feedback;
  - has its cost added to `manifest.budget_spent_usd`.

**Given** the story agent returns a schema-valid `FinalStoryPlanContract` (its own `QualityReview` is still enforced, `contracts/final_story_plan.py:112-133`)
**When** `review_before_persist` runs for the story stage
**Then** Python runs a separate story-critic `query()`:
  - its own `AgentDefinition` in `orchestrator/agents/`;
  - skills story-craft and source-fidelity;
  - tools limited to `Skill` plus the plugin-scoped `Read`, and no write tools;
  - input: only the draft plan and `analyzed_assets.json`;
  - output via `output_format`: a new `StoryCritiqueContract` with `verdict: APPROVE | REVISE` and `issues[]`, each giving a dimension, evidence and a fix.
**And** REVISE feeds the issues into the story agent's next attempt through the existing feedback path, counting toward the AD-5 ceiling. Exhausting the ceiling halts with `retry_ceiling`, and only APPROVE lets the plan persist.

**Given** a Veo or still producer returns an approved outcome (`approved` and `qa_summary`; `contracts/veo.py:63-64`, `contracts/stills.py:43-59`)
**When** the orchestrator receives it inside the shot task
**Then** Python runs a separate visual-QA reviewer `query()`:
  - skills visual-qa and character-likeness-qa;
  - `Read` limited to that shot's asset paths and the character reference. For a Veo clip it reviews the preview frames `generated/veo/previews/shot_NN_MM.jpg` and the seed;
  - output: a new `VisualQAVerdictContract` with `approved`, `failure_stage` (`seed_qa` | `clip_qa` | `still_qa`), `failure_code`, `reasons[]` and `summary`.
**And** a rejection is written into that attempt's record as `structured_output.failure` with its `stage`, so resume and the ladder logic see it. It follows the producer-QA-failure path for that attempt, and the attempt counts toward the shot's ceiling.
**And** for Veo only, a likeness-related `seed_qa` rejection advances the ladder rung, via `_forces_simpler_character_wording` (`run.py:887`) and `SEED_QA_CHARACTER_MARKERS` (`run.py:875`). `LIKENESS_REJECTION_CODES` are Veo API codes and aren't used for reviewer verdicts. Stills have no ladder state and simply retry.
**And** a likeness rejection from the reviewer uses `failure_code` `CHARACTER_LIKENESS`, which matches `SEED_QA_CHARACTER_MARKERS`, so `_prior_character_rejections` (`run.py:902`) sees it on resume
**And** on Veo, the reviewer runs before `upsert_production_asset`, which changes today's order of success then upsert (`run.py:1276-1292`)
**And** the story notes record the accepted cost: a reviewer's seed rejection arrives after the clip is already paid for, because the Veo producer makes the seed and the clip in one session (`veo_agent.py:31-68`)

**Given** an asset approved by both the producer and the reviewer
**When** it is upserted
**Then** `ProductionAsset` (`contracts/production_assets.py:31,40`) still requires `approved: True`, and gains an additive `reviewer_summary` field recorded next to the producer's `qa_summary`

**Given** critic and reviewer calls cost money
**When** reservations and budgets are computed
**Then** new settings exist: `CLAUDE_REVIEWER_CAP_USD`, `REVIEWER_MODEL` and `CRITIC_MODEL`. Both models default to `settings.claude_model`, and all three are env-overridable.
**And** each shot attempt's reservation (Story 4.3) and each story attempt's budget include one reviewer or critic call at that cap. The reviewer query's `max_budget_usd` is `CLAUDE_REVIEWER_CAP_USD`.
**And** the story agent's `call_agent` budget (today `settings.max_budget_usd - manifest.budget_spent_usd`, `run.py:216`) becomes the remaining budget minus `CLAUDE_REVIEWER_CAP_USD`. The critic runs only if at least that cap remains, and runs with `max_budget_usd = CLAUDE_REVIEWER_CAP_USD`.
**And** the story notes state the expected added spend per run: (1 + story retries) × critic cap, plus the number of shot attempts × reviewer cap

**Given** the change
**When** tests run with a mocked `query`
**Then** tests cover:
  - the critic's APPROVE/REVISE loop;
  - no critic call on the resume-skip path;
  - critic cost being charged;
  - reviewer approve and reject;
  - the Veo rung advance on a reviewer likeness rejection, and none for stills;
  - the rejection recorded in the attempt file;
  - the reviewer included in the reservation;
  - `reviewer_summary` persistence.
  The suite ends no worse than the NFR5 baseline.
**And** `ARCHITECTURE-SPINE.md` gains AD-18, refining AD-4: approving generative output requires an independent fresh-context reviewer sequenced in code; the producer's self-QA is necessary but not sufficient

### Story 4.6: Langfuse Tracing for Every Run

As a book_reels operator,
I want every run traced in my Langfuse Cloud project — each stage's prompt and output, every vendor call's input, provider and result,
So that I can see exactly what happened at each step of a run without reading log files.

**Acceptance Criteria:**

**Given** a Book Reels project created once in the Langfuse UI, and `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` set (`https://cloud.langfuse.com` for EU or `https://us.cloud.langfuse.com` for US; `LANGFUSE_HOST` is legacy)
**When** `python -m orchestrator.run --project <name>` runs
**Then** one trace is created per run. It opens right after `start_run` (`run.py:1781`) and wraps settings, preflight and `run_stages`.
**And** its session id is `<project>:<run_id>`, where the run id is the `run_<YYYYmmddTHHMMSS>` stamp from `progress.start_run` (`progress.py:32-41`), and it is tagged with the project name
**And** it is flushed at exit, including on halt and on exception, with a bounded timeout: Langfuse's `flush()` runs in a thread that is joined with a timeout, because `flush()` itself takes none
**And** with none of those variables set, no tracing code initialises and the run behaves exactly as it does today (FR16)

**Given** OpenInference's `openinference-instrumentation-claude-agent-sdk` (Python; ≥0.1.18) together with `langfuse>=4,<5`
**When** instrumentation is set up
**Then** `ClaudeAgentSDKInstrumentor().instrument()` runs **before** `orchestrator.agents` is imported, or the agents call `claude_agent_sdk.query(...)` through the module. Either way the patched function is the one used, because each agent binds `query` at import time today (e.g. `veo_agent.py:8`).
**And** a test proves Claude turns produce spans. The existing tests still monkeypatch the module-level `query`.
**And** it is verified that Langfuse's default span-export filter exports the OpenInference instrumentation scope; if it doesn't, a `should_export_span` filter that allows it is configured
**And** the instrumentor's injected PreToolUse/PostToolUse hooks and Story 4.2's hooks both still fire when merged

**Given** each stage
**When** it runs
**Then** it appears as a span named after the stage: preflight, asset_analyst, story_agent, story_critic, visual_agent, voice, production, delivery
**And** each Claude `query()` under it shows the user prompt, the output text or `structured_output`, tool calls, token usage and `total_cost_usd`, as captured by the instrumentation
**And** our own span attributes add the system prompt and the loaded skill names, because the instrumentation doesn't capture them. This is required.

**Given** a vendor tool call inside a shot
**When** it executes
**Then** a span under that shot's span records (FR14):
  - the provider (`codex` or `gemini`) and the model;
  - the final prompt exactly as sent after the ladder rung, and the rung;
  - input and output paths, and the GCS URI;
  - Veo model, duration and aspect ratio;
  - any `VeoFailureContract` or RAI result;
  - the cost charged.
**And** these spans nest under their own shot even with Story 4.4's concurrency and Story 4.3's `asyncio.to_thread` offloading. The shot span is current when that shot's `query()` starts. If tool-handler spans don't inherit it, the shot span's context is propagated explicitly. A test with two concurrent shots proves the nesting.

**Given** the deterministic stages
**When** they run
**Then** spans record (FR15):
  - the preflight result;
  - TTS input text, voice and model (`generate_narration_audio`, `run.py:651`);
  - the STT summary and alignment ratio (`extract_word_timing`, `run.py:677`);
  - the subtitle cue count;
  - the timeline path, the Remotion render command and the output path (`render_remotion`, `run.py:1731`).
**And** a halted run's span carries the failure report's `reason_code`, `resumable` and `failed_shots`

**Given** any traced value
**When** it is exported
**Then** no secrets are included: no ADC tokens, API keys or Langfuse keys (NFR3)
**And** media bytes and base64 are replaced by paths or URIs
**And** text longer than `LANGFUSE_MAX_CHARS` (default 20000) is truncated
**And** a Langfuse outage, wrong keys or a network error never fails or materially slows a run: every tracing call fails open (NFR2)

**Given** the change
**When** tests run with an in-memory OpenTelemetry exporter (no network)
**Then** tests prove:
  - the span tree shape for a mocked run, from preflight through delivery;
  - that the instrumentation takes effect;
  - the system-prompt attribute;
  - vendor span attributes, including provider and final prompt;
  - correct nesting across two concurrent shots;
  - redaction and truncation;
  - a no-op when keys are absent;
  - fail-open behaviour when export raises;
  - the bounded flush.
  The suite ends no worse than the NFR5 baseline.
**And** `README.md` documents the three environment variables, creating the Langfuse project and keys once in the UI, and the install command for the new dependencies into the `kayak-video` env
**And** `ARCHITECTURE-SPINE.md` gains AD-19: observability is opt-in via env, fails open, and never records secrets or media bytes
