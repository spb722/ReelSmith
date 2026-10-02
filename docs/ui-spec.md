# ReelSmith Studio — UI Functional Spec

A brief for designing the UI mockup of **ReelSmith**, a tool that turns book
screenshots into a short vertical video ("reel", 9:16, about 40–60 seconds) and
an Instagram cover image.

Today everything runs from the terminal. This UI replaces the terminal for the
day-to-day work: create a project, drop in screenshots, pick settings, run it,
watch progress, look at the reviews, and make the cover.

**Audience:** one person (the operator), on a desktop browser, running on their
own machine. No login, no teams, no billing screens.

---

## 1. What the tool does today (so the UI matches reality)

A **project** is a folder. It holds:

| Thing | What it is |
| --- | --- |
| Source screenshots | 3–15 PNG/JPG screenshots of a book page or book-summary app |
| Settings | Which models, story style, art style, character, limits, budget |
| Run history | Every run's log, each step's attempts, reviewer verdicts, money spent |
| Outputs | Narration audio, still pictures, video clips, the final `book_reel.mp4`, the cover image |

A **reel run** has 6 steps, always in this order:

| # | Step name (show this text) | Share of total time | What happens |
| --- | --- | --- | --- |
| 1 | Looking at your screenshots | ~9% | Reads the text and pictures in each screenshot |
| 2 | Writing the narration | ~26% | Writes the voice-over script; a critic reviews it (approve / reject with issues) |
| 3 | Planning the shots | ~10% | Splits the script into shots; decides which are still pictures and which are video clips |
| 4 | Recording the voice and building subtitles | ~4% | Text-to-speech, then word-by-word subtitle timings |
| 5 | Making the video clips and still pictures | ~49% | Makes every shot, several at once; a visual reviewer checks each one and may ask for a redo |
| 6 | Putting the final video together | ~2% | Renders the finished reel |

Important behaviours the UI must reflect:
- A step can **retry**. Each step has a maximum number of attempts. Running out
  of attempts **stops** the run with a reason.
- Step 5 works **per shot**, several shots at the same time. Each shot has its
  own attempts and reviewer verdict.
- The run has a **budget ceiling** in US dollars. It stops if the next action
  would go over it.
- A stopped run can be **resumed**. Finished work is kept; only the unfinished
  part runs again.
- **New in the UI — narration checkpoint:** by default the run does **not**
  pause; it goes straight through all 6 steps. Only when the operator turns on
  **Pause after narration** does the run stop after step 2, so they can read the
  narration, edit it if they want, and then continue. The pipeline does not do
  this yet; it will be built along with the UI.
- The percent complete is an estimate. It can pause during slow steps but never
  goes backwards.

The **cover** is a separate run, done after the reel exists. It picks a hook
line and a shot's artwork and makes a cover image. The operator can then give
written feedback ("make the headline bigger") to make a new version; old
versions are kept.

---

## 2. Screens overview

1. **Projects home** — list of all projects, create new.
2. **New project wizard** — name → screenshots → settings → review & run.
3. **Project page** — tabs: Overview, Screenshots, Settings, Run, Reviews, Outputs, Cover.
4. **Character pool** — add, remove, pick characters.
5. **Style library** — browse art styles with their sample images, and drop in new ones.
6. **Default settings** — the settings every new project starts with.
7. **Global setup** — cloud account, API keys, tool health checks.

Navigation: a left sidebar with Projects, Characters, Styles, Defaults, Setup.

---

## 3. Projects home

A grid or table of projects. Each card shows:
- Project name
- Thumbnail (cover image if made, else the first screenshot)
- Status badge: **Draft** (never run) · **Running** (with %) · **Stopped** (with reason) · **Finished**
- Last run date and how long it took
- Money spent so far
- Art style and character used (small chips)

Actions:
- **New project** (primary button)
- Search by name; filter by status
- Per card menu: Open · Duplicate (copy settings and screenshots into a new project) · Delete (confirm first)

