# ReelSmith Studio — build contract

Shared agreement for everyone building the UI. If you need to change something
here, keep it backwards compatible and note it at the bottom under "Changes".

- Product spec: `docs/ui-spec.md` (what each screen does).
- Visual design reference (the mockup to match): `ui_design/extracted/`
  - `app_markup.html` — the full mockup markup. Sections are marked with
    comments: `<!-- HOME -->`, `<!-- WIZARD HEAD -->`, `<!-- PROJECT HEAD -->`,
    `<!-- DEFAULTS HEAD -->`, `<!-- SHARED: SCREENSHOTS -->`,
    `<!-- SHARED: SETTINGS -->`, `<!-- DEFAULTS: COVER -->`, `<!-- RUN TAB -->`,
    `<!-- OVERVIEW -->`, `<!-- REVIEWS -->`, `<!-- OUTPUTS -->`, `<!-- COVER -->`,
    `<!-- CHARACTERS -->`, `<!-- STYLES -->`, `<!-- SETUP -->`, `<!-- WIZARD FOOTER -->`.
    It uses a template language: `{{ x }}` bindings, `<sc-if value=…>`,
    `<sc-for list=… as=…>`, `sc-camel-on-click`. Translate it to React.
  - `settings_panel.dc.html` — the shared Settings panel component.
  - `app_logic.js` — the mockup's view logic (state, labels, derived values).
    Read it for copy text, states and behaviour. Its data is fake.
  - `mock_data.js` (`window.RS_DATA`) and `mock_run_simulation.js` — fake data.
    Useful for shapes and wording only; the real app uses the API below.
  - `broadsheet_tokens.css`, `app_extra.css` — the design system CSS
    (colour tokens, `.btn`, `.seg`, `.tag`, `.card`, fonts, dark theme).
  - `*_shot*.jpg` — sample images.
- Icons: Phosphor duotone, class names like `ph-duotone ph-plus`
  (npm `@phosphor-icons/web`, imported once in `main.jsx`).

**Never start a real reel or cover run** (`python -m orchestrator.run` /
`orchestrator.cover`) while building or testing. They spend real money on
Claude, Gemini and Veo. The server runs the pipeline through a configurable
command (`REELSMITH_RUN_CMD`, see below); tests use the fake runner.

Python: `/opt/homebrew/anaconda3/envs/kayak-video/bin/python` (call it `PY`).
Run everything from the repo root.

---

## 1. Folder layout

```
ui/
  CONTRACT.md            this file
  README.md              how to start the studio
  __init__.py
  server/                FastAPI backend (Python)
    __init__.py
    app.py               FastAPI app, routes, static files; `PY -m ui.server.app`
    store.py             projects, settings, defaults, presets, characters, styles on disk
    runner.py            starts/stops pipeline subprocesses, reads status
    fake_runner.py       simulates a run for tests and demos (no vendor calls)
    requirements.txt     fastapi, uvicorn, python-multipart
    tests/               pytest tests (use FastAPI TestClient + fake runner)
  data/                  UI-owned state (git-ignored except .gitkeep)
    defaults.json        default settings for new projects
    presets.json         named settings presets
  web/                   Vite + React (plain JavaScript, JSX)
    package.json, vite.config.js (dev proxy /api and /files -> 127.0.0.1:8765)
    index.html
    src/
      main.jsx           imports css + icons, renders <App/>
      App.jsx            window shell: title bar, sidebar, theme, router
      router.js          tiny hash router: useRoute(), navigate(path)
      api.js             every fetch call in one place (section 3)
      hooks.js           usePoll(fn, ms), useAsync(fn, deps)
      styles/broadsheet.css   copied from design tokens
      styles/app.css          app-level layout classes
      components/        shared pieces (see ownership table)
      screens/           one file per screen/tab
```

Routes (hash router):

| Route | Screen file |
| --- | --- |
| `#/` | `screens/Home.jsx` |
| `#/new` | `screens/Wizard.jsx` |
| `#/p/:name` and `#/p/:name/:tab` | `screens/project/ProjectPage.jsx` (tabs: `overview`, `screenshots`, `settings`, `run`, `reviews`, `outputs`, `cover`) |
| `#/p/:name/narration` | `screens/project/NarrationEditor.jsx` (rendered inside ProjectPage) |
| `#/characters` | `screens/Characters.jsx` |
| `#/styles` | `screens/Styles.jsx` |
| `#/defaults` | `screens/Defaults.jsx` |
| `#/setup` | `screens/Setup.jsx` |

