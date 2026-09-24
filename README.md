# ReelSmith

Turns a handful of book screenshots into a finished 9:16 short-form reel — narration, voice, subtitles, generated visuals, and a rendered MP4 — with no manual step in between.

Each reel lives in its own project folder. You put that reel's screenshots in it
and walk away:

```bash
python -m orchestrator.run --project <name>
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
python -m orchestrator.run --project <name>
```

Every invocation runs preflight first, fresh or resumed. A halt is normal operation, not a crash — read the newest failure report and re-invoke:

```bash
ls -t orchestrator_runs/failure_*.json | head -1
python -m orchestrator.run --project <name>   # resumes at the halted stage
```

The voice stage stops after three transient attempts. To recover an exhausted voice stage and select Algieba:

```bash
conda activate kayak-video
TTS_VOICE_NAME=Algieba python -m orchestrator.run --project <name> --retry-voice
```

`--retry-voice` archives earlier voice attempts, diagnostics, audio and the prior run manifest under `orchestrator_runs/voice_history/`, then resets only the voice attempt count. Cumulative spending and other stage counts survive; the normal budget ceiling still applies. The command resumes the pipeline and can make paid calls.

Successful TTS audio is reused after a recognition failure only when `metadata/narration_audio.json` verifies its narration, direction, selected voice/model and current audio hash. A changed voice or unverifiable legacy audio triggers new TTS. An explicitly empty `TTS_VOICE_NAME` is an error. Previously approved legacy cues remain usable only without a voice override or new audio provenance.

If a take disagrees with the script, the stage now re-records rather than halting.
Gemini TTS occasionally adds or drops a line -- one live take appended an invented
"What would life be like if you did that?" -- and only a new take can change that
evidence. The cached recording is discarded first, because otherwise every attempt
re-transcribes the identical file to the identical failure and the retry ceiling is
decorative. `--retry-voice` does *not* do this: it copies the take into
`voice_history/` and leaves the original in place.

Extra words *after* the last script word no longer fail a run at all. The reel's
length comes from the last shot's end (`Composition.tsx`), so speech past it is
never rendered; the run logs that it was ignored. Extra words *inside* the
narration still fail, because they shift every later timestamp and desync the
subtitles.

The two percentages answer different questions and are not a strict/lenient pair:
`exact_match_ratio` is how much of the script was found (denominator: the script),
`normalized_match_ratio` is how much of what was said is in the script
(denominator: what was heard). The 98% gate uses the second.

Inspect `orchestrator_runs/word_timing_diagnostics.json` and the per-attempt `voice_agent_attempt_<n>_alignment.json` files for the recognized transcript, observed word spans, mismatches, and separate literal/normalized scores. Conservative number, percentage and hyphen equivalents can pass the unchanged 98% subtitle gate while preserving canonical caption text. Genuine disagreements halt with evidence; deterministic subtitle failures do not blindly regenerate speech. Attempted recognition requests are charged conservatively at the configured rate, including failed requests; reusing audio adds no TTS charge.

To work on the renderer alone, with the current reel's data already synced into `remotion/public/`:

```bash
cd remotion && npm run dev      # Remotion studio
```

### Recipes

`PY` below is this repo's interpreter. `conda run -n kayak-video` does not work on
this machine; call the environment's python by path:

```bash
PY=/opt/homebrew/anaconda3/envs/kayak-video/bin/python
```

