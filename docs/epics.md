---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: ['docs/architecture/architecture-book_reels-2026-09-15/ARCHITECTURE-SPINE.md', 'docs/book_reels_architecture.md', 'docs/book_reels_manual_interventions_and_automation_map.md', 'docs/book_reels_troubleshooting_agent_playbook.md', 'AGENTS.md']
---

# book_reels — Claude Agent SDK Orchestration — Epic Breakdown

## Overview

This document decomposes `docs/architecture/architecture-book_reels-2026-09-15/ARCHITECTURE-SPINE.md` (no PRD exists for this project; the architecture spine and the existing pipeline docs are the requirements source) into implementable epics and stories for the new Claude Agent SDK orchestration layer over the existing book_reels pipeline.

## Requirements Inventory

### Functional Requirements

FR1: The system ingests 6-10 user-provided screenshots and autonomously produces a finished rendered vertical video, with no manual per-stage execution or verification required.
FR2: Screenshot/image understanding is performed by Claude agent reasoning, not a Gemini call (AD-1).
FR3: Reel narration is written, self-reviewed, and revised in a loop by Claude agent reasoning, not proxied through Gemini (AD-1, AD-2 — `story_agent`).
FR4: The 8-shot visual/shot plan, including each shot's generation mode (still vs. Veo), is produced by Claude agent reasoning (AD-1, AD-2 — `visual_agent`, AD-15).
FR5: Narration audio (Gemini TTS), word-timing alignment (Google STT/Chirp), and subtitle cues (existing deterministic DP segmentation) are produced via their respective tools; subtitle segmentation is never re-judged by an LLM (AD-1, AD-4).
FR6: Veo video clips and Gemini-recomposed stills are generated only for the shots `visual_agent` assigned to each mode, and Veo's generation call only ever accepts a recomposed seed image — never a raw source screenshot (AD-9).
FR7: Every stage's output is validated against a pydantic schema contract before the next stage may consume it (AD-4).
FR8: For generative/creative outputs specifically, the producing agent performs its own semantic/quality judgment (visual QA, tone-match to narration, duration/length targets) in addition to schema validation before the output counts as approved (AD-4).
FR9: A stage that fails validation returns control to the producing agent to self-correct and retry, bounded by an iteration ceiling (AD-5).
FR10: When an agent exhausts its retry ceiling, the run halts at that stage and writes a diagnostic failure report (what failed, what was attempted, partial artifacts, in a shared minimal envelope) to the run's output location — no substitution, no live prompt (AD-6).
FR11: The orchestrator verifies Google Cloud ADC/Vertex auth and required GCS bucket access before executing the first stage of any invocation, fresh or resumed (AD-13).
FR12: A halted run, when re-invoked, resumes from the last stage with a validated persisted contract rather than restarting from the raw screenshots (AD-14).
FR13: Per-shot contract/state files are written via keyed upsert (merge by shot id), with the orchestrator as sole writer-of-record — never a full-file overwrite by an individual agent (AD-10).
FR14: Production asset selection for a shot is read from a single named `ProductionAssetsContract` — never inferred from bucket listing, filename, or recency (AD-11).
FR15: The Veo tool inspects the full `operation.response`/`operation.result` object and surfaces any RAI-filter rejection reason as a structured `VeoFailureContract` rather than a bare success/fail (AD-12).
FR16: Validated final assets are synced into `remotion/public/` and the existing Remotion render is triggered to produce the final video (Structural Seed; existing pipeline's final step).
FR17: The existing 14 root pipeline scripts are refactored into in-process Python tool functions (`@tool` + `create_sdk_mcp_server`) under `orchestrator/tools/`, not invoked as subprocesses (AD-3).
FR18: Once a human records approval at the final-review checkpoint (or an explicit reject/regenerate signal), that reel's locked artifacts (narration, subtitle cues, shot timing) may not be rewritten by a later run without explicit user direction; before that point, in-run agent self-correction is not a violation (AD-8).
FR19: The Remotion renderer (`timeline.ts`/`BookReel.tsx`) is data-driven — it consumes `timeline.json` + `subtitle_cues.json` directly and requires zero hand-authored code edits per reel (today a coding agent hand-writes these per reel from pasted prompts; without this, FR1's "no manual per-stage execution" cannot actually be achieved). `timeline.json`'s schema is derived from the orchestrator's existing contracts (visual plan, subtitle cues, production-asset manifest) — never invented independently of them, so Epic 3's integration stays genuinely thin rather than becoming late-discovered adapter work. Video shots structurally enforce `transform: none` — not a fix reapplied per reel, but architecturally impossible to violate (e.g. a shared video-shot component/type that offers no transform prop), since a reapplied fix is exactly how the documented Shot 2 zoom/breathing defect recurred.

**Acceptance** (a one-time regression test against the existing "Backwards Law" reel, proving the new renderer reproduces the approved output — **not** a per-run production gate, since a new reel has no reference to compare against; say so in the test itself so it's never later wired into the live pipeline):
- Duration, fps, resolution, and frame count match the reference MP4 (52.44s, 30fps, 1573 frames) exactly. Frame count = `floor(duration × fps)` — never round up, since a rounded-up final frame has no audio behind it.
- Frame comparison at each of the 24 subtitle-cue centres uses perceptual hash (pHash), not exact pixel equality — encoder output isn't bit-reproducible across runs/machines. Cue centre = `floor(midpoint(cue.start, cue.end) × fps)` (e.g. `cue_005` at 9.92–11.65s → midpoint 10.785s → frame 323). Starting threshold: Hamming distance ≤ 5 of 64 bits, flagged for calibration against first real output.
- Audio is out of scope for frame comparison (`narration.wav` is reused unchanged — no checksum needed), but the render must assert an audio track is present and its duration matches the video's; a silent or truncated render is a cheap-to-catch failure mode.

### NonFunctional Requirements

NFR1: Every run enforces a configurable per-run budget ceiling via the SDK's `max_budget_usd`; exceeding it halts the run through the same failure-report path as FR10 (AD-7).
NFR2: Every validate-and-reiterate loop has an explicit, bounded iteration ceiling (default: 4 for a generate→review→revise loop, 3 for a transient-failure retry) — no unbounded loops (AD-5).
NFR3: The orchestrator and all new agent/tool code run on the existing `kayak-video` conda environment (Python 3.11), satisfying `claude-agent-sdk`'s `>=3.10` requirement, without disrupting the existing environment (AD-3, Stack).
NFR4: `google-genai` is never upgraded reflexively — a version change requires diagnosing an actual traceback first, per existing `AGENTS.md` policy (Stack).
NFR5: Failure reports share one minimal common envelope (stage id, failed contract name, attempt count, timestamp, partial-artifact paths) across every agent, so resume (FR12) can read any agent's halt consistently (AD-6).
NFR6: No orchestrator-level notification/paging occurs on halt — the user checks the failure report asynchronously; this is a deliberate non-requirement, not a gap (Operational Envelope).

### Additional Requirements

- `claude-agent-sdk` (0.2.152) is not yet installed in `kayak-video` — installation is a first implementation step, not assumed present.
- Per-script duplicated config (`PROJECT_ID`, region, model name) across the 14 existing scripts is centralized into one settings module every tool file reads; ADC auth is threaded through that same module, not re-initialized per tool (Consistency Conventions).
- Contracts are named `<Artifact>Contract` (e.g. `VeoSeedContract`, `VeoResultContract`, `ProductionAssetsContract`, `VeoFailureContract`) — one pydantic model per artifact shape, evolved by adding fields only, never changing an existing field's type/meaning.
- One `create_sdk_mcp_server` per `tools/*.py` file, server name = module name, giving predictable `mcp__<server>__<tool>` naming.
- Orchestrator invocation: `python -m orchestrator.run <source_images_dir>` as a CLI entrypoint inside `kayak-video` (spine `[ASSUMPTION]` — confirm during implementation).
- No starter/greenfield template applies — this is a brownfield refactor of an existing, working pipeline, not a new scaffold.
- Explicitly out of scope for this epic breakdown (spine's own Deferred list): multi-reel namespacing, parallel stage execution, the byte-level `remotion/public/` sync format, the failure-report diagnostic payload beyond the minimal envelope, and the fate of the three standalone review/revise debugging scripts (kept as-is, unmigrated).

### UX Design Requirements

Not applicable — this is a backend pipeline orchestration project with no user-facing UI change.

### FR Coverage Map

FR1: Epic 3 — end-to-end autonomous run, the top-level goal
FR2: Epic 1 — asset understanding
FR3: Epic 1 — narration write/review/revise
FR4: Epic 1 — visual/shot planning
FR5: Epic 1 — voice, word-timing, subtitle cues
FR6: Epic 2 — Veo + stills generation
FR7: Epic 1 — schema validation (kernel)
FR8: Epic 1 — semantic QA gate (kernel)
FR9: Epic 1 — bounded retry (kernel)
FR10: Epic 1 — hard-halt + failure report (kernel)
FR11: Epic 1 — credential/env preflight (kernel)
FR12: Epic 1 — resume from last valid stage (kernel)
FR13: Epic 2 — keyed-upsert, single writer-of-record
FR14: Epic 2 — ProductionAssetsContract
FR15: Epic 2 — structured Veo failure inspection
FR16: Epic 3 — remotion sync + render trigger
FR17: Epic 1 (pre-production tools) + Epic 2 (visual-generation tools)
FR18: Epic 3 — locked-artifact/approval boundary
FR19: Epic 2 — data-driven Remotion renderer, structural transform:none enforcement

## Epic List

### Epic 1: Foundational Orchestrator Kernel + Autonomous Pre-Production
Screenshots go in; a validated narration, visual/shot plan, voice track, word-timing, and subtitle cues come out — with no manual review loop. This epic builds the orchestrator kernel every later stage reuses: contract validation, bounded retry, hard-halt + failure report, budget enforcement, credential preflight, and resume-from-last-valid-stage. It's proven here on the cheap, low-risk stages (Gemini text/TTS/STT) before the expensive stage touches it.
**FRs covered:** FR2, FR3, FR4, FR5, FR7, FR8, FR9, FR10, FR11, FR12, FR17 (asset_analyst/story_agent/visual_agent/voice_agent tools)
**NFRs covered:** NFR1, NFR2, NFR3, NFR4, NFR5, NFR6
**Implementation notes:** `claude-agent-sdk` install, config centralization, contract/tool naming conventions, and the CLI-invocation assumption all land here, since this is where `orchestrator/run.py`, `contracts/`, and `state/` are created.

### Epic 2: Autonomous Visual Asset Generation & Data-Driven Rendering
This epic starts with the regression harness, not the renderer rewrite: reconstruct `timeline.json` for the existing reference reel and build the runnable pass/fail check first, so the renderer rewrite that follows has a real signal from minute one instead of a promise made after the fact. That harness is also where `timeline.json`'s schema gets frozen — derived from Epic 1's `VisualPlanContract`/`SubtitleCuesContract`, never invented independently, so Epic 3 stays a genuine integration rather than late adapter-writing. Once the renderer rewrite makes that harness pass and structurally forecloses the transform-on-video-shot defect, Veo clips and Remotion stills get generated and QA'd autonomously, given Epic 1's validated visual plan — including the specific fixes for this pipeline's worst documented failure history (raw-screenshot leakage, per-shot overwrite races, ambiguous production-vs-test assets, opaque Veo failures).
**FRs covered:** FR19 (regression harness + data-driven renderer, structural `transform:none` — sequenced first), then FR6, FR13, FR14, FR15, FR17 (veo_agent/stills_agent tools)

### Epic 3: Full Autonomous Run & Delivery
The complete chain runs unattended from raw screenshots to a finished, rendered video — assets sync into `remotion/public/`, the now-data-driven render triggers, and the locked-artifact/approval boundary (a reel's artifacts lock only once you've actually reviewed and approved it) is enforced for real. Stays a thin integration epic: it wires Epic 1 and Epic 2's already-validated capabilities together end-to-end rather than building new generation logic.
**FRs covered:** FR1, FR16, FR18

## Epic 1: Foundational Orchestrator Kernel + Autonomous Pre-Production

Screenshots go in; a validated narration, visual/shot plan, voice track, word-timing, and subtitle cues come out — with no manual review loop. Builds the orchestrator kernel every later stage reuses, proven on the cheap, low-risk stages before Veo touches it.

### Story 1.1: Orchestrator Scaffold, Credential Preflight & Budget Ceiling

As a book_reels operator,
I want the orchestrator to verify credentials and enforce a budget ceiling before any stage runs,
So that an autonomous run never wastes API spend on a broken environment or runs away in cost with nobody watching.

**Acceptance Criteria:**

**Given** a `kayak-video` conda environment with `claude-agent-sdk` and a centralized settings module installed
**When** `orchestrator/run.py` is invoked via `python -m orchestrator.run <source_images_dir>` with Google Cloud ADC not configured
**Then** the preflight check fails immediately with a diagnostic failure report
**And** no Gemini/Veo/STT API call is made

**Given** ADC is valid but the configured GCS bucket is inaccessible
**When** preflight runs
**Then** it fails the same way, before stage 1 begins

**Given** a run is configured with a budget ceiling (`max_budget_usd`) that is already exhausted
**When** the orchestrator attempts to start
**Then** it halts via the same failure-report path as an exhausted-budget mid-run halt, before any paid tool call executes

**Given** valid credentials, bucket access, and remaining budget
**When** preflight runs
**Then** it passes and stage 1 is allowed to begin

**Given** the run is re-invoked after a prior halt, not fresh
**When** preflight runs
**Then** it still executes before the first stage that will actually run — never skipped because the run isn't literally starting at stage 1 (AD-13)

**Given** the centralized settings module
**When** any tool needs `PROJECT_ID`, region, or a model name
**Then** it reads from that one shared source, never a per-file duplicated literal

**Given** any of the 14 existing scripts' logic (Gemini, Veo, STT, or deterministic)
**When** it is exposed to an agent
**Then** it is refactored into a plain Python function wrapped with `@tool` + `create_sdk_mcp_server` and invoked in-process — never shelled out to as a subprocess (AD-3, FR17)

### Story 1.2: Autonomous Screenshot Understanding + Single-Stage Resume

As a book_reels operator,
I want screenshot understanding to run as a Claude agent with automatic retry, hard-halt, and resume,
So that I don't have to manually invoke `analyze_assets.py` or manually recover from a failed analysis run.

**Acceptance Criteria:**

**Given** 6-10 screenshots in `source_images/`
**When** the orchestrator processes them
**Then** it first calls the deterministic ingest tool (replacing `ingest_assets.py`: file scan + stable content-hash IDs), then passes that inventory to `asset_analyst`

**Given** that inventory
**When** `asset_analyst` runs
**Then** it produces a schema-valid `AnalyzedAssetsContract` using Claude's own vision reasoning directly on the images
**And** no Gemini "understand" tool call is made (AD-1)

**Given** the agent's output fails schema validation on an attempt
**When** it retries
**Then** it self-corrects up to the configured iteration ceiling (default 4) before the loop ends

**Given** the iteration ceiling is exhausted without a valid result
**When** the retry loop ends
**Then** the run halts at this stage and writes a failure report using the shared minimal envelope (stage id, failed contract name, attempt count, timestamp, partial-artifact paths)
**And** no substitute asset is produced and no live prompt blocks the run
**And** no external notification or page is sent — the operator discovers the halt only by checking the failure report whenever they next look (NFR6)

**Given** `asset_analyst` has already produced and persisted a validated `AnalyzedAssetsContract` from a prior invocation
**When** the orchestrator is invoked again for the same reel
**Then** `asset_analyst` is not re-invoked and no new vision call is made
**And** the orchestrator proceeds using the persisted contract

**Given** no persisted `AnalyzedAssetsContract` exists yet
**When** the orchestrator is invoked
**Then** `asset_analyst` runs normally

### Story 1.3: Autonomous Narration Writing, Review & Revision

As a book_reels operator,
I want the reel's narration to be written, reviewed, and revised automatically,
So that I no longer have to manually run `story_director.py`, review, revise, and re-review in sequence.

**Acceptance Criteria:**

**Given** a validated `AnalyzedAssetsContract` exists
**When** `story_agent` runs
**Then** it writes narration, reviews it, and revises it as needed in one Claude-agent-owned loop
**And** it never proxies this reasoning through a Gemini text call (AD-1)

**Given** a narration draft fails `story_agent`'s own quality bar
**When** it iterates
**Then** it self-corrects up to the configured iteration ceiling before the run halts, reusing Story 1.1/1.2's kernel behavior rather than reimplementing it

**Given** narration passes both schema validation and `story_agent`'s own semantic quality judgment
**When** it is persisted
**Then** it is written as a validated `FinalStoryPlanContract`
**And** schema validity alone is never treated as approval (AD-4)

**Given** the existing standalone `review_story_plan.py`, `revise_story_plan.py`, and `review_revised_story_plan.py` scripts
**When** this story is implemented
**Then** they are left unmigrated and untouched, per the spine's Deferred decision

### Story 1.4: Autonomous Visual & Shot-Mode Planning

As a book_reels operator,
I want the 8-shot visual plan and each shot's generation mode to be decided automatically,
So that I don't have to manually run `visual_director.py` or manually decide which shots need Veo vs. a still.

**Acceptance Criteria:**

**Given** a validated `FinalStoryPlanContract` exists
**When** `visual_agent` runs
**Then** it produces a validated `VisualPlanContract` containing the 8-shot plan and an explicit generation mode (still or Veo) assigned to every shot

**Given** `visual_agent`'s mode assignment
**When** Epic 2's `veo_agent`/`stills_agent` later read it
**Then** they only ever generate for the mode `visual_agent` assigned; only `visual_agent` may write or change a shot's mode (AD-15)

**Given** `VisualPlanContract` is produced by this story
**When** it is inspected
**Then** it contains no Remotion timeline schema and no final asset paths — those don't exist yet at this point in the chain; `visual_plan.json` and `timeline.json` are distinct artifacts owned by different stages

### Story 1.5: Autonomous Voice, Word-Timing & Subtitle Cues + Full-Chain Resume

As a book_reels operator,
I want voice generation, word-timing alignment, and subtitle cue segmentation to run automatically and the whole pre-production chain to be resumable,
So that a failure partway through doesn't force me to re-run, and re-pay for, everything from the screenshots again.

**Acceptance Criteria:**

**Given** validated `FinalStoryPlanContract` and `VisualPlanContract` exist
**When** `voice_agent` runs
**Then** it produces narration audio via the existing Gemini TTS tool, word-timing via the existing Google STT/Chirp tool, and subtitle cues via the existing deterministic DP segmentation tool
**And** the segmentation output is validated only by its own mechanical alignment-ratio gate, never re-judged by an LLM (AD-4's carve-out)

**Given** a run halts partway through the pre-production chain, e.g. after Story 1.3's narration succeeds but before Story 1.4's visual plan completes
**When** the run is re-invoked
**Then** it does not re-run `asset_analyst` or `story_agent`; it resumes from `visual_agent`, reading the persisted validated contracts for the earlier stages

**Given** the full chain (Stories 1.2 through 1.5) has already completed successfully for a reel
**When** the run is invoked again
**Then** no pre-production stage re-runs — the orchestrator is ready to proceed to Epic 2

**Given** `voice_agent`'s stage fails validation and exhausts its retry ceiling
**When** the run halts
**Then** the failure report uses the same shared minimal envelope as every other agent's halt (NFR5), so resume logic reads it consistently regardless of which stage failed

## Epic 2: Autonomous Visual Asset Generation & Data-Driven Rendering

Given Epic 1's validated visual plan, Veo clips and Remotion stills get generated and QA'd autonomously. The renderer itself becomes data-driven — proven by a regression harness built first, against the existing reference reel, before the rewrite it verifies.

### Story 2.1: timeline.json Converter & Reference Reel Regression Harness

As a book_reels operator,
I want one deterministic converter that owns `timeline.json`'s shape and a runnable check that proves a data-driven renderer would reproduce the existing approved reel,
So that the renderer rewrite that follows has a real pass/fail signal from day one, and every later stage that needs a `timeline.json` calls the same conversion instead of re-inventing it.

**Acceptance Criteria:**

**Given** Epic 1 ships `visual_plan.json` and `subtitle_cues.json` with zero knowledge of the renderer or `timeline.json`
**When** this story builds the `timeline.json` conversion
**Then** it is a single, deterministic, reusable converter (`visual_plan.json` + `subtitle_cues.json` + a per-shot final asset → `timeline.json`) owned only here — not a one-off script for this regression test, and not logic Epic 3 reinvents later
**And** its schema is derived directly from `VisualPlanContract`'s and `SubtitleCuesContract`'s existing field shapes, never invented independently

**Given** the existing "Backwards Law" reel's locked artifacts (`visual_plan.json`, `subtitle_cues.json`, the 8 shot boundaries, and the actual chosen asset per shot, standing in for a `ProductionAssetsContract` that doesn't exist yet at this point in the build)
**When** the converter runs against this historical data
**Then** it produces a concrete `timeline.json` for this reel
**And** every field needed to render the reel (shot list, timing, mode, final asset path, captions) is present

**Given** the reconstructed `timeline.json`
**When** the regression harness runs
**Then** it checks duration, fps, resolution, and frame count (frame count = `floor(duration × fps)`) against the reference MP4 (52.44s, 30fps, 1573 frames) exactly
**And** it checks perceptual hash (Hamming distance ≤ 5 of 64 bits, flagged for calibration) at each of the 24 subtitle-cue centres (cue centre = `floor(midpoint(cue.start, cue.end) × fps)`)

**Given** the harness runs against the current, still hand-authored renderer, before Story 2.2's rewrite
**When** it evaluates
**Then** it is expected to fail or be inconclusive — that result is itself proof the harness is wired correctly and measuring something real, not evidence of a bug

**Given** the render's audio track
**When** the harness checks it
**Then** it asserts an audio track is present and its duration matches the video's, with no pixel/hash comparison of audio content

**Given** this harness
**When** it is invoked
**Then** it runs as a one-time regression check, explicitly documented as not a per-run production gate — a new reel has no reference to compare against

**Given** Epic 3 later needs a `timeline.json` for a live, newly-generated reel
**When** it does so
**Then** it calls this story's converter with live `VisualPlanContract`/`SubtitleCuesContract`/`ProductionAssetsContract` data — it does not reimplement the conversion

### Story 2.2: Data-Driven Renderer Core + Structural `transform:none` Enforcement

As a book_reels operator,
I want the Remotion renderer to consume `timeline.json` and `subtitle_cues.json` directly,
So that I never again need a coding agent to hand-write `timeline.ts`/`BookReel.tsx` per reel.

**Acceptance Criteria:**

**Given** the `timeline.json` schema frozen by Story 2.1
**When** `timeline.ts`/`BookReel.tsx` is rewritten
**Then** it consumes `timeline.json` + `subtitle_cues.json` directly and requires zero hand-authored code changes to render a new reel

**Given** the rewritten renderer and Story 2.1's harness
**When** the harness runs the reconstructed Backwards Law `timeline.json` through the new renderer
**Then** it passes — the failure/inconclusive verdict from Story 2.1 is now a pass

**Given** a video shot in the new renderer
**When** any transition or animation is applied to it
**Then** `transform` is never applied to a video-layer component, enforced structurally (the video-shot component/type offers no transform prop), not by convention or a comment

**Given** a still shot
**When** it is rendered
**Then** Ken-Burns/transform effects remain available only for stills, never video shots (existing `AGENTS.md` convention, now structurally preserved)

### Story 2.3: Autonomous Veo Generation, Production Asset Manifest & Structured Failure Inspection

As a book_reels operator,
I want Veo clip generation to run autonomously with safe asset selection and clear failure diagnosis,
So that I don't have to manually invoke `generate_veo_clips.py` or manually recover from an opaque Veo failure.

**Acceptance Criteria:**

**Given** a shot assigned Veo mode in `VisualPlanContract`
**When** `veo_agent` requests generation
**Then** it only ever supplies a path produced by the seed-recomposition tool under its own contract
**And** it rejects and fails loud on a `source_images/` path rather than silently substituting one (AD-9)

**Given** a Veo generation call completes
**When** `veo_agent` inspects the result
**Then** it checks both `operation.response` and `operation.result`, distinguishes `video.uri` from `video.video_bytes`, and surfaces any RAI-filter rejection reason as a structured `VeoFailureContract` — never a bare success/fail (AD-12)

**Given** `veo_agent` produces a validated result for a shot
**When** it is recorded
**Then** it is written into `ProductionAssetsContract` via a keyed upsert (merge by shot id), with the orchestrator as sole writer-of-record — `veo_agent` returns its result to the orchestrator rather than writing the shared file directly (AD-10/AD-11)

**Given** two shots are generated in the same run
**When** each completes
**Then** neither shot's entry in `ProductionAssetsContract` is lost or overwritten by the other's write

### Story 2.4: Autonomous Stills Generation

As a book_reels operator,
I want stills generation for the non-Veo shots to run autonomously,
So that I don't have to manually invoke `prepare_remotion_stills.py`.

**Acceptance Criteria:**

**Given** a shot assigned still mode in `VisualPlanContract`
**When** `stills_agent` generates it
**Then** the validated result is written into the same `ProductionAssetsContract` via the orchestrator's single-writer keyed-upsert path established in Story 2.3 — `stills_agent` does not implement its own write path

**Given** a still fails its own visual QA (e.g. a duplicate-subject defect)
**When** `stills_agent` iterates
**Then** it retries up to the configured iteration ceiling before the run halts, reusing Epic 1's kernel behavior

**Given** all shots (Veo and stills) have validated `ProductionAssetsContract` entries
**When** Epic 2 concludes
**Then** every shot in the visual plan has exactly one approved production asset recorded

## Epic 3: Full Autonomous Run & Delivery

The complete chain runs unattended from raw screenshots to a finished, rendered video, and a reel's artifacts lock only once a human has actually reviewed and approved it.

### Story 3.1: Full Autonomous Run — Screenshots to Rendered Video

As a book_reels operator,
I want to provide only the source screenshots and receive a finished rendered video with no manual steps in between,
So that the entire pipeline I used to run by hand, stage by stage, now runs unattended end to end.

**Acceptance Criteria:**

**Given** 6-10 screenshots from a **second, different book** — not the Backwards Law screenshots, which only re-proves Story 2.1's already-established regression case — and no existing pipeline state for this reel
**When** I invoke the orchestrator once
**Then** it runs Epic 1's pre-production chain and then Epic 2's visual asset generation, with no manual per-stage invocation or verification

**Given** this second book's real narration and visual plan
**When** they are produced
**Then** the shot count reflects what `visual_agent` actually planned for this book, not a hardcoded 8, and the narration/video duration reflects the real generated audio, not a rounded target — e.g. if this book yields 6 shots and 71 seconds, the pipeline handles it with no code changes

**Given** Epic 1 and Epic 2 have produced validated `VisualPlanContract`, `SubtitleCuesContract`, and a complete `ProductionAssetsContract` for this second book
**When** the run reaches its final stage
**Then** it calls Story 2.1's converter with this live data to produce a real `timeline.json` for the reel — it does not reimplement the conversion

**Given** a real `timeline.json` and the final audio/subtitle files
**When** the run proceeds
**Then** it syncs the validated final assets into `remotion/public/` and triggers the Remotion render, producing a finished MP4 whose shot count/duration match this book's actual plan, not the Backwards Law reel's

**Given** the run is halted specifically inside Epic 2's Veo generation stage, after some shots' clips have already been produced and recorded in `ProductionAssetsContract`
**When** the run is re-invoked
**Then** it does not regenerate the shots already recorded — it resumes only the remaining shots, proving the case that actually saves Veo generation cost, not just an arbitrary earlier-stage halt

**Given** the run completes successfully
**When** the video is produced
**Then** the operator has done nothing but supply the screenshots (FR1)

### Story 3.2: Locked-Artifact / Approval Boundary

As a book_reels operator,
I want a reel's artifacts to become locked only once I've actually reviewed and approved the finished video,
So that a rendered-but-unreviewed video doesn't block my own next attempt or get treated as if I'd already signed off on it.

**Acceptance Criteria:**

**Given** a video has just finished rendering but no approval has been recorded
**When** the run is re-invoked
**Then** AD-14's resume/self-correction rules still apply — this is "before approval," not a locked-artifact scenario, and no explicit user direction is required to revise it

**Given** the operator explicitly records approval of a finished video, or an explicit reject/regenerate signal
**When** that event is recorded
**Then** the reel's locked artifacts (narration, subtitle cues, shot timing) become protected — a later run may not rewrite them without explicit user direction

**Given** a locked reel
**When** a new run is invoked without explicit user direction to revise it
**Then** the orchestrator refuses to overwrite the locked narration/subtitle cues/shot timing and fails loud rather than silently regenerating them

**Given** an explicit reject/regenerate signal from the operator
**When** a new run is invoked afterward
**Then** it is permitted to revise the previously locked artifacts
