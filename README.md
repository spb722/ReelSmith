# ReelSmith

Turns a handful of book screenshots into a finished 9:16 short-form reel — narration, voice, subtitles, generated visuals, and a rendered MP4 — with no manual step in between.

You point it at a directory of screenshots and walk away:

```bash
python -m orchestrator.run source_images
```

The most recent run produced a 53-second, 1080×1920, 30fps reel from 5 screenshots: 6 shots (4 Gemini-composed stills, 2 Veo clips), 28 subtitle cues, for about $3.83 of API spend.

## How it works

The pipeline is deliberately split. Claude agents do the work that needs judgment — reading screenshots, writing narration, planning shots, deciding whether a generated image is actually good. Deterministic Python does everything where a "creative" answer would be a bug: timing, alignment, segmentation, composition, rendering.

```
preflight  ──  ADC credentials → GCS bucket access → budget headroom
    │
    ├─ 1. screenshot understanding   asset_analyst   (Claude reads the screenshots)
    ├─ 2. narration                  story_agent     (write → self-review → revise)
    ├─ 3. visual plan                visual_agent    (shot list + still-vs-Veo mode per shot)
    ├─ 4. voice                      deterministic   (Gemini TTS → Google STT → subtitle cues)
    ├─ 5. Veo generation             veo_agent       (video shots, seeded images only)
    ├─ 6. stills generation          stills_agent    (Gemini-recomposed stills)
    └─ 7. delivery                   deterministic   (timeline.json → sync → Remotion render)
                                                          ↓
                                                  remotion/out/book_reel.mp4
```

Subtitle segmentation in stage 4 is dynamic-programming, not an LLM — an earlier LLM-grouping approach was unreliable and was deliberately replaced. Word timing comes from real STT alignment against the narration audio, so cues match what the voice actually says rather than what the script predicted.

### The kernel

Every stage runs through the same bounded-execution machinery, which is most of what makes an unattended run safe:

- **Schema contracts.** Every stage's output is validated against a pydantic contract (`AnalyzedAssetsContract`, `FinalStoryPlanContract`, `VisualPlanContract`, `SubtitleCuesContract`, `VeoOutcomeContract`, `StillOutcomeContract`, `ProductionAssetsContract`) before the next stage may read it.
- **Semantic QA on top of schema.** For generated images and clips, schema-valid is not approved — the producing agent does its own visual QA and can reject its own output.
- **Bounded retry.** A failed validation hands control back to the agent to self-correct, with an explicit iteration ceiling. There are no unbounded loops.
- **Hard halt with a report.** Exhausting the ceiling stops the run and writes `orchestrator_runs/failure_<stage>_<timestamp>.json` with the failed contract, attempt count, and partial artifact paths. Nothing is substituted, and nothing prompts you mid-run.
- **Resume from the last valid contract.** Re-invoking after a halt picks up at the last stage with a validated persisted contract instead of restarting from the screenshots. Stages whose output is still valid print `skipped` and cost nothing.
- **Budget ceiling.** Spend accumulates in `orchestrator_runs/run_manifest.json` and is checked in preflight; expensive shots reserve headroom before they start.
- **Single writer of record.** Per-shot state is merged by shot id through one keyed-upsert path in the orchestrator. Agents never overwrite a shared file.

### Hard rules the code enforces

These exist because each one is a defect that actually happened:

- Veo never receives a raw screenshot — only a recomposed seed image.
- A shot's production asset is read from `ProductionAssetsContract`, never inferred from filename, bucket listing, or recency.
- Veo failures are inspected from the full operation object, so an RAI-filter rejection surfaces as a structured reason instead of a bare failure.
- `shot_fingerprint` is stamped by the orchestrator from the authoritative visual plan, not trusted from agent output.
- Video shots structurally cannot take a `transform` — the shot-2 zoom/breathing defect is foreclosed by the component's type, not by a fix reapplied per reel.

## Repo layout

```
orchestrator/
  run.py              stage chain + CLI entrypoint
  preflight.py        credentials → bucket → budget, never skipped
  settings.py         one source of truth for project, region, models, costs
  agents/             asset_analyst, story_agent, visual_agent, veo_agent, stills_agent
  contracts/          one pydantic model per artifact shape
  tools/              in-process @tool servers: deterministic, gemini, veo,
                      timeline_converter, regression_harness
  state/              run manifest + production-asset upsert
  tests/              ~340 tests
remotion/             data-driven TS renderer (1080×1920, 30fps)
  src/timeline.ts     consumes timeline.json + subtitle_cues.json
metadata/             generated pipeline database — one JSON per stage
generated/            veo/, veo_seeds/, stills/
orchestrator_runs/    run manifest, per-attempt records, failure reports
docs/                 architecture, epics, per-story specs, troubleshooting
```