Project tab files: `screens/project/OverviewTab.jsx`, `ScreenshotsTab.jsx`,
`SettingsTab.jsx`, `RunTab.jsx`, `ReviewsTab.jsx`, `OutputsTab.jsx`, `CoverTab.jsx`.

Shared components (each has one owner; others import them):

| File | Props |
| --- | --- |
| `components/ScreenshotDrop.jsx` | `{ files, onAdd(FileList), onRemove(file), onReorder(order[]), readOnlyWarning? }` — drag-drop + reorderable strip |
| `components/SettingsPanel.jsx` | `{ value, defaults, meta, onChange(next), showRawConfig?, projectName? }` — full settings form (Basic/Advanced) with "changed" dots and per-field reset |
| `components/StatusBadge.jsx` | `{ status, percent? }` |
| `components/Modal.jsx` | `{ open, title, onClose, children, footer }` |
| `components/ConfirmButton.jsx` | `{ label, confirmText, onConfirm, className }` |
| `components/Toast.jsx` | `useToast()` → `toast(message, kind)` |
| `components/StylePicker.jsx` | `{ value, onChange, styles }` |
| `components/CharacterPicker.jsx` | `{ value, onChange, characters }` (includes "No character") |
| `components/Stepper.jsx`, `components/ShotGrid.jsx`, `components/LogPanel.jsx` | used by RunTab |

---

## 2. Data on disk

- Projects: `projects/<name>/` (existing layout: `source_images/`, `metadata/`,
  `generated/`, `audio/`, `orchestrator_runs/`, `book_reel.mp4`).
  - `projects/<name>/ui_settings.json` — the project's full settings object (section 4).
  - `projects/<name>/ui_narration_history.json` — narration edit history.
  - Screenshot order = file names sorted; the server renames to
    `01_<orig>.png`, `02_…` when reordering so the pipeline reads them in order.
- Defaults: `ui/data/defaults.json` once saved; until then built-in defaults with the repo `.env` (and server environment) layered on top, so a UI run matches a terminal run. A project with no `ui_settings.json` takes its `story_skill` from the length of its saved story plan.
- Presets: `ui/data/presets.json` — `[{ "name", "settings" }]`.
- Characters: `assets/characters/<id>/character.json`
  `{ "id", "name", "description", "is_default", "main": "main.png", "turnaround": "turnaround.png"|null, "face": "face.png"|null }`
  plus the image files. On first start, if the folder is empty, seed one
  character `default` from `assets/character/character.png` and
  `assets/character/character_turnaround.png` (copy the files; do not move them).
- Styles: `styles/<id>.json` (existing format: `name`, `description`,
  `palette` (list of colour words), `lighting`, `mood`, `avoid`, optional
  `display_name`) + optional sample image `styles/<id>.png` (or `.jpg`).
  The combined reference sheet is `styles/reference_sheet.png`.

## 3. HTTP API (base `/api`, JSON unless noted; server on 127.0.0.1:8765)

Errors: HTTP 4xx/5xx with `{ "detail": "plain words message" }`.

### Meta
- `GET /api/meta` → `{ styles:[{id,name}], characters:[{id,name}], story_skills:[{id,label,words,seconds}], source_modes:[{id,label}], voices:[str], claude_models:[str], image_providers:["gemini","codex"], image_models:[str], veo_models:[str], veo_resolutions:["720p","1080p"] }`

### Projects
- `GET /api/projects` → `[ProjectSummary]`
  `ProjectSummary = { name, status: "draft"|"running"|"paused"|"stopped"|"finished", percent, stop_reason, last_run_at, duration_s, spent_usd, budget_usd, style, character, thumbnail_url, has_cover, screenshot_count }`