Real examples of project names today: `37rule`, `37rule_easy`, `sweaty_tax`,
`setback`, `busy_not_the_same`, `you_get_what_you_measure`.

---

## 4. New project wizard

### Step A — Name
- Project name (becomes the folder name; letters, numbers, `-`, `_` only; must be unique).
- Optional: "Start from" — Defaults (default) or copy settings from an existing project.

### Step B — Screenshots
- Large **drag-and-drop area**; also a "Browse files" button. PNG, JPG.
- Dropped images appear as a **reorderable thumbnail strip**. The order matters:
  it is the reading order of the book.
- Per image: remove, view large, drag to reorder.
- Show count and a gentle warning outside 3–15 images.
- Reject duplicates and non-image files with an inline message.

### Step C — Settings
The same settings panel as the project's Settings tab (section 6), pre-filled
from Defaults. Show the most-used settings first ("Basic") and hide the rest
under "Advanced".

### Step D — Review & run
- Summary of name, screenshot count, key settings, character thumbnail.
- **Estimated cost** range and time (about 20 minutes for a typical run).
- Buttons: **Save as draft** · **Create and run**.

---

## 5. Project page

Header: project name, status badge, percent (if running), money spent / budget,
and the main action button, which changes with state:
- Draft → **Run**
- Running → **Stop**
- Stopped → **Resume** (and secondary "Start over")
- Finished → **Run again** (and secondary "Make cover")

Tabs:

### 5.1 Overview
- Final video player (if finished) with download button.
- Cover image (if made).
- Key facts: settings summary, total cost, total time, date.
- Last run result: finished, or stopped with the reason in plain words.

### 5.2 Screenshots
Same drag-and-drop and reorder area as the wizard. Editing screenshots after a
run shows a warning: "Changing screenshots means the next run starts over."

### 5.3 Settings
The settings panel from section 6. Shows which values differ from Defaults
(e.g. a dot or "changed" tag, with "reset to default" per field). Includes the
raw config editor (section 6.6).

### 5.4 Run (live progress)
This is the most important screen.

**Top:** overall progress bar with percent, elapsed time, money spent vs budget
(a second, thinner bar).

**Step list:** the 6 steps as a vertical stepper. Each step shows:
- State: waiting · running (spinner) · retrying (attempt 2 of 4) · done (✓, time taken) · failed
- Time taken and cost for that step
- Click to expand details: attempts, the reviewer verdict, what was produced

**"Pause after narration" button (off by default):** a toggle button on the Run
screen, next to Stop. It is off unless the operator clicks it. It can be clicked
before starting or while steps 1–2 are running; once step 3 has started it is
disabled ("Too late for this run"). When it is on, the button shows as pressed
and the stepper shows a small pause marker after step 2.

**Narration checkpoint (only when the button is on):** the run pauses and the
stepper shows step 2 as **"Waiting for you"** (an amber state, not an error).
The page shows a clear panel: "The narration is ready. Read it, edit it if you
like, then continue." See section 5.8 for the editor. The overall percent holds
at ~35% while waiting, and the elapsed clock shows the waiting time separately
so it isn't counted as run time.

**Step 5 detail — shot grid:** one tile per shot. Each tile shows:
- Shot number and type: **Still** or **Video clip**
- State: queued · making · being reviewed · redo requested · approved · failed
- Attempt count (e.g. "attempt 2 of 3")
- Preview image when ready (for video clips, four preview frames or a small player)
- Click → shot detail drawer: the prompt used, each attempt's image/video, the
  reviewer's comments per attempt

**Live log panel:** collapsible, auto-scrolling, timestamped lines exactly as
the terminal prints today (e.g. `[04:12] Step 3 of 6 — Planning the shots · ~35%`).
Has "copy" and "open full log".