| What you want | Command |
| --- | --- |
| A new reel, Codex images | `IMAGE_PROVIDER=codex $PY -m orchestrator.run --project <name>` |
| A new reel, Gemini images | `$PY -m orchestrator.run --project <name>` |
| Resume a halted reel | same command again — finished stages print `Already done` and cost nothing |
| More headroom | `MAX_BUDGET_USD=30 IMAGE_PROVIDER=codex $PY -m orchestrator.run --project <name>` |
| A different narrator | `TTS_VOICE_NAME=Algieba IMAGE_PROVIDER=codex $PY -m orchestrator.run --project <name>` |
| Redo the voice from scratch | `TTS_VOICE_NAME=Algieba $PY -m orchestrator.run --project <name> --retry-voice` |
| A cover, Claude picks frame and hook | `IMAGE_PROVIDER=codex $PY -m orchestrator.cover --project <name>` |
| A cover, your words | `IMAGE_PROVIDER=codex $PY -m orchestrator.cover --project <name> --hook "your line" --force` |
| A cover, your frame and words (no Claude call) | `IMAGE_PROVIDER=codex $PY -m orchestrator.cover --project <name> --shot 6 --hook "your line" --force` |
| Compare cover designs, free | `$PY test/try_cover.py --sweep --shot 6 --hook "your line"` |
| Move a pre-projects reel in | `$PY -m orchestrator.migrate <name>` |

Flags:

| Command | Flag | Meaning |
| --- | --- | --- |
| `run` | `--project <name>` | **required** — the folder under `projects/` |
| `run` | `--retry-voice` | archive voice attempts and reset only the voice counter |
| `cover` | `--project <name>` | **required** |
| `cover` | `--hook "..."` | use these exact words instead of asking Claude |
| `cover` | `--shot <n>` | build from this shot's artwork |
| `cover` | `--force` | replace an existing cover |

