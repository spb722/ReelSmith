"""ReelSmith Studio HTTP server (ui/CONTRACT.md section 3).

    python -m ui.server.app          # http://127.0.0.1:8765

Serves the API under /api, project files under /files, and the built web app
(ui/web/dist) at / when it exists.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ui.server import runner, store
from ui.server.store import StoreError, read_json, write_json

app = FastAPI(title="ReelSmith Studio")
WORDS_PER_MINUTE = 145  # the narrator's pace in the story plans' voice direction


@app.exception_handler(StoreError)
def _store_error(request: Request, exc: StoreError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


def _not_running(name: str, action: str) -> None:
    if runner.live_run(name) or runner.status(name)["state"] == "running":
        raise StoreError(f"A run is going on this project. Stop it before you {action}.", 409)


def _media(name: str, rel: str) -> str | None:
    path = store.project_dir(name) / rel
    return store.versioned_url(name, path) if path.is_file() else None


# -------------------------------------------------------------------- meta

@app.get("/api/meta")
def get_meta():
    return store.meta()


# ---------------------------------------------------------------- projects

def project_summary(name: str) -> dict:
    project = store.existing_project(name)
    settings = store.get_project_settings(name)
    shots = store.screenshot_files(name)
    cover = project / "generated" / "cover" / "cover.png"
    character = store.get_character(settings["character_id"]) if settings["character_id"] else None
    style = store.get_style(settings["style"])
    thumb = store.versioned_url(name, cover) if cover.is_file() else (
        store.file_url(name, f"source_images/{shots[0]}") if shots else None)
    return {
        "name": name, **runner.summary(name),
        "style": settings["style"], "style_name": style["name"] if style else settings["style"],
        "source_mode": settings["source_mode"],
        "character": character["name"] if character else None, "character_id": settings["character_id"],
        "thumbnail_url": thumb, "has_cover": cover.is_file(), "screenshot_count": len(shots),
    }


@app.get("/api/projects")
def list_projects():
    return [project_summary(n) for n in store.list_project_names()]


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    source = body.get("from") or None
    if source:
        store.existing_project(source)
    store.create_project(store.check_name(body.get("name", "")), source)
    return project_summary(body["name"])


@app.get("/api/projects/{name}")
def get_project(name: str):
    project = store.existing_project(name)
    return {**project_summary(name), "settings": store.get_project_settings(name),
            "screenshots": store.screenshots(name), "reel_url": _media(name, "book_reel.mp4"),
            "cover_url": _media(name, "generated/cover/cover.png")}


@app.delete("/api/projects/{name}")
def delete_project(name: str):
    store.existing_project(name)
    _not_running(name, "delete it")
    store.delete_project(name)
    return {"ok": True}


@app.post("/api/projects/{name}/duplicate")
def duplicate_project(name: str, body: dict = Body(...)):
    new_name = store.check_name(body.get("new_name", ""))
    store.duplicate_project(name, new_name)
    return project_summary(new_name)


# ------------------------------------------------------------- screenshots

@app.get("/api/projects/{name}/screenshots")
def list_screenshots(name: str):
    store.existing_project(name)
    return store.screenshots(name)


@app.post("/api/projects/{name}/screenshots")
async def upload_screenshots(name: str, request: Request):
    store.existing_project(name)
    _not_running(name, "change the screenshots")
    form = await request.form()
    uploads = [(f.filename, await f.read()) for f in form.getlist("files") + form.getlist("files[]")
               if hasattr(f, "read")]
    if not uploads:
        raise StoreError("Choose some PNG or JPG screenshots to add.")
    return store.add_screenshots(name, uploads)


@app.put("/api/projects/{name}/screenshots/order")
def reorder_screenshots(name: str, body: dict = Body(...)):
    _not_running(name, "change the screenshots")
    return store.reorder_screenshots(name, list(body.get("order") or []))


@app.delete("/api/projects/{name}/screenshots/{file}")
def delete_screenshot(name: str, file: str):
    store.existing_project(name)
    _not_running(name, "change the screenshots")
    return store.delete_screenshot(name, file)


# ---------------------------------------------------------------- settings

@app.get("/api/projects/{name}/settings")
def get_settings(name: str):
    return {"settings": store.get_project_settings(name), "defaults": store.get_defaults()}


@app.put("/api/projects/{name}/settings")
def put_settings(name: str, body: dict = Body(...)):
    store.existing_project(name)
    store.put_project_settings(name, body)
    return get_settings(name)


@app.get("/api/projects/{name}/config")
def get_config(name: str):
    store.existing_project(name)
    path = runner.runs_dir(name) / "config.json"
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        import json
        text = json.dumps({"limits": {"max_attempts": store.get_project_settings(name)["max_attempts"]}}, indent=2)
    usage = ((read_json(path, {}) or {}) if path.is_file() else {}).get("usage") or {}
    return {"text": text, "usage": usage}


@app.put("/api/projects/{name}/config")
def put_config(name: str, body: dict = Body(...)):
    import json
    store.existing_project(name)
    _not_running(name, "edit the config")
    text = body.get("text")
    if not isinstance(text, str):
        raise StoreError("Send the config as text.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StoreError(f"The config is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}).") from None
    if not isinstance(data, dict):
        raise StoreError("The config must be a JSON object.")
    attempts = (data.get("limits") or {}).get("max_attempts")
    if attempts is not None:
        if not isinstance(attempts, dict):
            raise StoreError("limits.max_attempts must be an object like {\"story_agent\": 4}.")
        settings = store.get_project_settings(name)
        settings["max_attempts"] = {**settings["max_attempts"], **attempts}
        store.put_project_settings(name, settings)
    path = runner.runs_dir(name) / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"text": text}


@app.get("/api/defaults")
def get_defaults():
    return store.get_defaults()


@app.put("/api/defaults")
def put_defaults(body: dict = Body(...)):
    return store.put_defaults(body)


@app.get("/api/presets")
def list_presets():
    return store.list_presets()


@app.post("/api/presets")
def save_preset(body: dict = Body(...)):
    return store.save_preset(body.get("name", ""), body.get("settings") or {})


@app.delete("/api/presets/{preset}")
def delete_preset(preset: str):
    return store.delete_preset(preset)


# --------------------------------------------------------------------- run

@app.post("/api/projects/{name}/run")
def start_run(name: str, body: dict = Body(default={})):
    return runner.start_reel(name, body.get("mode", "resume"), bool(body.get("retry_voice")),
                             body.get("pause_after_narration"))


@app.post("/api/projects/{name}/stop")
def stop_run(name: str):
    store.existing_project(name)
    return runner.stop(name)


@app.post("/api/projects/{name}/pause-after-narration")
def pause_after_narration(name: str, body: dict = Body(...)):
    store.existing_project(name)
    return runner.set_pause_after_narration(name, bool(body.get("enabled")))


@app.get("/api/projects/{name}/status")
def run_status(name: str):
    return runner.status(name)


@app.get("/api/projects/{name}/log")
def run_log(name: str, offset: int = 0):
    store.existing_project(name)
    return runner.read_log(name, offset)


@app.get("/api/projects/{name}/logs")
def run_logs(name: str):
    store.existing_project(name)
    return runner.list_logs(name)


# --------------------------------------------------------------- narration

def _hooks():
    try:
        from orchestrator import ui_hooks
    except ImportError:
        raise StoreError("The narration tools (orchestrator/ui_hooks.py) are not installed yet.", 503) from None
    return ui_hooks


HISTORY_FILE = "ui_narration_history.json"


def _norm(script: str) -> str:
    """The script as ui_hooks.write_narration saves it: whitespace collapsed."""
    return " ".join((script or "").split())


def _history(name: str, script: str, save: bool = False) -> list[dict]:
    """Edit history; any script not in it yet was written by Claude and is added."""
    path = store.project_dir(name) / HISTORY_FILE
    entries = read_json(path, []) or []
    if script and not any(_norm(e.get("script")) == _norm(script) for e in entries):
        entries.append({"id": str(len(entries) + 1), "by": "claude", "script": script, "at": store.now_iso(),
                        "label": "Original (written by Claude)" if not entries else "Rewritten by Claude"})
        save = save or path.is_file()
    if save:
        write_json(path, entries)
    return entries


def _read_narration(name: str) -> dict:
    project = store.existing_project(name)
    data = _hooks().read_narration(project) or {}
    if not data.get("script"):
        raise StoreError("There is no narration yet. It is written in step 2 of a run.", 404)
    return data


def narration_payload(name: str) -> dict:
    data = _read_narration(name)
    script = data.get("script") or ""
    words = len(script.split())
    limits = data.get("story_skill_limits") or {}
    if not limits:
        from orchestrator.contracts.final_story_plan import STORY_LIMITS
        lo, hi, slo, shi = STORY_LIMITS[store.get_project_settings(name)["story_skill"]]
        limits = {"min_words": lo, "max_words": hi, "min_s": slo, "max_s": shi}
    return {
        "script": script, "word_count": words, "est_seconds": round(words * 60 / WORDS_PER_MINUTE),
        "limits": limits,
        "critic": data.get("critic") or {"verdict": None, "issues": []},
        "outline": data.get("outline") or {"hook": "", "main_point": "", "ending": ""},
        "history": [{k: e.get(k) for k in ("id", "label", "script", "at")} for e in _history(name, script)],
        "downstream_done": bool(_hooks().downstream_done(store.project_dir(name))),
    }


def _write_narration(name: str, script: str) -> None:
    _not_running(name, "change the narration")
    try:
        _hooks().write_narration(store.project_dir(name), script)
    except ValueError as exc:
        raise StoreError(str(exc)) from None


@app.get("/api/projects/{name}/narration")
def get_narration(name: str):
    return narration_payload(name)


@app.put("/api/projects/{name}/narration")
def put_narration(name: str, body: dict = Body(...)):
    script = body.get("script")
    if not isinstance(script, str) or not script.strip():
        raise StoreError("The narration must not be empty.")
    script = _norm(script)
    current = _read_narration(name).get("script") or ""
    entries = _history(name, current)
    if script != _norm(current):
        _write_narration(name, script)
        edits = sum(1 for e in entries if e.get("by") == "you")
        entries.append({"id": str(len(entries) + 1), "by": "you", "script": script, "at": store.now_iso(),
                        "label": f"Your edit {edits + 1}"})
        write_json(store.project_dir(name) / HISTORY_FILE, entries)
    return narration_payload(name)


@app.post("/api/projects/{name}/narration/discard")
def discard_narration(name: str):
    current = _read_narration(name).get("script") or ""
    claude = [e for e in _history(name, current, save=True) if e.get("by") == "claude"]
    if claude and _norm(claude[-1]["script"]) != _norm(current):
        _write_narration(name, claude[-1]["script"])
    return narration_payload(name)


@app.post("/api/projects/{name}/narration/restore")
def restore_narration(name: str, body: dict = Body(...)):
    current = _read_narration(name).get("script") or ""
    entry = next((e for e in _history(name, current, save=True) if e.get("id") == str(body.get("id"))), None)
    if entry is None:
        raise StoreError("That version is not in the history.", 404)
    if _norm(entry["script"]) != _norm(current):
        _write_narration(name, entry["script"])
    return narration_payload(name)


@app.post("/api/projects/{name}/narration/continue")
def continue_narration(name: str):
    store.existing_project(name)
    runner.set_flag(name, False)
    return runner.start_reel(name, "resume", pause_after_narration=False)


@app.post("/api/projects/{name}/narration/rewrite")
def rewrite_narration(name: str, body: dict = Body(...)):
    instructions = (body.get("instructions") or "").strip()
    if not instructions:
        raise StoreError("Say what should change, e.g. \"make the ending punchier\".")
    current = _read_narration(name).get("script") or ""
    _history(name, current, save=True)
    return runner.start_reel(name, "resume", pause_after_narration=True, rewrite=instructions)


# ----------------------------------------------------------------- reviews

_ATTEMPT = re.compile(r"^(stills|veo)_agent_shot_(\d+)_attempt_(\d+)\.json$")
_STORY = re.compile(r"^story_agent_attempt_(\d+)\.json$")


def _issue_text(issue) -> str:
    if isinstance(issue, str):
        return issue
    if isinstance(issue, dict):
        head = str(issue.get("dimension") or "").replace("_", " ")
        body = issue.get("evidence") or issue.get("issue") or issue.get("description") or ""
        fix = issue.get("fix")
        text = f"{head.capitalize()}: {body}" if head else str(body)
        return f"{text} Fix: {fix}" if fix else text
    return str(issue)


@app.get("/api/projects/{name}/reviews")
def get_reviews(name: str):
    project = store.existing_project(name)
    folder = runner.runs_dir(name)
    files = sorted(folder.iterdir()) if folder.is_dir() else []
    story = []
    for path in files:
        if m := _STORY.match(path.name):
            attempt = int(m[1])
            out = (read_json(path, {}) or {}).get("structured_output") or {}
            review = (read_json(folder / f"story_agent_attempt_{attempt}_review.json", {}) or {}).get(
                "structured_output") or {}
            story.append({"attempt": attempt, "verdict": review.get("verdict"),
                          "issues": [_issue_text(i) for i in review.get("issues") or []],
                          "script": out.get("narration_script", "")})
    story.sort(key=lambda s: s["attempt"])
    shots: dict[int, dict] = {}
    for path in files:
        if not (m := _ATTEMPT.match(path.name)):
            continue
        number, attempt = int(m[2]), int(m[3])
        out = (read_json(path, {}) or {}).get("structured_output") or {}
        result, failure = out.get("result") or {}, out.get("failure") or {}
        ok = out.get("status") == "SUCCESS"
        comment = result.get("qa_summary") if ok else failure.get("reason")
        shot = shots.setdefault(number, {"number": number, "type": "video" if m[1] == "veo" else "still",
                                         "attempts": []})
        entry = {"attempt": attempt, "verdict": "approve" if ok else "reject",
                 "comments": [comment] if comment else [], "media_url": None,
                 "camera_motion": result.get("camera_motion") or None}
        if ok:
            entry["media_url"] = _media(name, result.get("local_video_path") or result.get("local_image_path") or "")
        shot["attempts"].append(entry)
    for shot in shots.values():
        shot["attempts"].sort(key=lambda a: a["attempt"])
        # Every attempt writes to the same file, so only the newest one still has its picture.
        last = shot["attempts"][-1]
        if last["media_url"] is None:
            rel = f"generated/veo/shot_{shot['number']:02d}.mp4" if shot["type"] == "video" else \
                f"generated/stills/shot_{shot['number']:02d}.png"
            last["media_url"] = _media(name, rel)
    plan = read_json(project / "metadata" / "final_story_plan.json", {}) or {}
    return {"story": story, "final_script": plan.get("narration_script", ""),
            "shots": [shots[n] for n in sorted(shots)]}


# ----------------------------------------------------------------- outputs

@app.get("/api/projects/{name}/outputs")
def get_outputs(name: str):
    project = store.existing_project(name)
    gen = project / "generated"

    def listing(folder: Path, pattern: str) -> list[dict]:
        return [{"name": p.name, "url": store.versioned_url(name, p)} for p in sorted(folder.glob(pattern))]

    story = read_json(project / "metadata" / "final_story_plan.json")
    cues = (read_json(project / "metadata" / "subtitle_cues.json", {}) or {}).get("cues") or []
    clips = []
    for clip in sorted((gen / "veo").glob("*.mp4")):
        previews = sorted((gen / "veo" / "previews").glob(f"{clip.stem}_*.jpg"))
        clips.append({"name": clip.name, "url": store.versioned_url(name, clip),
                      "previews": [store.versioned_url(name, p) for p in previews]})
    return {
        "reel_url": _media(name, "book_reel.mp4"), "audio_url": _media(name, "audio/narration.wav"),
        "script": (story or {}).get("narration_script", ""),
        "subtitles": [{"text": c.get("text", ""), "start": c.get("start_seconds"), "end": c.get("end_seconds")}
                      for c in cues],
        "stills": listing(gen / "stills", "*.png"), "clips": clips,
        "seeds": listing(gen / "veo_seeds", "*.png"),
        "story_plan": story, "visual_plan": read_json(project / "metadata" / "visual_plan.json"),
        "logs": runner.list_logs(name),
    }


# ------------------------------------------------------------------- cover

def cover_payload(name: str) -> dict:
    project = store.existing_project(name)
    folder = project / "generated" / "cover"
    plan = read_json(project / "metadata" / "cover_plan.json", {}) or {}
    history = read_json(folder / "feedback_history.json", []) or []
    by_file = {Path(h.get("previous_cover", "")).name: h for h in history if isinstance(h, dict)}
    # cover_vN.png are the earlier covers; the history entry for cover_vN holds the
    # feedback that replaced it, i.e. the feedback that made version N+1.
    versions = []
    for path in folder.glob("cover_v*.png"):
        m = re.match(r"cover_v(\d+)$", path.stem)
        if not m:
            continue
        n = int(m[1])
        h = by_file.get(f"cover_v{n - 1}.png", {})
        versions.append({"version": n, "url": store.versioned_url(name, path), "feedback": h.get("feedback"),
                         "hook_text": h.get("hook_text"), "shot": h.get("base_shot_sequence"), "current": False})
    current = folder / "cover.png"
    if current.is_file():  # the live cover is the newest version
        n = max([v["version"] for v in versions] or [0]) + 1
        h = by_file.get(f"cover_v{n - 1}.png", {})
        versions.append({"version": n, "url": store.versioned_url(name, current), "feedback": h.get("feedback"),
                         "hook_text": plan.get("hook_text"), "shot": plan.get("base_shot_sequence"), "current": True})
    shots = {}
    for pattern, rx in (("generated/stills/shot_*.png", r"shot_(\d+)$"),
                        ("generated/veo_seeds/shot_*_seed.png", r"shot_(\d+)_seed$")):
        for path in project.glob(pattern):
            if m := re.match(rx, path.stem):
                shots.setdefault(int(m[1]), store.versioned_url(name, path))
    return {
        "enabled": (project / "book_reel.mp4").is_file(), "exists": (folder / "cover.png").is_file(),
        "cover_url": _media(name, "generated/cover/cover.png"), "grid_url": _media(name, "generated/cover/cover_grid.png"),
        "hook_text": plan.get("hook_text"), "shot": plan.get("base_shot_sequence"),
        "versions": sorted(versions, key=lambda v: v["version"]),
        "shots": [{"number": n, "url": shots[n]} for n in sorted(shots)],
        "status": runner.cover_status(name),
    }


@app.get("/api/projects/{name}/cover")
def get_cover(name: str):
    return cover_payload(name)


@app.post("/api/projects/{name}/cover/run")
def run_cover(name: str, body: dict = Body(default={})):
    runner.start_cover(name, hook=(body.get("hook") or "").strip(), shot=body.get("shot"),
                       force=bool(body.get("force")), image_provider=body.get("image_provider") or "")
    return cover_payload(name)


@app.post("/api/projects/{name}/cover/revise")
def revise_cover(name: str, body: dict = Body(...)):
    feedback = (body.get("feedback") or "").strip()
    if not feedback:
        raise StoreError("Say what should change, e.g. \"make the headline bigger\".")
    if not (store.existing_project(name) / "generated" / "cover" / "cover.png").is_file():
        raise StoreError("There is no cover to revise yet. Make one first.")
    runner.start_cover(name, feedback=feedback)
    return cover_payload(name)


@app.post("/api/projects/{name}/cover/restore")
def restore_cover(name: str, body: dict = Body(...)):
    project = store.existing_project(name)
    _not_running(name, "change the cover")
    folder = project / "generated" / "cover"
    chosen = folder / f"cover_v{int(body.get('version') or 0)}.png"
    if not chosen.is_file():
        raise StoreError("That cover version does not exist.", 404)
    current = folder / "cover.png"
    if current.is_file():  # keep the current one as a new version so nothing is lost
        numbers = [int(m[1]) for p in folder.glob("cover_v*.png") if (m := re.match(r"cover_v(\d+)$", p.stem))]
        shutil.copyfile(current, folder / f"cover_v{max(numbers) + 1}.png")
    shutil.copyfile(chosen, current)
    try:
        from orchestrator.tools.cover_tools import write_grid_preview
        write_grid_preview(current, folder / "cover_grid.png")
    except Exception:  # the grid is a preview; a stale one is not worth failing the restore
        pass
    return cover_payload(name)


# -------------------------------------------------------------- characters

async def _form(request: Request) -> tuple[dict, dict]:
    form = await request.form()
    fields, files = {}, {}
    for key, value in form.multi_items():
        if hasattr(value, "read"):
            data = await value.read()
            if data:
                files[key] = data
        else:
            fields[key] = value
    return fields, files


@app.get("/api/characters")
def list_characters():
    return store.list_characters()


@app.post("/api/characters")
async def create_character(request: Request):
    fields, files = await _form(request)
    return store.create_character(fields.get("name", ""), fields.get("description", ""),
                                  {slot: files.get(slot) for slot in store.SLOTS})


@app.put("/api/characters/{cid}")
async def update_character(cid: str, request: Request):
    fields, files = await _form(request)
    return store.update_character(cid, fields.get("name"), fields.get("description"),
                                  {slot: files.get(slot) for slot in store.SLOTS})


@app.delete("/api/characters/{cid}")
def delete_character(cid: str):
    return {"ok": True, "affected": store.delete_character(cid)}


@app.post("/api/characters/{cid}/default")
def default_character(cid: str):
    return store.set_default_character(cid)


@app.post("/api/characters/{cid}/duplicate")
def duplicate_character(cid: str):
    return store.duplicate_character(cid)


@app.get("/api/characters/{cid}/image/{slot}")
def character_image(cid: str, slot: str):
    path = store.character_image_path(cid, slot)
    if path is None:
        raise StoreError("No such image.", 404)
    return FileResponse(path)


# ------------------------------------------------------------------ styles

@app.get("/api/styles")
def list_styles():
    return store.list_styles()


@app.post("/api/styles")
async def upload_style(request: Request, on_conflict: str | None = None):
    fields, files = await _form(request)
    raw = files.get("json") or (fields.get("json") or "").encode()
    if not raw:
        raise StoreError("Drop the style's JSON file.")
    try:
        return store.add_style(raw, files.get("image"), on_conflict)
    except StoreError as exc:
        if exc.status == 409:
            return JSONResponse(status_code=409, content={"detail": str(exc), "conflict": True})
        raise


@app.put("/api/styles/{sid}/image")
async def style_image_upload(sid: str, request: Request):
    _, files = await _form(request)
    return store.set_style_image(sid, files.get("image"))


@app.delete("/api/styles/{sid}")
def delete_style(sid: str):
    return {"ok": True, "affected": store.delete_style(sid)}


@app.get("/api/styles/{sid}/image")
def style_image(sid: str):
    path = store.style_image_path(sid) if store.STYLE_ID_RE.match(sid) else None
    if path is None:
        raise StoreError("This style has no sample image.", 404)
    return FileResponse(path)


@app.get("/api/styles-reference-sheet")
def style_reference_sheet():
    path = store.reference_sheet_path()
    if path is None:
        raise StoreError("There is no reference sheet in styles/.", 404)
    return FileResponse(path)


# ------------------------------------------------------------------- setup

def setup_payload() -> dict:
    return {**store.setup_values(), "logins": runner.logins(), "keys": store.secret_keys()}


@app.get("/api/setup")
def get_setup():
    return setup_payload()


@app.put("/api/setup")
def put_setup(body: dict = Body(...)):
    store.write_setup(body)
    return setup_payload()


@app.post("/api/setup/health")
def health():
    return runner.health()


# ------------------------------------------------------------------- files

@app.get("/files/{name}/{path:path}")
def project_file(name: str, path: str):
    project = store.existing_project(name).resolve()
    target = (project / path).resolve()
    if not target.is_relative_to(project) or target == project:
        raise StoreError("That file is outside the project.", 403)
    if not target.is_file():
        raise StoreError("No such file.", 404)
    return FileResponse(target)


DIST = store.REPO / "ui" / "web" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("REELSMITH_PORT", "8765")))


if __name__ == "__main__":
    main()