- `POST /api/projects` `{ name, from?: null|"<project name>" }` → `ProjectSummary` (409 if exists; 400 bad name — only `[A-Za-z0-9_-]`)
- `GET /api/projects/{name}` → `ProjectSummary & { settings, screenshots:[Screenshot], reel_url, cover_url }`
- `DELETE /api/projects/{name}` → `{ ok }` (refuse while running)
- `POST /api/projects/{name}/duplicate` `{ new_name }` → `ProjectSummary` (copies settings + screenshots only)

### Screenshots (`Screenshot = { file, url }`)
- `GET /api/projects/{name}/screenshots` → `[Screenshot]` in order
- `POST /api/projects/{name}/screenshots` multipart `files[]` → `[Screenshot]` (PNG/JPG only, duplicates by content hash rejected with a message in `rejected:[{file,reason}]`; response `{ screenshots, rejected }`)
- `PUT /api/projects/{name}/screenshots/order` `{ order:[file] }` → `[Screenshot]`
- `DELETE /api/projects/{name}/screenshots/{file}` → `[Screenshot]`

### Settings
- `GET /api/projects/{name}/settings` → `{ settings, defaults }`
- `PUT /api/projects/{name}/settings` `settings` → `{ settings, defaults }` (validated; 400 with message)
- `GET /api/projects/{name}/config` → `{ text }` raw `orchestrator_runs/config.json` (or a generated one from settings if missing) plus `{ usage }`
- `PUT /api/projects/{name}/config` `{ text }` → `{ text }` (must be valid JSON; `limits.max_attempts` is synced back into settings)
- `GET /api/defaults` → `settings`; `PUT /api/defaults` `settings` → `settings`
- `GET /api/presets` → `[{name, settings}]`; `POST /api/presets` `{name, settings}`; `DELETE /api/presets/{name}`

### Run
- `POST /api/projects/{name}/run` `{ mode: "resume"|"start_over", retry_voice?: bool, pause_after_narration?: bool }` → `RunStatus` (409 if already running; 400 if no screenshots). `start_over` moves `metadata/ generated/ audio/ orchestrator_runs/ book_reel.mp4` into `projects/<name>/_archive/<timestamp>/` first.
- `POST /api/projects/{name}/stop` → `RunStatus`
- `POST /api/projects/{name}/pause-after-narration` `{ enabled }` → `RunStatus` (creates/removes the flag file; 409 "Too late for this run" once step 3 started)
- `GET /api/projects/{name}/status` → `RunStatus`
- `GET /api/projects/{name}/log?offset=N` → `{ text, offset, name }` (current or latest run log, from byte offset)
- `GET /api/projects/{name}/logs` → `[{ name, url, at }]`

`RunStatus = { state: "idle"|"running"|"paused"|"stopped"|"finished", kind: "reel", percent, step, steps:[Step], shots:[Shot], spent_usd, budget_usd, elapsed_s, waiting_s, started_at, stop_reason, stop_kind: null|"attempts"|"budget"|"vendor"|"operator", pause_after_narration, log_tail:[str] }`
`Step = { index (1-6), name, state: "waiting"|"running"|"retrying"|"done"|"failed"|"waiting_for_you", attempt, max_attempts, duration_s, cost_usd }`
`Shot = { number, type: "still"|"video", state: "queued"|"making"|"reviewing"|"redo"|"approved"|"failed", attempt, max_attempts, preview_url, prompt }`

