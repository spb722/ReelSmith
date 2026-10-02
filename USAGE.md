# How to make a reel

This guide shows you how to turn book screenshots into a short video (a "reel").
Just follow the steps. For how it works inside, see [README.md](README.md).

---

## Before you start (once per terminal)

Open a terminal and run:

```bash
cd ~/PycharmProjects/book_reels
conda activate kayak-video
```

Do this every time you open a new terminal. The start of the prompt should now say
`(kayak-video)`, not `(base)`.

> First time on this computer? See [First-time setup](#first-time-setup) at the end.

---

## Make a reel in 3 steps

### Step 1: Choose a name and make a folder

Pick a short name for the book, like `atomic_habits`. Then run:

```bash
mkdir -p projects/atomic_habits/source_images
```

### Step 2: Put your screenshots in the folder

Copy your screenshots into `projects/atomic_habits/source_images/`, either with Finder or:

```bash
cp ~/Desktop/shots/* projects/atomic_habits/source_images/
```

PNG and JPG both work.

### Step 3: Run it

First, look at your screenshots and pick one:

**A. The screenshots have pictures** (the book's own illustrations):

```bash
python -m orchestrator.run --project atomic_habits
```

**B. The screenshots are only text** (no pictures, so the video's pictures are made from the story):

```bash
SOURCE_MODE=content python -m orchestrator.run --project atomic_habits
```

It takes about 20–40 minutes. When it's done, your video is at:

```
projects/atomic_habits/book_reel.mp4
```

---

## Extra options

Add these to the Step 3 command.

| I want… | Add this | Example |
| --- | --- | --- |
| A story that's easier to follow | `STORY_SKILL=story-craft-easy` at the **start** | `STORY_SKILL=story-craft-easy python -m orchestrator.run --project atomic_habits` |
| A different drawing style *(only for B, text screenshots)* | `--style <name>` at the **end** | `SOURCE_MODE=content python -m orchestrator.run --project atomic_habits --style watercolour` |
| Free pictures (slower) | `IMAGE_PROVIDER=codex` at the start | `IMAGE_PROVIDER=codex python -m orchestrator.run --project atomic_habits` |
| To allow more spending | `MAX_BUDGET_USD=60` at the start | The normal limit is $40 per reel |
| More moving video shots | `MAX_VEO_SHOTS=5 MAX_BUDGET_USD=60` at the start | The normal count is 3. Only a new shot plan uses it. Raise the budget too, or the extra videos become still pictures |
| Video shots up to 15 s long | `VEO_EXTEND_LONG_SHOTS=true` at the start | Off normally, so a shot longer than 8 s stays a still. Only a new shot plan uses it. Each such shot costs two video clips (about $2.40 a try) |
| A different narrator voice | `TTS_VOICE_NAME=Algieba` at the start | The normal voice is `Gacrux` |
STORY_SKILL=story-craft-easy
You can combine them:

```bash
SOURCE_MODE=content STORY_SKILL=story-craft-easy python -m orchestrator.run --project atomic_habits --style childrens_gouache
```

**Drawing styles** (for B only):

| Name | Looks like |
| --- | --- |
| `flat_editorial` | clean, flat magazine illustration (this is used if you don't choose) |
| `watercolour` | soft watercolour painting |
| `childrens_gouache` | warm, rounded children's-book painting |
| `risograph` | bold, grainy two-colour print |

---

## ⚠️ Three rules

1. **Always rerun a reel with the exact same command.** If you used `SOURCE_MODE`,
   `STORY_SKILL` or `--style` the first time, use them every time for that reel,
   including when you make its cover.
2. **To try a different option, use a new folder.** For example, to also make an
   easy version: make `projects/atomic_habits_easy/source_images/`, copy the same
   screenshots in, and run it with the new option.
3. **Make one reel at a time.** Two videos can't be put together at the same moment.

---

## While it runs

- The screen shows each step and roughly how much is done (for example `~45%`).
- **Save money by checking early:** after "Step 2" and "Step 3" finish, you can read
  the story in `metadata/final_story_plan.json` and the plan for the pictures in
  `metadata/visual_plan.json`, inside the reel's folder. If you don't like them, press
  **Ctrl+C**. Nothing for pictures or video has been paid for yet.
- To watch from a second terminal:
  `tail -f projects/atomic_habits/orchestrator_runs/run_*.log`

---

## If it stops

Stopping is normal, and nothing is lost. Read the last message on the screen, then:

| The message says | What to do |
| --- | --- |
| something failed or ran out of tries | Run the **same command again**. Finished parts are skipped and cost nothing |
| it ran out of tries on a shot | Open `projects/<name>/orchestrator_runs/config.json`, find that shot (for example `"veo_agent_shot_2": 3`), change the number to `0`, save, then run the same command again |
| budget exhausted, or "needs $… reserved … only $… of the budget is left" | Run again with `MAX_BUDGET_USD=60` at the start, or add the line `MAX_BUDGET_USD=60` to `.env`. Before making pictures, the run holds back the worst-case cost of every shot (about $4 each), so a reel with many shots needs a higher limit even though it really spends much less. You only pay for what is used |
| session or usage limit | Wait until the limit resets, then run again |
| "vendor limit", `429` or `RESOURCE_EXHAUSTED` | Google's image service got too many requests. Nothing is lost. Wait a few minutes and run again. If it keeps happening, add `GEMINI_IMAGE_CONCURRENCY=1` at the start (fewer pictures at once), or `IMAGE_PROVIDER=codex` (free, slower) |
| the voice keeps failing | Run with `--retry-voice` at the end (and try `TTS_VOICE_NAME=Algieba` at the start) |
| "planned with SOURCE_MODE=…" or "planned in the … style" | Run again with the setting the message names |
| "narration_script has … words" | Add `STORY_SKILL=story-craft-easy` at the start (this reel was made with the easy story) |

If the same shot keeps failing in the same way, more tries rarely fix it. Send the
message to whoever maintains the pipeline.

The full details of each stop are saved in `projects/<name>/orchestrator_runs/failure_*.json`.

---

## Make the cover picture

Watch the video first. If you like it, run:

```bash
SOURCE_MODE=content STORY_SKILL=story-craft-easy python -m orchestrator.cover --project atomic_habits
```

The cover is saved at `projects/atomic_habits/generated/cover/cover.png`.
`cover_grid.png` next to it shows how it looks in your Instagram profile grid.

**Change the cover:**

| I want… | Run |
| --- | --- |
| To change something | `python -m orchestrator.cover --project atomic_habits --feedback "make the headline bigger"` |
| Different headline words | add `--hook "your words"` |
| A different picture from the video | add `--shot 4` (shot number) |
| A brand-new cover | `python -m orchestrator.cover --project atomic_habits --force` |

**Tips:**
- Say exactly what to change and what to keep, like *"move the headline up; keep the colours"*.
- Each change keeps the previous cover as `cover_v1.png`, `cover_v2.png`, and so on.
- **If a change came out badly, go back first:** copy the good version over
  `cover.png`, then try again with different words:
  ```bash
  cp projects/atomic_habits/generated/cover/cover_v1.png projects/atomic_habits/generated/cover/cover.png
  ```
- `--force` does **not** keep the old cover. Copy it somewhere first if you might want it.
- Headline words you type with `--hook` are used exactly as typed, so use words from the video.
- To try several cover designs for free before choosing:
  `python test/try_cover.py --sweep --shot 6 --hook "your words"` (results in `test/out/`).
- Check the spelling on the cover before posting.

---

## Settings file (optional)

To avoid typing the same options every time, put them in a file called `.env` in
the `book_reels` folder, one per line:

```
IMAGE_PROVIDER=gemini
IMAGE_FALLBACK_PROVIDER=codex
```

An option typed in front of a command wins over the file.

**Who draws the pictures:**

| Setting | What it means |
| --- | --- |
| `IMAGE_PROVIDER=gemini` | Google Gemini draws them: fast, a few cents each *(normal)* |
| `IMAGE_PROVIDER=codex` | Codex draws them: free with your ChatGPT plan, but slower (about 1–2 minutes each) |
| `IMAGE_FALLBACK_PROVIDER=codex` | If Gemini keeps failing on a picture, try Codex for it automatically |

Video clips are always made by Google Veo, and covers are always drawn by Codex.

**The recurring character:** the person who appears in the videos comes from
`assets/character/character.png`. Replace that file to change them, or add
`CHARACTER_REFERENCE_PATH=` (with nothing after `=`) to turn the character off.

All other settings are listed in the README's [Configuration](README.md#configuration) section.

---

## First-time setup

Only once per computer:

```bash
conda activate kayak-video
pip install claude-agent-sdk google-genai google-auth google-cloud-storage pydantic Pillow
cd remotion && npm install && cd ..
gcloud auth application-default login
codex login          # only if you use Codex
```

Moving an old reel (made before the `projects/` folder existed) into `projects/`:
`python -m orchestrator.migrate <name>`.

Adding your own drawing style: copy one of the files in `styles/`, rename it, and
change the words inside it. The README's [Art styles](README.md#art-styles) section
shows the format.
