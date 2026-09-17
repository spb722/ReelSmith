# Epic 1 Context: Foundational Orchestrator Kernel + Autonomous Pre-Production

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Take 6-10 user-provided screenshots and autonomously produce a validated narration, an 8-shot visual/generation-mode plan, a voice track, word-timing, and subtitle cues — with no manual per-stage execution or review loop. This epic builds the orchestrator kernel every later stage reuses: contract validation, bounded self-correcting retry, hard-halt-plus-failure-report, per-run budget enforcement, credential/environment preflight, and resume-from-last-valid-stage. It is proven here on the cheaper, lower-risk stages (Gemini text/TTS/STT, deterministic segmentation) before the expensive Veo/video-generation stage (Epic 2) relies on the same kernel behavior.

## Stories

- Story 1.1: Orchestrator scaffold, credential preflight & budget ceiling
- Story 1.2: Autonomous screenshot understanding + single-stage resume
- Story 1.3: Autonomous narration writing, review & revision
- Story 1.4: Autonomous visual & shot-mode planning
- Story 1.5: Autonomous voice, word-timing & subtitle cues + full-chain resume

## Requirements & Constraints

- The pipeline must run with no manual per-stage execution or verification; screenshots go in, validated pre-production artifacts come out.
- Screenshot/image understanding, narration writing/review/revision, and visual/shot planning must be performed by Claude agent reasoning directly over the input — never proxied through a Gemini text or vision call.
- Narration audio (TTS), word-timing alignment (STT), and subtitle cue segmentation stay on their existing tools; subtitle segmentation is deterministic (dynamic programming over word timestamps, punctuation, pause length, protected phrases, semantic ending penalties, min/max words per cue) and must never be re-judged or replaced by an LLM.
- Every stage's output must pass pydantic schema validation before the next stage consumes it. Generative/creative outputs (narration, visual plan) additionally require the producing agent's own semantic/quality judgment — schema validity alone is never "approved." Deterministic-tool outputs instead clear their existing mechanical gate (e.g. word-timing alignment ratio) and are never re-judged by an LLM.
- A failing stage retries by self-correcting, bounded by an iteration ceiling (default 4 for generate→review→revise loops, 3 for transient-failure retries) — no unbounded loops; exact per-agent ceilings beyond this default are deferred pending real-run evidence.
- Exhausting the ceiling halts the run at that stage and writes a diagnostic failure report (stage id, failed contract name, attempt count, timestamp, partial-artifact paths — the shared minimal envelope every agent uses) to the run's output location. No substitute asset, no live prompt, no external notification/paging — the operator discovers a halt only by later checking the report.
- Every run enforces a configurable budget ceiling (`max_budget_usd`, the SDK's only enforced hard stop); exhausting it halts through the same failure-report path. Every paid tool call checks remaining budget synchronously immediately before executing, and the ceiling is also checked before the run starts (an already-exhausted budget blocks preflight from passing).
- Google Cloud ADC/Vertex auth and required GCS bucket access must be verified before the first stage of any invocation — fresh or resumed — runs; this preflight is plain orchestrator code, not agent judgment, and is never skipped just because a resumed run isn't literally starting at stage 1.
- A resumed run must not re-run any stage that already has a validated persisted contract; the resume point is derived from which contracts exist on disk (plus the persisted SDK `session_id`), never tracked as a separate counter.
- The 14 existing pipeline scripts' logic relevant to this epic (ingest, image understanding, story director, TTS, word-timing, subtitle DP segmentation) must be refactored into in-process Python functions (`@tool` + `create_sdk_mcp_server`), never invoked as subprocesses.
- `claude-agent-sdk` (0.2.152) must be installed in the `kayak-video` conda env (Python 3.11) as a first implementation step; `google-genai` (2.23.0 as actually installed — not the stale 2.19.0 figure in older docs) is never upgraded reflexively, only after diagnosing an actual traceback.

## Technical Decisions

- **Directory layout:** `orchestrator/run.py` (top-level query, session/budget/iteration state, failure-report writer; also where `voice_agent`'s TTS→STT→DP pipeline lives as `run_voice_pipeline`/`run_voice_stage` closures — it has no Claude/Agent-SDK session at all, so unlike the other three stages it has no `AgentDefinition` file of its own) · `orchestrator/agents/` (`AgentDefinition`s for this epic's Claude-session stages: `asset_analyst`, `story_agent`, `visual_agent`) · `orchestrator/tools/` (`@tool`-wrapped Gemini/STT/deterministic calls; `gemini_tools.py` exposes only generation/editing/TTS, never an "understand" tool) · `orchestrator/contracts/` (pydantic models) · `orchestrator/state/` (`run_manifest.py`: budget spent, iteration counts, session id — resume pointer is *derived* from contracts, never separately tracked).
- Each pipeline responsibility is its own scoped `AgentDefinition`; the orchestrator delegates via the Agent tool and never performs a stage's reasoning itself. The one exception is preflight, which runs as plain code since it involves no judgment. An agent reaches Gemini/Veo/STT or deterministic compute only through a named tool behind this boundary — never directly.
- Contracts are named `<Artifact>Contract` (e.g. `AnalyzedAssetsContract`, `FinalStoryPlanContract`, `VisualPlanContract`), one model per artifact shape (not per owning agent), evolved by adding fields only, never changing an existing field's type/meaning.
- One `create_sdk_mcp_server` per `tools/*.py` file, server name = module name (predictable `mcp__<server>__<tool>` naming, e.g. `veo_tools.py` → server `veo`).
- Per-script duplicated config (`PROJECT_ID`, region, model name) is centralized into one settings module every tool reads; ADC auth threads through that same module rather than being re-initialized per tool.
- `visual_agent` alone assigns/changes each shot's generation mode (still vs. Veo) in `VisualPlanContract`; this is binding for Epic 2's `veo_agent`/`stills_agent`, which only generate for the mode already assigned. `VisualPlanContract` contains no Remotion timeline schema and no final asset paths at this stage — those are separate artifacts (`timeline.json`, `ProductionAssetsContract`) owned by later stages.
- Orchestrator invocation is `python -m orchestrator.run <source_images_dir>`, a CLI entrypoint run from the repo root inside `kayak-video` — matches existing project convention (flagged as an assumption in source material, pending confirmation during implementation).
- Standalone `review_story_plan.py`, `revise_story_plan.py`, `review_revised_story_plan.py` are explicitly left unmigrated/untouched (kept for manual single-stage debugging, out of the automated chain).
- Stack: `claude-agent-sdk` 0.2.152 (not yet installed), `pydantic` 2.13.4 (already present as a `google-genai` transitive dependency), `google-genai` 2.23.0 as actually installed, Python 3.11 via `kayak-video`.
- Locked-artifact protection over narration/subtitle cues/shot timing only takes effect once a human has recorded approval (Epic 3's concern); before that point, an agent's own retry/revise loop within this epic may freely revise its own draft — that in-run self-correction is not a violation.

## Cross-Story Dependencies

- Story 1.1's kernel behaviors (preflight, budget ceiling, retry/halt machinery, tool-wrapping pattern) are reused as-is by every later story in this epic and by Epic 2 — they are not reimplemented per stage.
- Story 1.2 (`asset_analyst` → `AnalyzedAssetsContract`) must complete before Story 1.3 (`story_agent`, requires the analyzed-assets contract), which must complete before Story 1.4 (`visual_agent`, requires `FinalStoryPlanContract`), which must complete before Story 1.5 (`voice_agent`, requires both `FinalStoryPlanContract` and `VisualPlanContract`).
- Story 1.5 also establishes full-chain resume across Stories 1.2-1.5: a halt at any point must resume only from the first stage lacking a validated contract, not restart from screenshots.
- This epic's outputs (`VisualPlanContract`, `SubtitleCuesContract`, `FinalStoryPlanContract`, narration audio) are the direct inputs Epic 2 depends on (Veo/stills generation, and the `timeline.json` converter). Epic 3's locked-artifact/approval boundary later governs the narration/subtitle-cues/shot-timing artifacts this epic produces, but that enforcement is out of scope here.
