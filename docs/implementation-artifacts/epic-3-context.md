# Epic 3 Context: Full Autonomous Run & Delivery

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Wire Epic 1's pre-production chain and Epic 2's visual-asset generation plus data-driven Remotion renderer into one unattended end-to-end run: screenshots in, finished rendered vertical video out — including syncing validated final assets into `remotion/public/` and triggering the Remotion render. This epic also enforces the locked-artifact / approval boundary for real: a reel's narration, subtitle cues, and shot timing lock only after a human records approval (or an explicit reject/regenerate signal), never merely because a video finished rendering. It stays a thin integration epic — no new generation or conversion logic.

## Stories

- Story 3.1: Full autonomous run — screenshots to rendered video
- Story 3.2: Locked-artifact / approval boundary

## Requirements & Constraints

- A single orchestrator invocation with only source screenshots must run the full Epic 1 → Epic 2 chain unattended and produce a finished MP4 — no manual per-stage execution or verification.
- End-to-end proof must use a second, different book (not the Backwards Law reference reel already covered by Epic 2's regression case), with no pre-existing pipeline state for that reel.
- Shot count and narration/video duration must follow whatever `visual_agent` and the generated audio actually produce for that book — never a hardcoded 8-shot / rounded-duration assumption.
- After validated `VisualPlanContract`, `SubtitleCuesContract`, and a complete `ProductionAssetsContract` exist, the run must call Epic 2's existing `timeline.json` converter with that live data — never reimplement the conversion.
- Validated final assets must sync into `remotion/public/` and trigger the existing Remotion render; the finished MP4's shot count and duration must match this book's actual plan.
- A halt mid-Veo generation (some shots already recorded in `ProductionAssetsContract`) must resume only remaining shots on re-invocation — not regenerate shots already recorded.
- Rendered-but-unapproved video is still "before approval": re-invocation follows resume/self-correction rules; no explicit user direction is required to revise.
- Locked artifacts (narration, subtitle cues, shot timing) become protected only when the operator explicitly records approval, or an explicit reject/regenerate signal. After lock, a later run without explicit direction to revise must refuse overwrite and fail loud — never silently regenerate. An explicit reject/regenerate afterward permits revision again.
- Exact byte-level `remotion/public/` sync layout/naming remains deferred to the Remotion project's own handoff conventions; this epic owns that the sync tool and render trigger exist and run as the final stage.

## Technical Decisions

- Thin integration only: reuse Epic 1 kernel (preflight, budget, contracts, retry/halt, resume) and Epic 2 outputs (complete `ProductionAssetsContract`, data-driven renderer, reusable `timeline.json` converter) — do not build new generation agents or a second converter.
- Sync into `remotion/public/` and the Remotion render trigger live as deterministic `@tool`s (alongside ingest/subtitle segmentation), invoked in-process — Remotion itself stays a separate npm project as the final deterministic render step.
- Resume point stays derived from persisted validated contracts (plus session id), never a separate counter; mid-Veo resume is the cost-saving case this epic must prove end-to-end.
- Artifacts lock only at the recorded human final-review approval (or explicit reject/regenerate) — never at "V1 rendered." Before that event, re-invocation is autonomous resume; after it, touching locked narration/subtitle cues/shot timing requires explicit user direction. Voice-owned subtitle cues are in scope for that lock alongside story/visual artifacts.
- `timeline.json` schema is already frozen from upstream contracts in Epic 2; this epic consumes it as-is so integration stays genuinely thin rather than becoming adapter work.

## Cross-Story Dependencies

- Story 3.1 depends on Epic 1's full pre-production chain and Epic 2's visual generation, data-driven renderer, and Story 2.1 converter being already validated; it only wires them end-to-end.
- Story 3.2's approval/lock boundary governs the narration, subtitle cues, and shot-timing artifacts produced earlier; it must distinguish "rendered, not yet approved" (still resumable/mutable) from "approved or explicitly rejected" (locked until explicit direction).
- Story 3.1's mid-Veo halt/resume case depends on Epic 2's keyed-upsert `ProductionAssetsContract` single-writer path so already-recorded shots survive re-invocation.
