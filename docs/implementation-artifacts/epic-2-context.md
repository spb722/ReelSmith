# Epic 2 Context: Autonomous Visual Asset Generation & Data-Driven Rendering

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Given Epic 1's validated visual plan, produce every shot's approved production asset (Veo clip or Remotion still) autonomously, and make the Remotion renderer itself data-driven so a new reel never again requires a coding agent to hand-write `timeline.ts`/`BookReel.tsx`. The epic sequences a regression harness first — reconstructing `timeline.json` for the existing approved "Backwards Law" reel and proving pass/fail against it — before the renderer rewrite, so the rewrite has a real signal from day one instead of a promise validated after the fact. It also closes out this pipeline's worst documented failure history: raw-screenshot leakage into Veo, per-shot state overwrite races, ambiguous production-vs-test asset selection, opaque Veo failures, and a video-transform bug that made Veo clips visibly zoom/breathe inside Remotion.

## Stories

- Story 2.1: timeline.json converter & reference reel regression harness
- Story 2.2: Data-driven renderer core + structural `transform:none` enforcement
- Story 2.3: Autonomous Veo generation, production asset manifest & structured failure inspection
- Story 2.4: Autonomous stills generation

## Requirements & Constraints

- A single, deterministic, reusable `timeline.json` converter (inputs: `VisualPlanContract` + `SubtitleCuesContract` + a per-shot final asset) must be built once and reused by every later consumer (including Epic 3) — never reimplemented as a one-off.
- The converter's output schema must be derived directly from `VisualPlanContract`'s and `SubtitleCuesContract`'s existing field shapes, never invented independently, since a schema not backed by real upstream contracts becomes late-discovered adapter work later.
- The regression harness is a one-time proof against the existing reference reel, explicitly not a per-run production gate (a new reel has no reference to compare against). Running it against the current hand-authored renderer, before the rewrite, is expected to fail or be inconclusive — that itself is proof the harness measures something real.
- Regression pass criteria: duration/fps/resolution/frame count must exactly match the reference MP4 (52.44s, 30fps, 1573 frames; frame count = `floor(duration × fps)`, never rounded up). Frame comparison at each of the 24 subtitle-cue centres (`floor(midpoint(cue.start, cue.end) × fps)`) uses perceptual hash, not pixel equality (encoder output isn't bit-reproducible across runs/machines) — starting threshold Hamming distance ≤ 5 of 64 bits, flagged for calibration against first real output. Audio content is out of scope for frame comparison (`narration.wav` reused unchanged, no checksum needed), but the render must assert an audio track is present with duration matching the video's.
- The rewritten renderer must consume `timeline.json` + `subtitle_cues.json` directly with zero hand-authored code changes per new reel.
- `transform` must never be applicable to a video-layer component — enforced structurally (the video-shot component/type offers no transform prop), not by convention or comment. Ken-Burns/transform effects (zoom, pan, spring/interpolate transforms) remain available only for stills; video-shot transitions are opacity-only. This is a hardened repeat of a real production defect (Veo shots 2, 5, 6 visibly zoomed/breathed after Remotion applied still-image transform logic to them), so it must not be re-fixable-but-reintroducible — it must be structurally impossible.
- `veo_tools`'s generation call must accept only a path produced by the seed-recomposition tool under its own contract, and must reject/fail loud on any `source_images/` path — never silently substitute one. This closes a real historical bug where a lost seed-result record caused a raw screenshot to be fed into Veo.
- Veo result inspection must check both `operation.response` and `operation.result`, distinguish `video.uri` from `video.video_bytes`, and surface any RAI-filter rejection as a structured `VeoFailureContract` — never a bare success/fail.
- Only `visual_agent` (Epic 1) assigns or changes a shot's generation mode; `veo_agent`/`stills_agent` only ever generate for the mode already assigned to a shot, and a QA failure never lets one agent unilaterally reassign a shot to the other's mode.
- Per-shot writes into `ProductionAssetsContract` (and any other shot-keyed contract/state) must go through a keyed upsert (merge by shot id); the orchestrator is the sole writer-of-record — an agent returns its validated per-shot result to the orchestrator rather than writing the shared file directly. Two shots completing in the same run must never lose or overwrite each other's entry.
- Any agent selecting a generated asset for downstream use must read it from the named `ProductionAssetsContract` — never infer "the right one" from GCS bucket listing, filename, or recency (a real historical near-miss: an old test object was almost mistaken for the production asset).
- A still that fails its own visual QA (e.g. duplicate-subject defect) retries up to the configured iteration ceiling before halting, reusing Epic 1's kernel retry/halt machinery rather than reimplementing it.
- By epic's end, every shot in the visual plan (Veo and stills) has exactly one approved `ProductionAssetsContract` entry.
- The 14 existing scripts' visual-generation logic (Veo seed prep, Veo generation, stills prep) must be refactored into in-process `@tool`-wrapped functions under a `create_sdk_mcp_server`, never invoked as subprocesses — same convention as Epic 1.

## Technical Decisions

- Sequencing within the epic is deliberate: build the `timeline.json` converter and regression harness (2.1) before the renderer rewrite (2.2), so the rewrite is validated against a real, already-wired signal rather than a rewrite-then-hope-it-matches approach.
- `visual_plan.json` (as produced by Epic 1's `visual_agent`) and `subtitle_cues.json` (as produced by `voice_agent`) carry no timing/subtitle-cue linkage fields that pre-existing scripts (`prepare_remotion_stills.py`, `prepare_veo_seed_images.py`, `generate_veo_clips.py`) expect (e.g. `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids`) — those three scripts would `KeyError` against the new pipeline's contracts as-is. This epic's tools/converter must not assume those fields exist on the new contracts; reconcile by building fresh in-process tool logic against the actual `VisualPlanContract`/`SubtitleCuesContract` shapes rather than assuming compatibility with the legacy scripts' expected input shape.
- `SubtitleCuesContract`'s `Cue` shape (as Epic 1 built it) does not carry `style_hint`/`duration_seconds`/`start_word_index`/`end_word_index`/`duration_warning`, which the existing `remotion/src/components/Subtitles.tsx` reads (e.g. `style_hint` selects a typography variant like `IMPACT`). Story 2.2's renderer rewrite is where this gap must be resolved — either by extending the contract (additive-only, per Epic 1's contract-evolution convention) or by deriving the needed rendering hints inside the `timeline.json` converter.
- Existing reference structure the converter/renderer must remain compatible with: `remotion/public/{audio,stills,video,data}/` (narration.wav; per-shot still PNGs; per-shot video MP4s; `subtitle_cues.json` + `visual_plan.json`/`timeline.json`). The existing composition is `id: BookReel`, `1080x1920`, `30fps`.
- A recommended production-asset-manifest shape from prior pipeline analysis (informative precedent for `ProductionAssetsContract`, not a fixed schema to copy verbatim): per-shot entries carrying `type` (`still`/`video`), local `path`, optional `gcs_uri` for video, and an `approved` flag; plus a top-level audio/narration path.
- Contracts continue to follow Epic 1's naming convention: one pydantic model per artifact shape (`ProductionAssetsContract`, `VeoFailureContract`), evolved by adding fields only.
- `veo_agent`/`stills_agent` are each their own scoped `AgentDefinition`, consistent with Epic 1's agent-per-responsibility pattern; they reach Veo/Gemini/deterministic tools only through named `@tool`s behind an MCP server boundary, never directly.

## Cross-Story Dependencies

- Story 2.1's converter and regression harness must exist before Story 2.2's renderer rewrite, which is verified against that same harness (the harness's failing/inconclusive verdict against the old renderer becomes a pass against the new one).
- Story 2.1's converter is also the one Epic 3 calls later with live data for a newly-generated reel — it is not reimplemented there.
- Story 2.3 establishes the single-writer keyed-upsert path into `ProductionAssetsContract`; Story 2.4's `stills_agent` reuses that same orchestrator-owned write path rather than implementing its own.
- Story 2.3/2.4 both depend on Epic 1's validated `VisualPlanContract` (specifically `visual_agent`'s per-shot mode assignment) as their sole source of which shots need Veo vs. stills.
- This epic's outputs — a data-driven renderer, a complete `ProductionAssetsContract`, and the reusable `timeline.json` converter — are the direct inputs Epic 3 integrates end-to-end; Epic 3 does not build new generation or conversion logic, only wires this epic's already-validated capabilities together.