`metadata/` is generated output, not configuration. Regenerate it by re-running the owning stage rather than hand-editing.

## Setup

Requires **Python 3.11** (the `kayak-video` conda environment), **Node.js** for Remotion, and **ffmpeg/ffprobe** for the regression harness.

```bash
conda activate kayak-video
pip install claude-agent-sdk google-genai google-auth google-cloud-storage pydantic Pillow

cd remotion && npm install && cd ..

gcloud auth application-default login
```

Auth is Google Cloud ADC against Vertex AI, not an API key. Speech-to-Text is called over REST with an authorized session, so no `google-cloud-speech` package is needed.

There is no `requirements.txt` or `pyproject.toml` — the conda environment is where dependencies live. Known-good versions: `claude-agent-sdk` 0.2.152, `google-genai` 2.23.0, `pydantic` 2.13.4, Remotion 4.0.524. Don't upgrade `google-genai` reflexively; diagnose the actual traceback first.

## Running

```bash
python -m orchestrator.run source_images
```

Every invocation runs preflight first, fresh or resumed. A halt is normal operation, not a crash — read the newest failure report and re-invoke:

```bash
ls -t orchestrator_runs/failure_*.json | head -1
python -m orchestrator.run source_images    # resumes at the halted stage
```

To work on the renderer alone, with the current reel's data already synced into `remotion/public/`:

```bash
cd remotion && npm run dev      # Remotion studio
```

### Configuration

Everything is environment-variable overridable, with defaults in `orchestrator/settings.py`:

| Variable | Default | Notes |
| --- | --- | --- |
| `GOOGLE_CLOUD_PROJECT` | `gen-lang-client-0240752803` | |
| `GOOGLE_CLOUD_LOCATION` | `global` | |
| `GCS_BUCKET_URI` | `gs://sachin-kayaking-video-test/book_reels/veo/` | Veo output staging |
| `MAX_BUDGET_USD` | `15.0` | Per-run ceiling |
| `VEO_MODEL` | `veo-3.1-fast-generate-001` | |
| `GEMINI_MODEL` | `gemini-2.5-flash` | |
| `IMAGE_MODEL` | `gemini-3.1-flash-image` | |
| `MAX_VEO_ATTEMPTS` | `3` | |
| `VEO_CALL_COST_USD` / `IMAGE_CALL_COST_USD` | `1.20` / `0.04` | Estimates for budget reservation, not billing |

## Tests

```bash
python -m pytest orchestrator/tests -q
```

**Currently 323 pass, 2 fail, 12 error.** The failures are a known, isolated issue, not general breakage: the Story 2.1 regression harness and the timeline-converter tests read `metadata/visual_plan.json` and `metadata/subtitle_cues.json` *live* rather than from frozen fixtures. Running the pipeline for a new reel overwrites those files, so the reference-reel checks now see the wrong reel's data (28 cues where they expect the reference's 24). The fix is to snapshot the reference inputs into `orchestrator/tests/fixtures/`, alongside the `reference_reel_metrics.json` that's already there.

That regression harness is a one-time check that the data-driven renderer reproduces the original approved reel — it is deliberately not a per-run production gate, since a new reel has no reference to compare against.

## Status

Epics 1 through 3 are implemented: the full chain runs unattended from screenshots to a rendered MP4. Remaining work is **Story 3.2 — the locked-artifact / approval boundary**: once a reel is reviewed and approved, its narration, subtitle cues, and shot timing should be immune to rewriting by a later run without explicit direction.

`remotion/out/` is gitignored, so rendered MP4s stay local.

## Docs

Read these before changing the pipeline — they are the source of truth:

- `AGENTS.md` — policy, pitfalls, and where things live
- `docs/book_reels_architecture.md` — pipeline architecture
- `docs/architecture/architecture-book_reels-2026-09-15/ARCHITECTURE-SPINE.md` — the orchestration design and its numbered decisions (AD-1 … AD-15)
- `docs/epics.md` — requirements, epics, and per-story acceptance criteria
- `docs/book_reels_live_run_fixes_2026-09.md` — symptom → cause → fix table from the first live run
- `docs/book_reels_troubleshooting_agent_playbook.md` — failure triage