### Narration
- `GET /api/projects/{name}/narration` → `{ script, word_count, est_seconds, limits:{min_words,max_words,min_s,max_s}, critic:{verdict, issues:[str]}, outline:{hook, main_point, ending}, history:[{id,label,script,at}], downstream_done: bool }`
- `PUT /api/projects/{name}/narration` `{ script }` → same (records history entry)
- `POST /api/projects/{name}/narration/discard` → same (restore Claude's original)
- `POST /api/projects/{name}/narration/restore` `{ id }` → same
- `POST /api/projects/{name}/narration/continue` → `RunStatus` (resume the run from step 3)
- `POST /api/projects/{name}/narration/rewrite` `{ instructions }` → `RunStatus` (re-runs step 2 with the instructions, then pauses again)

### Reviews
- `GET /api/projects/{name}/reviews` → `{ story:[{attempt, verdict, issues:[str], script}], final_script, shots:[{number, type, attempts:[{attempt, verdict, comments:[str], media_url, camera_motion?}]}] }`

### Outputs
- `GET /api/projects/{name}/outputs` → `{ reel_url, audio_url, script, subtitles:[{text,start,end}], stills:[{name,url}], clips:[{name,url,previews:[url]}], seeds:[{name,url}], story_plan, visual_plan, logs:[{name,url,at}] }`
- `GET /files/{name}/{path}` — raw project file (images, video, audio, json, logs). Must refuse paths outside the project.

### Cover
- `GET /api/projects/{name}/cover` → `{ enabled (reel finished), exists, cover_url, grid_url, hook_text, shot, versions:[{version, url, feedback, hook_text, shot}], shots:[{number,url}], status: RunStatus-like {state, percent, log_tail, stop_reason} }`
- `POST /api/projects/{name}/cover/run` `{ hook?: str, shot?: int, force?: bool, image_provider?: str }` → cover status
- `POST /api/projects/{name}/cover/revise` `{ feedback }` → cover status
- `POST /api/projects/{name}/cover/restore` `{ version }` → cover payload

### Characters (`Character = { id, name, description, is_default, main_url, turnaround_url, face_url, used_by:[project] }`)
- `GET /api/characters` → `[Character]`
- `POST /api/characters` multipart `name, description, main (file, required), turnaround?, face?` → `Character`
- `PUT /api/characters/{id}` multipart (any subset) → `Character`
- `DELETE /api/characters/{id}` → `{ ok, affected:[project] }` (affected projects fall back to no character)
- `POST /api/characters/{id}/default` → `[Character]`
- `POST /api/characters/{id}/duplicate` → `Character`
- `GET /api/characters/{id}/image/{slot}` — slot `main|turnaround|face`

### Styles (`Style = { id, name, description, palette:[str], lighting, mood, avoid:[str], image_url|null, used_by:[project] }`)
- `GET /api/styles` → `[Style]`
- `POST /api/styles` multipart `json (file, required), image?` `?on_conflict=replace|keep_both` → `Style` (400 plain-words validation message, 409 `{detail, conflict:true}` when the id exists and no on_conflict given)
- `PUT /api/styles/{id}/image` multipart `image` → `Style`
- `DELETE /api/styles/{id}` → `{ ok, affected:[project] }`
- `GET /api/styles/{id}/image`, `GET /api/styles-reference-sheet` — images

### Setup
- `GET /api/setup` → `{ project_id, location, gcs_bucket_uri, logins:[{name, ok, hint}], keys:[{name, set: bool, masked}] }`
- `PUT /api/setup` `{ project_id, location, gcs_bucket_uri }` → same (writes those three keys to the repo `.env`, preserving every other line)
- `POST /api/setup/health` → `[{ name, ok, detail, hint }]` (runs the existing preflight checks; never makes paid calls)

## 4. Settings object

```json
{
  "source_mode": "content_and_images",
  "story_skill": "story-craft",
  "style": "flat_editorial",
  "character_id": "default",
  "voice": "Gacrux",
  "pause_after_narration": false,
  "claude_model": "claude-opus-5",
  "reviewer_model": "claude-opus-5",
  "image_provider": "gemini",
  "image_fallback_provider": "",
  "image_model": "gemini-3.1-flash-image",
  "veo_model": "veo-3.1-fast-generate-001",
  "veo_resolution": "720p",
  "max_budget_usd": 40.0,
  "max_attempts": { "asset_analyst": 4, "story_agent": 4, "visual_agent": 4, "voice_agent": 3, "veo_agent": 3, "stills_agent": 3 },
  "claude_shot_attempt_cap_usd": 3.0,
  "claude_reviewer_cap_usd": 1.0,
  "shot_concurrency": 4,
  "veo_concurrency": 3,
  "gemini_image_concurrency": 2,
  "codex_image_concurrency": 1,
  "codex_image_timeout_seconds": 900,
  "veo_poll_seconds": 15,
  "veo_max_poll_seconds": 900,
  "image_call_cost_usd": 0.04,
  "veo_call_cost_usd": 1.2,
  "cover": { "hook_mode": "claude", "shot_mode": "claude", "image_provider": "" }
}
```

`character_id: null` means "No character". Built-in default values come from
`orchestrator/settings.py` (import its `DEFAULT_*` constants; do not copy numbers).

How a run gets its settings (server `runner.py`): environment variables for the
subprocess (names exactly as read in `orchestrator/settings.py`: `SOURCE_MODE`,
`STORY_SKILL`, `CLAUDE_MODEL`, `IMAGE_PROVIDER`, `IMAGE_FALLBACK_PROVIDER`,
`IMAGE_MODEL`, `VEO_MODEL`, `VEO_RESOLUTION`, `MAX_BUDGET_USD`,
`CLAUDE_SHOT_ATTEMPT_CAP_USD`, `CLAUDE_REVIEWER_CAP_USD`, `SHOT_CONCURRENCY`,
`VEO_CONCURRENCY`, `GEMINI_IMAGE_CONCURRENCY`, `CODEX_IMAGE_CONCURRENCY`,
`CODEX_IMAGE_TIMEOUT_SECONDS`, `VEO_POLL_SECONDS`, `VEO_MAX_POLL_SECONDS`,
`IMAGE_CALL_COST_USD`, `VEO_CALL_COST_USD`, `TTS_VOICE_NAME`,
`CHARACTER_REFERENCE_PATH`, `CHARACTER_TURNAROUND_REFERENCE_PATH`,
`CHARACTER_FACE_REFERENCE_PATH` (absolute paths; empty string = no character),
plus the critic/reviewer model variable if settings.py reads one), the
`--style` argument when `source_mode == "content"`, and
`limits.max_attempts` written into `orchestrator_runs/config.json` before the run
(keep its `usage` block untouched).

Command: `REELSMITH_RUN_CMD` env var on the server (default
`<PY> -m orchestrator.run`), cover `REELSMITH_COVER_CMD` (default
`<PY> -m orchestrator.cover`). Tests and demos set both to
`<PY> -m ui.server.fake_runner` (reel) / `<PY> -m ui.server.fake_runner --cover`.
With `REELSMITH_ROOT` set, both must be set too (the server refuses with 409
rather than run the real pipeline on the repo's own projects). Every run the
server starts gets `REELSMITH_UI=1`.
Subprocess cwd = repo root; it gets `--project <name>` like the real CLI.
One process per project at a time; several projects may run at once. The
server keeps a pid file `projects/<name>/orchestrator_runs/ui_run.pid` so a
restarted server can still see and stop a live run.

## 5. Pipeline ↔ UI files (written by the pipeline, read by the server)

`projects/<name>/orchestrator_runs/status.json`, rewritten atomically on every
progress event by `orchestrator/progress.py`:

```json
{
  "kind": "reel",
  "state": "running | paused_for_narration | stopped | finished",
  "percent": 42,
  "step": 3,
  "steps": [{ "index": 1, "name": "Looking at your screenshots", "state": "waiting|running|retrying|done|failed|waiting_for_you", "attempt": 1, "max_attempts": 4, "started_at": "ISO", "finished_at": "ISO|null", "cost_usd": 0.31 }],
  "shots": [{ "number": 1, "type": "still|video", "state": "queued|making|reviewing|redo|approved|failed", "attempt": 1, "max_attempts": 3, "preview": "generated/stills/shot_01.png|null", "prompt": "…" }],
  "spent_usd": 3.2,
  "budget_usd": 40.0,
  "started_at": "ISO",
  "updated_at": "ISO",
  "stop_reason": null,
  "stop_kind": null
}
```

The cover run writes `orchestrator_runs/cover_status.json` with the same basic
fields (`kind: "cover"`, `state`, `percent`, `stop_reason`).

Narration checkpoint: at the end of step 2, `orchestrator/run.py` pauses when
`REELSMITH_UI=1` (only UI-started runs; its SIGTERM handler is also UI-only) and
the file `projects/<name>/orchestrator_runs/pause_after_narration.flag` exists
(it checks at that moment, so the flag can be created while steps 1-2 run).
Pausing = write status `paused_for_narration` with step 2 `waiting_for_you`,
log a line, exit with code 0. Running again resumes from step 3.
`STORY_REWRITE_INSTRUCTIONS` env var = redo step 2 using the current script
plus these instructions (then the flag pauses again).

`orchestrator/ui_hooks.py` (pipeline-side helpers the server imports):
- `read_narration(project_dir: Path) -> dict` → `{ script, critic:{verdict, issues}, outline:{hook, main_point, ending}, story_skill_limits:{min_words,max_words,min_s,max_s} }`
- `write_narration(project_dir: Path, script: str) -> None` — updates the story plan so a resume uses this script, and removes everything downstream of step 2 (shot plan, voice, subtitles, clips, stills, final reel) so they are remade.
- `downstream_done(project_dir: Path) -> bool` — whether any step after 2 already produced output.
- `pause_flag_path(project_dir: Path) -> Path`

---

## Changes

- **Pipeline side (section 5), from the pipeline agent:**
  - The server can read `status.json` as soon as a run starts. It is refreshed on every progress event and on every log line, so `spent_usd` and `updated_at` stay current. When the process exits, a state still `running` becomes `finished` (exit 0) or `stopped`.
  - **Steps:** there is no `duration_s` in the file. Work it out from `started_at` and `finished_at`. `attempt` counts tries used so far, including earlier runs. `attempt`/`max_attempts` are `null` for steps 5 and 6, because shots count their own tries. A second or later try shows the step as `retrying`.
  - **Shots:** `shots` fills in when step 5 starts. `prompt` is the visual plan's `shot_goal`, because the exact image prompt is never saved. `preview` is the approved clip or still path, relative to the project.
  - **`stop_kind`:** `null` for halts that have no kind, such as settings, preflight or a crash.
  - **Stop (SIGTERM):** SIGTERM sets `stopped` / `operator`, "Stopped by you", and the process exits 143. Child Claude CLI processes may outlive it, so the server should start runs with `start_new_session=True` and stop them with `os.killpg`.
  - **Pause flag:** the run pauses only when step 2 actually wrote the story plan in this run. The flag can stay in place: a resume skips step 2 and does not pause again. A rewrite writes a new plan, so it pauses again.
  - **Rewrite (`STORY_REWRITE_INSTRUCTIONS`):**
    - The old plan and everything made after step 2 move to `_archive/narration_rewrite_<ts>/`.
    - The story agent gets a fresh set of tries on top of the ones already used.
    - If the rewrite fails, the old plan is put back.
  - **`read_narration`:** also returns `word_count`. The limits come from the project's `ui_settings.json` `story_skill`, or from `STORY_SKILL` if that is missing.
  - **`write_narration`:**
    - It splits the edited script across the existing scenes, re-estimates scene lengths, and clears any `impact_text` that is no longer in the script. It then checks the plan with the real contract and raises `ValueError` with a plain message (for example a wrong word count). An unchanged script is a no-op.
    - Everything after step 2 is moved to `_archive/narration_edit_<ts>/`, not deleted. That includes the attempt files for steps 3 to 5, and their try counters in `config.json` are reset. Spend is kept. The cover (`generated/cover/`) is left alone.
    - To check the plan it sets `STORY_SKILL` briefly, so the server should not run two of these at the same time.
  - **Extra helpers:** `ui_hooks.archive_downstream(project_dir, label=...)` and the constants `STATUS_FILENAME` and `PAUSE_FLAG_NAME`.
  - **`cover_status.json`:** `{kind:"cover", state: running|finished|stopped, percent (0, then 50 once the plan is saved, then 100), stop_reason, stop_kind, started_at, updated_at}`.
- **Integration pass:**
  - `GET /api/projects/{name}/cover` `versions` now also lists the live `cover.png` as the newest version, and every entry has `current: bool`. A version's `feedback` is the feedback that produced it (the first cover has none). Restoring the current version is not possible (it has no `cover_vN.png`).
  - `GET /api/projects/{name}/log` always reads the latest *reel* log (the Run tab's). The cover's progress lines come from the cover payload's `status.log_tail`.
  - `orchestrator/styles.py` `Style` now accepts the optional `display_name` field, so a style uploaded with one also works in a real run.
  - A narration edit outside the story skill's word range is refused (400) because the pipeline's story plan contract rejects it; the editor warns before saving.
