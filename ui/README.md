# ReelSmith Studio

A local web app for book_reels: create a project, drop in screenshots, pick
settings, run the pipeline, watch it, read the reviews, make the cover.

`PY` below is `/opt/homebrew/anaconda3/envs/kayak-video/bin/python`. Run every
command from the repo root:

```sh
cd /Users/sachinpb/PycharmProjects/book_reels
PY=/opt/homebrew/anaconda3/envs/kayak-video/bin/python
```

## First time

```sh
$PY -m pip install -r ui/server/requirements.txt
(cd ui/web && npm install)
```

## Start it (one command)

```sh
(cd ui/web && npm run build) && $PY -m ui.server.app
```

Then open http://127.0.0.1:8765. The build is only needed again after
changing something in `ui/web/src`; otherwise `$PY -m ui.server.app` alone is
enough. The server shows the built app from `ui/web/dist` at `/`, the API at
`/api` and project files at `/files`. Set `REELSMITH_PORT` to use another port.

**Runs started here are real.** Each one calls Claude, Gemini and Veo and
spends money, the same as `python -m orchestrator.run`.

## Dev mode (live reload)

Use two terminals:

```sh
$PY -m ui.server.app                  # API on :8765
(cd ui/web && npm run dev)            # UI on http://localhost:5173 (sends /api and /files on to :8765)
```

## Demo with the fake runner (spends nothing)

The fake runner acts out a whole run in about 30 seconds and makes no vendor
calls. It writes the same files the pipeline writes: the log, `status.json`,
story and shot attempts, stills, previews, a reel and a cover. It also pauses
after the narration when asked.

The safest demo also uses a throwaway folder, so the real `projects/` is
never touched:

```sh
DEMO=/tmp/reelsmith_demo
mkdir -p $DEMO/projects $DEMO/assets
cp -R styles $DEMO/styles && cp -R assets/character $DEMO/assets/character
(cd ui/web && npm run build)
REELSMITH_ROOT=$DEMO \
REELSMITH_RUN_CMD="$PY -m ui.server.fake_runner" \
REELSMITH_COVER_CMD="$PY -m ui.server.fake_runner --cover" \
REELSMITH_CAPTION_CMD="$PY -m ui.server.fake_runner --caption" \
FAKE_RUN_SECONDS=40 \
$PY -m ui.server.app
```

Drop the `REELSMITH_ROOT=$DEMO` line to demo on your real projects list (fake
runs then write into the real `projects/<name>/` folders, so use a new project).

Options:

- `FAKE_RUN_SECONDS`: how long a whole reel run takes. The default is 30.
- `FAKE_FAIL`: set to `attempts`, `budget` or `vendor` to make the run stop for
  that reason. It stops that way once, so the next Resume finishes.
- `REELSMITH_ROOT=/some/folder`: use a different folder for `projects/`,
  `styles/`, `assets/` and `ui/data/`, so a demo never touches the real
  projects. The folder must contain `styles/*.json`. For a default character
  it also needs `assets/character/character.png`. Only use it with the fake
  runner: the real pipeline always works in the repo's own `projects/`.

## Where things live

| What | Where |
| --- | --- |
| A project's settings | `projects/<name>/ui_settings.json` |
| Narration edit history | `projects/<name>/ui_narration_history.json` |
| The live run's process id | `projects/<name>/orchestrator_runs/ui_run.pid` |
| The run's console output | `projects/<name>/orchestrator_runs/ui_last_output.txt` |
| "Start over" copies of old outputs | `projects/<name>/_archive/<timestamp>/` |
| Defaults and presets | `ui/data/defaults.json`, `ui/data/presets.json` |
| Characters | `assets/characters/<id>/` (the first start copies `assets/character/` in as "Default") |
| Art styles | `styles/<id>.json` plus `styles/<id>.png` |

Projects made before the studio have no `ui_settings.json`. They are shown with
the default settings plus the attempt limits from their `config.json`. They are
only changed when you save their settings or run them.

The "Longer video shots" switch (Settings, Models) is `veo_extend_long_shots`, passed to runs as
`VEO_EXTEND_LONG_SHOTS` (default off). It applies to new shot plans only.

## Tests

```sh
$PY -m pytest ui/server/tests orchestrator/tests -q
```

The tests use a temporary folder and the fake runner. They never touch the real
projects and never start a real run.
