# Using ReelSmith

The practical guide: how to make a reel, which switches exist, and what to type.
For how the pipeline works inside, see [README.md](README.md).

All commands run from the repo root. `PY` is the project's Python. Plain `python`
in the `(base)` conda env has older packages, and `conda run` does not work on this
machine:

```bash
PY=/opt/homebrew/anaconda3/envs/kayak-video/bin/python
```

---

## 1. One-time setup

```bash
conda activate kayak-video
pip install claude-agent-sdk google-genai google-auth google-cloud-storage pydantic Pillow
cd remotion && npm install && cd ..
gcloud auth application-default login        # Google Cloud (Vertex AI) login
codex login                                  # only if you use Codex for images
```

Then create a `.env` file at the repo root. See [7. Configuration](#7-configuration).

---

## 2. Modes of operation

Three independent choices decide what kind of reel you get. You can mix them freely.

| Choice | Set with | Options | Default |
| --- | --- | --- | --- |
| **Where the pictures come from** | `SOURCE_MODE=` | `content_and_images`: recompose the screenshots' own artwork<br>`content`: screenshots give only the words; every picture is invented | `content_and_images` |
| **How the story is told** | `STORY_SKILL=` | `story-craft`: dense and punchy, 90–115 words, 40–50 s<br>`story-craft-easy`: one clear lesson for a first-time viewer, 90–140 words, 40–60 s | `story-craft` |
| **Art style** (content mode only) | `--style` | `flat_editorial`, `watercolour`, `childrens_gouache`, `risograph` (files in `styles/`) | `flat_editorial` |

Which `SOURCE_MODE` to use:

| Your screenshots… | Use |
| --- | --- |
| show the book's illustrations (e.g. a book-summary app with pictures) | `content_and_images` (default) |
| are text only (pages, highlights, notes) | `content` |

The image provider (Gemini or Codex) is a separate setting. See [7.2 Image providers](#72-image-providers).

---

## 3. Create a new reel

### 3.1 Make a folder and place the screenshots

Every reel has its own folder under `projects/`. Screenshots go in its `source_images/`:

```bash
mkdir -p projects/<name>/source_images
cp ~/Desktop/shots/*.jpg projects/<name>/source_images/
```

`<name>` is any folder name, e.g. `atomic_habits`. Accepted: `.png`, `.jpg`/`.jpeg`,
`.webp`, `.bmp`, `.tif`/`.tiff`, in any letter case and in subfolders too. Nothing else
needs to exist; the run creates everything it writes inside this folder.

### 3.2 Run it

**Screenshots with images** (the pictures come from the book's art):

```bash
$PY -m orchestrator.run --project <name>
STORY_SKILL=story-craft-easy $PY -m orchestrator.run --project <name>      # easier story
```

**Screenshots without images** (text only, pictures invented):

```bash
SOURCE_MODE=content $PY -m orchestrator.run --project <name>                      # flat_editorial
SOURCE_MODE=content $PY -m orchestrator.run --project <name> --style watercolour
SOURCE_MODE=content STORY_SKILL=story-craft-easy $PY -m orchestrator.run --project <name> --style childrens_gouache
```

The finished video is `projects/<name>/book_reel.mp4`.

### 3.3 What happens, and where to look

The console shows each step with an estimated `~NN%` done. Everything is also saved
to `projects/<name>/orchestrator_runs/run_<time>.log`. To watch from another terminal:
`tail -f projects/<name>/orchestrator_runs/run_*.log`.

| Step | What it does | Saved to (inside the project) | Paid? |
| --- | --- | --- | --- |
| 1 | Reads the screenshots | `metadata/analyzed_assets.json` | Claude |
| 2 | Writes the narration | `metadata/final_story_plan.json` | Claude |
| 3 | Plans the shots (and the style, in content mode) | `metadata/visual_plan.json` | Claude |
| 4 | Records the voice, builds subtitles | `audio/`, `metadata/subtitle_cues.json` | Gemini TTS + STT |
| 5 | Makes the pictures and video clips | `generated/stills/`, `generated/veo/` | Images + Veo (the expensive part) |
| 6 | Renders the final video | `book_reel.mp4` | free |

**Cheap checkpoints.** Read the narration after step 2 and the shot plan after step 3.
If you don't like them, press Ctrl+C. Nothing for images or video has been spent yet.

---

## 4. All the runs it can do

| What you want | Command |
| --- | --- |
| New reel, screenshots with images | `$PY -m orchestrator.run --project <name>` |
| New reel, text-only screenshots | `SOURCE_MODE=content $PY -m orchestrator.run --project <name>` |
| …in another art style | add `--style watercolour` (or `childrens_gouache`, `risograph`) |
| Easy-to-follow story | add `STORY_SKILL=story-craft-easy` in front |
| Draw pictures with Codex (free, slower) | add `IMAGE_PROVIDER=codex` in front |
| Allow more spending | add `MAX_BUDGET_USD=60` in front |
| A different narrator voice | add `TTS_VOICE_NAME=Algieba` in front |
| Resume after a stop | run **the same command again**: finished steps are skipped and cost nothing |
| Redo the voice from scratch | `TTS_VOICE_NAME=Algieba $PY -m orchestrator.run --project <name> --retry-voice` |
| Make the cover | see [5. Cover image](#5-cover-image) |
| Move an old reel (from before `projects/`) in | `$PY -m orchestrator.migrate <name>` |

Compare two versions of one book by giving each its own folder with the same screenshots:

```bash
mkdir -p projects/<name>_easy/source_images
cp projects/<name>/source_images/* projects/<name>_easy/source_images/
STORY_SKILL=story-craft-easy $PY -m orchestrator.run --project <name>_easy
```

### Rules to remember

- **Rerun with the same switches.** A saved reel remembers its source mode and style:
  a rerun with a different `SOURCE_MODE` or `--style` stops before any paid call and
  tells you which one to use. `STORY_SKILL` is not remembered yet: leave it out on an
  easy reel and a saved narration over 115 words is rejected and rewritten, and the
  voice and shots after it are redone.
- **To switch mode, style or story skill, use a fresh project folder.** A project that
  already has a saved plan keeps it.
- **Two reels can't render at the same time**, because both use `remotion/public/`.

---

## 5. Cover image

The cover is a separate command. Watch the reel first, then:

```bash
$PY -m orchestrator.cover --project <name>
```

Claude picks the frame and a headline taken from the narration, and Codex draws the
cover (Codex always draws covers, whatever `IMAGE_PROVIDER` says). If the reel used the
easy story, add `STORY_SKILL=story-craft-easy` in front, or the cover stops with
"narration_script has … words".

| What you want | Command |
| --- | --- |
| Claude picks the frame and headline | `$PY -m orchestrator.cover --project <name>` |
| Your own headline | `… --hook "your line" --force` |
| Your own frame and headline (no Claude call) | `… --shot 6 --hook "your line" --force` |
| Change the current cover | `… --feedback "make the headline bigger"` |
| Change it and use new words | `… --feedback "..." --hook "new words"` |
| Redraw from another shot with feedback | `… --feedback "..." --shot 4` |
| Compare cover designs, free | `$PY test/try_cover.py --sweep --shot 6 --hook "your line"` |

Output:

```
generated/cover/cover.png         the 1080x1920 cover
generated/cover/cover_grid.png    what survives Instagram's profile-grid crop
generated/cover/cover_v<N>.png    each earlier version, kept by --feedback
generated/cover/feedback_history.json
```

**If a revision came out wrong:** `--feedback` edits the *latest* cover, so put back the
one you liked first, then reword and run again:

```bash
cp projects/<name>/generated/cover/cover_v2.png projects/<name>/generated/cover/cover.png
$PY -m orchestrator.cover --project <name> --feedback "only make the headline larger; keep the picture"
```

Watch out for two things:
- `--force` overwrites `cover.png` **without** keeping a version, so copy it aside first.
- Your own `--hook` words are not checked against the narration.

---

## 6. When a run stops

A stop is normal. The console says why, and the full report is the newest file here:

```bash
ls -t projects/<name>/orchestrator_runs/failure_*.json | head -1
```

Each reel's limits and counters live in `projects/<name>/orchestrator_runs/config.json`:

```json
{
  "limits": { "max_attempts": { "story_agent": 4, "visual_agent": 4, "voice_agent": 3,
                                "veo_agent": 3, "stills_agent": 3, "asset_analyst": 4 } },
  "usage":  { "budget_spent_usd": 12.2, "attempts_used": { "veo_agent_shot_2": 3 } }
}
```

| The report says | Do this |
| --- | --- |
| `Retry ceiling exhausted` / out of tries | in `config.json`, set that shot's `attempts_used` back to `0` (or raise `max_attempts`), then rerun |
| `Budget exhausted` | rerun with a higher `MAX_BUDGET_USD=…` |
| a session or usage limit | wait for the limit to reset, then rerun; that try is given back |
| `planned with SOURCE_MODE=…` or `planned in the … style` | rerun with the switch it names |
| a picture keeps failing on Gemini | nothing, if `IMAGE_FALLBACK_PROVIDER=codex` is set: the rerun moves that shot to Codex |

A shot's review feedback does not carry into a new run. After you reset a counter,
the first new try starts without the earlier correction, and later tries get feedback
again. If a video clip keeps failing the same way, the shot's motion is probably too
hard for Veo, and more tries rarely help.

---

## 7. Configuration

### 7.1 The `.env` file

Settings live in `.env` at the repo root (git-ignored). Write one `KEY=value` per line,
with no comments on the same line. A variable typed in front of a command wins over `.env`.

```
IMAGE_PROVIDER=gemini
IMAGE_FALLBACK_PROVIDER=codex
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
```

### 7.2 Image providers

The provider decides who draws the stills and Veo seeds.

| Setting | Options | Notes |
| --- | --- | --- |
| `IMAGE_PROVIDER` | `gemini` (default), `codex` | Gemini: fast, about $0.04 per image. Codex: free on your ChatGPT subscription, about 80 s per image, needs `codex login` |
| `IMAGE_FALLBACK_PROVIDER` | `codex`, `gemini`, `none` (default: off) | When a shot runs out of tries **because of its pictures**, it gets fresh tries on this provider. It can't be the same as `IMAGE_PROVIDER` |
| `IMAGE_MODEL` | `gemini-3.1-flash-image` | Gemini only |
| `CODEX_IMAGE_TIMEOUT_SECONDS` | `900` | The longest one Codex image may take |

Video clips always come from Veo (`VEO_MODEL`, default `veo-3.1-fast-generate-001`).

### 7.3 Every setting

| Setting | Default | What it does |
| --- | --- | --- |
| `SOURCE_MODE` | `content_and_images` | `content` = text-only screenshots, pictures invented |
| `STORY_SKILL` | `story-craft` | `story-craft-easy` = easier story, up to 140 words / 60 s |
| `--style` (flag) | `flat_editorial` | art style in content mode; files in `styles/` |
| `MAX_BUDGET_USD` | `40.0` | spending ceiling for the reel |
| `TTS_VOICE_NAME` | `Gacrux` | the narrator's voice |
| `CHARACTER_REFERENCE_PATH` | `assets/character/character.png` | the recurring character; set it empty to turn the character off |
| `CHARACTER_FACE_REFERENCE_PATH` | empty | optional face close-up (off: a photoreal face trips the image filter) |
| `CLAUDE_MODEL` | `claude-opus-5` | model for every agent |
| `CRITIC_MODEL` / `REVIEWER_MODEL` | `CLAUDE_MODEL` | story critic / picture reviewer |
| `MAX_VEO_ATTEMPTS` | `3` | tries per video clip |
| `SHOT_CONCURRENCY` | `4` | shots made at the same time |
| `VEO_CONCURRENCY` / `GEMINI_IMAGE_CONCURRENCY` / `CODEX_IMAGE_CONCURRENCY` | `3` / `2` / `1` | calls to each vendor at the same time |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` / `GCS_BUCKET_URI` | see `orchestrator/settings.py` | Google Cloud account and Veo storage |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | unset | turn on run tracing in Langfuse (optional) |

Budget and cost-estimate settings (`CLAUDE_SHOT_ATTEMPT_CAP_USD`, `CLAUDE_REVIEWER_CAP_USD`,
`VEO_CALL_COST_USD`, `IMAGE_CALL_COST_USD`) are explained in the README's
[Configuration](README.md#configuration) table.

### 7.4 Shared files

| Path | What it is |
| --- | --- |
| `assets/character/` | the recurring character sheets; a project's own `assets/character/character.png` wins |
| `styles/*.json` | the art styles; add your own by dropping in a file (format in the README's [Art styles](README.md#art-styles)) |
| `orchestrator/skills_plugin/skills/` | the agents' writing and planning rules (`story-craft`, `story-craft-easy`, `visual-planning`, `visual-planning-content`, …) |