**When a run stops:** a clear banner with the reason in plain words, and what
to do. Examples:
- "Shot 5 used all 3 attempts. Raise the still-picture attempt limit or resume."
- "The next shot would go over the $40 budget."
- Actions: **Resume**, **Raise limit & resume** (opens the limit field inline), **View details**.

Controls: **Stop** (confirm), and for a stopped run, **Resume**. Optional
"Redo voice only" (today's `--retry-voice` switch).

### 5.5 Reviews
Everything the automatic reviewers said, in one place, per run.
- **Story review:** each attempt of the narration script, the critic's verdict
  (Approve / Reject) and its list of issues. Show the final approved script in full.
- **Shot reviews:** per shot, each attempt with its picture/clip and the visual
  reviewer's verdict and comments (including camera-motion checks for video clips).
- Filter: all · rejected only · approved only.
- Compare attempts side by side for a shot.

### 5.6 Outputs
File browser for what the run made, with previews:
- Final reel (`book_reel.mp4`)
- Narration audio with the script and subtitles
- Still pictures, video clips, and their seed images
- The story plan and shot plan (readable view, with a "raw JSON" toggle)
- Run logs (one per run, newest first)

### 5.7 Cover
See section 8.

### 5.8 Narration review & edit
Opens when the run pauses after "Writing the narration", and can also be opened
from Reviews later.

- **The script**, large and readable, in an editable text area. Live word count
  and estimated spoken length (e.g. "112 words · ~46 s"), with the story style's
  limits shown (Dense & punchy: 90–115 words, 40–50 s; Easy to follow: 90–140
  words, 40–60 s). Going outside the limits shows a warning, not a block.
- **The critic's verdict** beside it: Approve / Reject and its list of issues.
- **Story outline** (read-only, collapsible): the hook, the main point, the ending.
- **Edit history:** "Original (written by Claude)" and each of the operator's
  saved edits, with a diff view and "Restore this version".
- Actions:
  - **Continue with this narration** (primary) — the run carries on from step 3
    ("Planning the shots") using the text as shown.
  - **Rewrite with instructions** — a text box ("make the ending punchier",
    "simpler words") that sends the script back to Claude for a new version,
    then pauses again for review.
  - **Discard my edits** — back to Claude's version.
- If the narration is edited **after** later steps already ran, warn that the
  shots, voice and video will be remade on the next run.

---

## 6. Settings panel

Used in three places: the wizard, a project's Settings tab, and the Defaults
screen. Each field shows a short help line. Values below are the real options
and defaults in the code today.

### 6.1 Content & story (Basic)

| Field | Control | Options | Default |
| --- | --- | --- | --- |
| Where the pictures come from | Segmented toggle | **Use the screenshot's artwork** (`content_and_images`) · **Invent new pictures from the text** (`content`) | Use the screenshot's artwork |
| Story style | Radio cards | **Dense & punchy** (`story-craft`: 90–115 words, 40–50 s) · **Easy to follow** (`story-craft-easy`: 90–140 words, 40–60 s) | Dense & punchy |
| Art style | Visual picker from the Style library (section 7b): cards with a sample image | `flat_editorial`, `watercolour`, `childrens_gouache`, `risograph`, `anime_inspired`, `bold_graphic`, `cute_clay`, `line_colour_sketch`, `neon_glow`, `pixel_art`, plus any dropped in | `flat_editorial` |
| Pause after narration | Switch (same as the Run screen button) | On · Off | **Off** |
| Character | Picker from the character pool (section 7) | Any character, or **No character** | The default character |
| Narrator voice | Dropdown with a play-sample button | Gemini TTS voices | `Gacrux` |

Art style is only used when pictures are invented; grey it out otherwise and say why.

### 6.2 Models (Basic)

| Field | Options | Default |
| --- | --- | --- |
| Claude model (writes story, plans shots, directs) | Claude models | `claude-opus-5` |
| Critic / reviewer model | Claude models | same as above |
| Image maker | **Gemini** · **Codex** (uses the ChatGPT subscription, costs $0 per image, slower) | Gemini |
| Backup image maker | None · the other one | None |
| Image model | text / dropdown | `gemini-3.1-flash-image` |
| Video model | text / dropdown | `veo-3.1-fast-generate-001` |
| Video resolution | 720p · 1080p | 720p |

### 6.3 Budget & limits (Advanced)

| Field | Default |
| --- | --- |
| Budget ceiling for the whole run (USD) | 40.00 |
| Max attempts — screenshots | 4 |
| Max attempts — narration | 4 |
| Max attempts — shot planning | 4 |
| Max attempts — voice | 3 |
| Max attempts — each video clip | 3 |
| Max attempts — each still picture | 3 |
| Claude spend cap per shot attempt (USD) | 3.00 |
| Claude spend cap per review (USD) | 1.00 |

### 6.4 Speed (Advanced)

| Field | Default |
| --- | --- |
| Shots in production at once | 4 |
| Video clips generating at once | 3 |
| Gemini images at once | 2 |
| Codex images at once | 1 |
| Codex image timeout (seconds) | 900 |
| Video status check interval / max wait (seconds) | 15 / 900 |

### 6.5 Cost estimates (Advanced, rarely touched)
Estimated cost per image ($0.04) and per video clip ($1.20). Used only to
reserve budget before a call.

### 6.6 Raw config editor
For power use: a JSON editor showing the project's full config file, with
syntax highlighting, validation on save (errors shown inline, save disabled
while invalid), and a "Reset to defaults" button. Changes here and in the form
stay in sync.

The same screen shows **usage so far** (read-only): money spent and attempts
used per step and per shot, e.g. `stills_agent_shot_5: 7`.

---

## 7. Character pool

A library of recurring characters. **Each reel uses one character** (or none),
picked from the pool. The chosen character appears in the pictures of that reel.

Each character has:
- Name (e.g. "Sachin", "Default")
- **Main reference image** (full body) — required
- **Turnaround sheet** (the character from several angles) — optional but recommended
- **Face close-up** — optional; show a note: "Photoreal face close-ups can be
  blocked by the image model. Use an illustrated face."
- Optional short description (e.g. "man in his 30s, round glasses, green jacket")
- Used by: list of projects

Screen functions:
- Grid of character cards with the main image.
- **Add character**: name + drag-and-drop the images into labelled slots.
- **Edit**, **Duplicate**, **Remove** (if projects use it, warn and list them;
  those projects fall back to "No character").
- **Set as default** (star). New projects get this character.
- In a project's settings: pick a character from the pool via a picker with
  thumbnails; "No character" is always an option. Swapping the character on a
  finished project warns that pictures must be remade.

---

## 7b. Style library

An art style is a small JSON file in the `styles/` folder, for example:

```json
{
  "name": "pixel_art",
  "description": "Retro pixel-art illustration with crisp pixel blocks, a limited palette, simplified forms, and game-like environments",
  "palette": ["azure blue", "teal green", "sun yellow", "orange", "cream"],
  "lighting": "simple sprite-style lighting with limited shading",
  "mood": "playful, nostalgic, compact",
  "avoid": ["photorealism", "any text, letters, numbers or logos", "blurry edges"]
}
```

Styles are **not written in the UI**. The operator makes the JSON elsewhere and
drops it in. The UI's job is to add styles, show them clearly, and let the
operator pick one.

Each style card shows:
- A **sample image** of the style (the visual reference), large
- Display name (e.g. "Pixel Art") and the one-line description
- **Palette swatches** made from the palette list
- Mood and lighting as small chips
- "Avoid" list, collapsed
- Used by: list of projects

Sample images: the `styles/` folder already holds a reference sheet, "Modern
Illustration Styles", showing 15 styles as labelled tiles with a sample picture,
5 palette dots and a one-line description each. Ten of these styles have a
JSON today. Each style's card uses its own image, stored next to its JSON with
the same name (`pixel_art.json` + `pixel_art.png`). A style with no image shows
a placeholder built from its palette swatches.

Screen functions:
- Grid of style cards, with a search box.
- **Add style**: a drop zone for the **JSON file** and its **sample image**
  (both at once, or the image added later). The JSON is checked on drop; errors
  are shown in plain words (e.g. "`palette` is missing").
- A style whose name already exists asks: replace or keep both.
- **Replace sample image** and **Remove** per card (removing one that projects
  use warns first and lists them).
- **View reference sheet**: opens the full "Modern Illustration Styles" image,
  to compare all the styles at once.
- In the settings picker, the same cards appear smaller; clicking one selects it.

---

## 8. Cover generation

Lives in the project's **Cover** tab. Only enabled once the reel is finished.

**Settings** (with the same "use defaults" behaviour as the reel):
- Hook text: **Let Claude write it** (default) or **Use my own text** (text box).
- Base artwork: **Let Claude choose** (default) or **pick a shot** from a
  thumbnail strip of the reel's shots.
- Image maker / model: same options as the reel, defaulting to the project's.
- "Make a fresh cover even if one exists" (today's `--force`).

**Result area:**
- The current cover, large, in Instagram preview frames (feed crop 4:5 and grid
  crop 1:1 side by side; the tool already makes a grid preview).
- The hook text and chosen shot used.
- Download button.

**Feedback loop:**
- A text box: "What should change?" (e.g. "make the headline bigger and
  stylish") → **Revise cover**.
- **Version history**: every earlier cover (v1, v2 …) with the feedback that
  produced the next one. Click to compare side by side; "Use this version" to
  restore one.
- Progress and log for a cover run, same style as the reel run (smaller).

---

## 9. Default settings screen

- The full settings panel (section 6) plus cover defaults (section 8).
- These values fill every new project. Changing them does not change existing projects.
- **Presets** (optional, nice-to-have): save the current settings under a name
  (e.g. "Cheap test run: Codex + fast Veo", "Kids book watercolour") and pick a
  preset when creating a project.

---

## 10. Global setup screen

One-time and health settings, not per project:
- Google Cloud project ID, region, storage bucket for video
- API keys / logins status: Google Cloud login, Codex login, Langfuse tracing keys
  (masked inputs; never shown in full)
- **Health check** button: runs the existing pre-flight checks and shows each as
  ✓ / ✗ with a fix hint (e.g. "Run `gcloud auth application-default login`").

---

## 11. States and edge cases to design

- Empty states: no projects yet, no characters yet, no cover yet, no runs yet.
- A run is already going on a project → Run button disabled, show "Running".
- Only one run per project at a time; several projects may run at once (show a
  small "2 runs going" indicator in the sidebar).
- Run stopped by: budget, attempts used up, a vendor error, the operator. Each
  has its own plain-language message and a suggested next action.
- Settings changed after a run: show "Next run will use new settings" and which
  steps they affect.
- Long waits (a single video clip can take minutes): show a heartbeat line
  like "Still waiting for shot 5's video (2m 30s)" so it never looks frozen.
- Browser closed and reopened during a run: the page picks the live run back up.

---

## 12. Design direction

- Calm, tool-like, dark mode and light mode.
- Vertical 9:16 previews everywhere a shot or reel appears.
- Plain language for step names and errors (the terminal already speaks this way).
- Money and percent are always visible while running.

---

## 13. Decisions made

1. "Context type" means **Where the pictures come from**: the screenshot's
   artwork or invented pictures.
2. **One character per reel**, picked from the pool.
3. The run **does not pause by default**. It pauses after the narration only
   when the operator clicks **Pause after narration**, so the narration can be
   read and rewritten before the rest runs.
4. Art styles are **dropped in as JSON + sample image**, not written in the UI;
   the UI shows them with their reference image and lets the operator choose.