Every knob is an environment variable — see [Configuration](#configuration). The ones
you will reach for: `IMAGE_PROVIDER`, `MAX_BUDGET_USD`, `TTS_VOICE_NAME`,
`CHARACTER_REFERENCE_PATH` (set it empty to turn the character off for a run).

### When a run halts, and the config file

Every limit and every counter for a reel lives in one editable file:

```
projects/<name>/orchestrator_runs/config.json
```

```json
{
  "limits": {
    "max_attempts": {
      "asset_analyst": 4, "story_agent": 4, "visual_agent": 4,
      "voice_agent": 3, "veo_agent": 3, "stills_agent": 3
    }
  },
  "usage": {
    "budget_spent_usd": 10.12,
    "attempts_used": { "veo_agent_shot_3": 3 },
    "session_id": "..."
  }
}
```

**`limits`** is yours to set — raise a ceiling and re-run.
**`usage`** is written by the run, and is also where you reset a stuck counter
or correct a wrong charge. Edit either, save, re-run. No commands, no snippets.

A halt is normal. Read the newest report, then decide:

```bash
ls -t projects/<name>/orchestrator_runs/failure_*.json | head -1
```

| What you see | What to edit in `config.json` |
| --- | --- |
| `Retry ceiling exhausted` | set that stage's `attempts_used` back to `0`, or raise its `max_attempts` |
| `Budget exhausted: spent 30.0 of max 30.0` at startup, when you know you have not | set `usage.budget_spent_usd` to the real figure |
| `You've hit your session limit` / `usage limit` | nothing wrong with the reel — wait for the reset, then clear the counters those failed attempts consumed |

A ceiling left out of the file falls back to its built-in default, so a partial
edit still runs. A corrupt file falls back to a fresh config rather than
halting — preflight must never be blocked by bad state.

**Attempt ceilings come from this file only.** `MAX_BUDGET_USD` keeps its
environment override, because it is genuinely a per-run choice
(`MAX_BUDGET_USD=60 python -m orchestrator.run ...`); `usage.budget_spent_usd`
is what that ceiling is measured against.

Why a counter gets stuck when nothing is wrong with the reel: an external
vendor refusing service — a Claude session limit, a Codex credit shortfall —
currently looks the same to the retry machinery as a shot that will not render,
so it consumes an attempt. `docs/vendor-limit-fix.md` has the code change that
tells those apart. Until then, this file is how you recover.

Projects created before this file existed keep working: their old
`run_manifest.json` is read once and superseded by `config.json` on the next
save.

### One folder per reel

Every reel gets its own folder under `projects/`, and nothing is ever deleted to
make room for the next one:

```
book_reels/
  assets/character/     shared: the recurring character, used unless a project
                        drops its own assets/character/character.png in
  remotion/             shared: the renderer
  projects/
    fear-mask/
      source_images/    this reel's screenshots
      metadata/  generated/  audio/  orchestrator_runs/
      book_reel.mp4     the finished reel
      cover.png
    next-book/
      source_images/
```

Starting a new reel is making a folder:

```bash
mkdir -p projects/next-book/source_images
cp ~/Desktop/shots/*.jpg projects/next-book/source_images/

IMAGE_PROVIDER=codex python -m orchestrator.run   --project next-book
IMAGE_PROVIDER=codex python -m orchestrator.cover --project next-book
```

Every per-reel path in this repo is relative -- `metadata/visual_plan.json`,
`generated/stills/`, and twenty more -- so the orchestrator moves into the project
at startup and they all resolve inside it, without any of them being rewritten.
That is the mechanism the tests already used: fourteen modules isolate themselves
with `monkeypatch.chdir(tmp_path)`.

Only what every reel shares stays pinned to the repo root, via `repo_path()`: the
renderer, the regression harness's reference clip, and the default character sheet.
`remotion/public/` and `remotion/out/` are shared scratch that each render
overwrites, so the reel's keeping copy is `projects/<name>/book_reel.mp4`.

`--project` is required and never inferred -- writing a reel's output into the
wrong folder would overwrite finished work, so a typo fails immediately with a
list of the projects that exist. Two reels cannot render at the same time, since
both would stage into `remotion/public/`.

A reel made before projects existed moves in with:

```bash
python -m orchestrator.migrate fear-mask
```

which refuses to run if that project already holds anything.

### Instagram cover

The cover is a **separate command**, deliberately not one of the seven stages. You
watch the finished MP4 first; only if it is worth posting do you draw a cover:

```bash
# 1. make the reel
IMAGE_PROVIDER=codex python -m orchestrator.run --project <name>

# 2. watch it, then make the cover
IMAGE_PROVIDER=codex python -m orchestrator.cover --project <name>
```

Nothing in the reel pipeline reads the cover's output, and re-running the reel
neither triggers nor invalidates a cover.

It reads only artefacts every run already produces -- the story arc, the narration,
the shot goals, and which shots have approved artwork -- so a different book with a
different arc goes through the identical code.

The same split as the rest of the repo. `cover_agent` chooses what cannot be
measured: which approved frame makes the strongest cover, and which of the reel's
own lines hooks hardest. Codex then designs the finished cover, headline included.

- **The hook must be the reel's own words.** Every word of it has to appear in the
  narration, checked in code. An agent asked for a hook will otherwise write
  marketing copy, and then the cover contradicts the first seconds of voiceover.
- **Codex draws the headline too.** An earlier version had it draw a text-free
  plate and composited the type with Pillow, on the reasoning that image models
  misspell. Run side by side in `test/try_cover.py`, the composited version looked
  like type dropped on a picture and the Codex-designed version looked designed,
  with the headline spelled correctly every time. The compositing was removed.
- **The brief makes the symbol the hero.** The instruction that won that sweep was
  not about typography: it was enlarging the story's central symbolic object and
  shrinking the character, which is what makes a cover readable as a thumbnail.
- **A grid preview is written**, because Instagram crops the 9:16 cover to a
  portrait slice in the profile grid, and a headline can survive full-screen while
  being cut off where most people first see the post.

Because the words are drawn rather than composited, a typo is possible and no code
can catch it. Look at the cover before posting; `--force` redraws it.

```
generated/cover/cover.png        the finished 1080x1920 cover
generated/cover/cover_grid.png   what survives the profile grid crop
metadata/cover_plan.json         the validated plan
```

To try design directions before committing to one, `test/try_cover.py --sweep`
renders several briefs of the same frame and hook into `test/out/`. Codex images
are free, so it costs only time.

Taste is involved, so there are overrides: `--hook "your own words"`, `--shot 6` to
force a frame, `--force` to replace an existing cover. Supplying both `--hook` and
`--shot` skips the Claude call entirely.

### Watching a run

Every stage reports as it goes, timestamped with elapsed time since the run started:

```
[00:00:00] ▸ Starting a new reel
[00:00:00] ▸ Step 1 of 7 — Looking at your screenshots
[00:00:00]   Try 1 of 4 · $15.00 left to spend
[00:00:04]     opening a screenshot to look at it
[00:01:12]   Done in 1m08s · cost $0.42 · saved metadata/analyzed_assets.json
[00:01:12] ▸ Step 4 of 7 — Recording the voice and building subtitles
[00:01:13]   Part 1 of 3: reading the script out loud (voice: Algieba)
[00:01:58]   Part 2 of 3: listening back to find when each word is spoken
```

The long waits — a Claude agent turn, a Veo poll, a Codex image call — print a
heartbeat rather than going silent, so a stalled run is distinguishable from a slow
one. The agents' tool calls are logged live as they stream. Everything printed is
also written to `orchestrator_runs/run_<timestamp>.log`, since the console scrollback
is usually gone by the time a long run halts.

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
| `IMAGE_MODEL` | `gemini-3.1-flash-image` | Used only by the `gemini` image provider |
| `IMAGE_PROVIDER` | `gemini` | `gemini` or `codex` — which backend draws stills and Veo seeds |
| `CODEX_IMAGE_TIMEOUT_SECONDS` | `900` | Ceiling on one Codex image call |
| `MAX_VEO_ATTEMPTS` | `3` | |
| `VEO_CALL_COST_USD` / `IMAGE_CALL_COST_USD` | `1.20` / `0.04` | Estimates for budget reservation, not billing |
| `TTS_VOICE_NAME` | `Gacrux` | The narrator. Empty is an error, not a default |
| `CLAUDE_MODEL` | `claude-opus-5` | Pinned so a reel's cost and quality cannot drift |
| `CHARACTER_REFERENCE_PATH` | `assets/character/character.png` | Project's own copy wins; falls back to the repo root. Set empty to turn the character off |
| `CHARACTER_FACE_REFERENCE_PATH` | *(empty)* | Off by default — a photoreal face crop trips the likeness filter |

#### Image provider

`IMAGE_PROVIDER=codex` draws the stills and Veo seeds with the local Codex CLI
instead of the Gemini image-edit API:

```bash
IMAGE_PROVIDER=codex python -m orchestrator.run --project <name>
```

Codex is a coding agent, not an image endpoint — it draws when the prompt opens with
the `$imagegen` directive and writes the result to a file. Each call stages a scratch
workspace (the scene plus the character sheet, under fixed names), attaches both with
`--image` so the model actually sees them, and reads the finished PNG back out. The
prompt ladder, the contracts, and the agents' visual QA are unchanged — only the thing
putting pixels on disk differs, and a Codex refusal advances the ladder exactly as a
Gemini refusal does.

Three practical differences:

- **It is slow.** One image is a whole agent turn: ~80s against Gemini's few seconds.
  A six-shot reel takes minutes, not seconds. Budget the wall clock, not the dollars.
- **It is free.** Codex bills the operator's ChatGPT subscription, so a Codex image
  reserves no budget headroom (`IMAGE_CALL_COST_USD` defaults to `0.0` under this
  provider; set it explicitly to override). `OPENAI_API_KEY` and `CODEX_API_KEY` are
  stripped from the call so a stray key cannot silently switch it to per-call billing.
- **It picks its own canvas.** Output is checked for a 9:16 portrait shape and
  rejected otherwise, rather than being letterboxed by Remotion three stages later.
  Observed output is 1080×1920, matching the reel exactly.

Preflight checks that `codex` is on PATH and logged in when this provider is selected,
so a missing or logged-out CLI stops the run up front rather than at the first shot
that needs a picture. `$imagegen` is a prompt convention, not a documented CLI flag —
if it changes, this provider breaks and `gemini` does not.

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
