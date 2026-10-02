"""Starts and stops pipeline subprocesses and turns their files into RunStatus.

A run is `REELSMITH_RUN_CMD` (default `<python> -m orchestrator.run`) with
`--project <name>`, cwd = repo root, settings passed as environment variables.
`orchestrator_runs/ui_run.pid` records the live process so a restarted server
still sees and can stop it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ui.server import store
from ui.server.store import StoreError, read_json, write_json

STEP_NAMES = [
    "Looking at your screenshots",
    "Writing the narration",
    "Planning the shots",
    "Recording the voice and building subtitles",
    "Making the video clips and still pictures",
    "Putting the final video together",
]
STEP_ATTEMPT_KEYS = ["asset_analyst", "story_agent", "visual_agent", "voice_agent", "stills_agent", None]
STATE_MAP = {"running": "running", "paused_for_narration": "paused", "stopped": "stopped", "finished": "finished"}
PID_FILE = "ui_run.pid"
FLAG_FILE = "pause_after_narration.flag"
# A status.json that says "running" with no process of ours behind it is a run
# started from the terminal, if the pipeline touched it this recently.
TERMINAL_RUN_FRESH_S = 90

_procs: dict[str, subprocess.Popen] = {}
_ended: set[str] = set()  # projects whose run, started by this server, has exited
_lock = threading.RLock()  # re-entrant: start_reel holds it around _spawn


def runs_dir(name: str) -> Path:
    return store.project_dir(name) / "orchestrator_runs"


def pause_flag(name: str) -> Path:
    return runs_dir(name) / FLAG_FILE


def _parse_iso(value) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


# ------------------------------------------------------------- processes

def _pid_alive(pid: int, name: str) -> bool:
    proc = _procs.get(name)
    if proc is not None and proc.pid == pid:
        return proc.poll() is None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    # Guard against pid reuse after a reboot: the command must name this project.
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return f"--project {name}" in cmd or f"--project={name}" in cmd


def live_run(name: str) -> dict | None:
    """The pid file's content if its process is still going, else None (and the file is removed)."""
    path = runs_dir(name) / PID_FILE
    info = read_json(path)
    if not isinstance(info, dict) or not isinstance(info.get("pid"), int):
        return None
    if _pid_alive(info["pid"], name):
        return info
    path.unlink(missing_ok=True)
    if _procs.pop(name, None) is not None:
        _ended.add(name)
    return None


def _command(env_var: str, module_args: list[str]) -> list[str]:
    raw = os.environ.get(env_var)
    if not raw and os.environ.get("REELSMITH_ROOT"):
        # The real pipeline always works on the repo's projects/, not REELSMITH_ROOT's.
        raise StoreError(f"REELSMITH_ROOT is set, so set {env_var} too (e.g. the fake runner); "
                         "the real pipeline would run on the repo's own projects.", 409)
    return shlex.split(raw) if raw else [sys.executable, "-m", *module_args]


def settings_env(settings: dict) -> dict:
    """Environment variables for a run, named exactly as orchestrator/settings.py reads them."""
    s = settings
    env = {
        "SOURCE_MODE": s["source_mode"], "STORY_SKILL": s["story_skill"],
        "CLAUDE_MODEL": s["claude_model"], "CRITIC_MODEL": s["reviewer_model"],
        "REVIEWER_MODEL": s["reviewer_model"],
        "IMAGE_PROVIDER": s["image_provider"], "IMAGE_FALLBACK_PROVIDER": s["image_fallback_provider"] or "none",
        "IMAGE_MODEL": s["image_model"], "VEO_MODEL": s["veo_model"], "VEO_RESOLUTION": s["veo_resolution"],
        "TTS_VOICE_NAME": s["voice"],
        # Tells orchestrator.run it was started here: only then does it honour the
        # narration pause flag and turn SIGTERM into an operator stop.
        "REELSMITH_UI": "1",
    }
    for key in ("max_budget_usd", "claude_shot_attempt_cap_usd", "claude_reviewer_cap_usd", "shot_concurrency",
                "veo_concurrency", "gemini_image_concurrency", "codex_image_concurrency",
                "codex_image_timeout_seconds", "veo_poll_seconds", "veo_max_poll_seconds",
                "image_call_cost_usd", "veo_call_cost_usd", "max_veo_shots"):
        env[key.upper()] = str(s[key])
    env["VEO_EXTEND_LONG_SHOTS"] = "true" if s["veo_extend_long_shots"] else "false"
    # Left unset, the pipeline prices a Codex image at $0; only pass an explicit override.
    from orchestrator.settings import DEFAULT_IMAGE_CALL_COST_USD
    if "codex" == s["image_provider"] and s["image_call_cost_usd"] == DEFAULT_IMAGE_CALL_COST_USD:
        env.pop("IMAGE_CALL_COST_USD")
    env.update(store.character_env(s.get("character_id")))
    return env


def _spawn(name: str, kind: str, cmd: list[str], extra_env: dict) -> None:
    with _lock:
        if live_run(name):
            raise StoreError("A run is already going on this project.", 409)
        folder = runs_dir(name)
        folder.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **extra_env}
        if "STORY_REWRITE_INSTRUCTIONS" not in extra_env:
            env.pop("STORY_REWRITE_INSTRUCTIONS", None)
        with open(folder / "ui_last_output.txt", "ab") as out:
            proc = subprocess.Popen(cmd, cwd=store.REPO, env=env, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        _procs[name] = proc
        _ended.discard(name)
        write_json(folder / PID_FILE, {"pid": proc.pid, "kind": kind, "started_at": store.now_iso(),
                                       "command": cmd})


def write_run_config(name: str, settings: dict) -> None:
    """Put limits.max_attempts into config.json, leaving its usage block alone."""
    path = runs_dir(name) / "config.json"
    data = read_json(path, {}) or {}
    data.setdefault("limits", {})["max_attempts"] = dict(settings["max_attempts"])
    write_json(path, data)


ARCHIVED = ("metadata", "generated", "audio", "orchestrator_runs", "book_reel.mp4", "ui_narration_history.json")


def archive_outputs(name: str) -> Path | None:
    project = store.project_dir(name)
    present = [p for p in ARCHIVED if (project / p).exists()]
    if not present:
        return None
    while True:  # a fresh folder: moving into an existing one would nest the items
        target = project / "_archive" / datetime.now().strftime("%Y%m%dT%H%M%S%f")
        try:
            target.mkdir(parents=True)
            break
        except FileExistsError:
            time.sleep(0.001)
    for item in present:
        shutil.move(str(project / item), str(target / item))
    return target


def start_reel(name: str, mode: str = "resume", retry_voice: bool = False,
               pause_after_narration: bool | None = None, rewrite: str | None = None) -> dict:
    store.existing_project(name)
    if mode not in ("resume", "start_over"):
        raise StoreError("mode must be 'resume' or 'start_over'.")
    cmd = _command("REELSMITH_RUN_CMD", ["orchestrator.run"]) + ["--project", name]
    # One request at a time from the check to the spawn, so two clicks can't
    # archive under each other or start two runs.
    with _lock:
        if live_run(name) or status(name)["state"] == "running":
            raise StoreError("A run is already going on this project.", 409)
        if not store.screenshot_files(name):
            raise StoreError("Add some screenshots before running.")
        settings = store.get_project_settings(name)
        if mode == "start_over":
            archive_outputs(name)
        runs_dir(name).mkdir(parents=True, exist_ok=True)
        write_run_config(name, settings)
        pause = settings["pause_after_narration"] if pause_after_narration is None else bool(pause_after_narration)
        set_flag(name, pause or bool(rewrite))
        if settings["source_mode"] == "content":
            cmd += ["--style", settings["style"]]
        if retry_voice:
            cmd.append("--retry-voice")
        env = settings_env(settings)
        if rewrite:
            env["STORY_REWRITE_INSTRUCTIONS"] = rewrite
        _spawn(name, "reel", cmd, env)
    return status(name)


def start_cover(name: str, hook: str = "", shot: int | None = None, force: bool = False,
                image_provider: str = "", feedback: str = "") -> dict:
    store.existing_project(name)
    if not (store.project_dir(name) / "book_reel.mp4").is_file():
        raise StoreError("Make the reel first: the cover is built from its shots.")
    settings = store.get_project_settings(name)
    cmd = _command("REELSMITH_COVER_CMD", ["orchestrator.cover"]) + ["--project", name]
    if hook:
        cmd += ["--hook", hook]
    if shot:
        cmd += ["--shot", str(int(shot))]
    if force:
        cmd.append("--force")
    if feedback:
        cmd += ["--feedback", feedback]
    env = settings_env(settings)
    provider = image_provider or settings["cover"].get("image_provider") or ""
    if provider:
        if provider not in ("gemini", "codex"):
            raise StoreError("Image maker must be gemini or codex.")
        env["IMAGE_PROVIDER"] = provider
        if env["IMAGE_FALLBACK_PROVIDER"] == provider:
            env["IMAGE_FALLBACK_PROVIDER"] = "none"
    _spawn(name, "cover", cmd, env)
    return cover_status(name)


def start_caption(name: str, force: bool = False, feedback: str = "") -> dict:
    store.existing_project(name)
    if not (store.project_dir(name) / "metadata" / "final_story_plan.json").is_file():
        raise StoreError("Write the narration first: the caption is written from it.")
    cmd = _command("REELSMITH_CAPTION_CMD", ["orchestrator.caption"]) + ["--project", name]
    if force:
        cmd.append("--force")
    if feedback:
        cmd += ["--feedback", feedback]
    _spawn(name, "caption", cmd, settings_env(store.get_project_settings(name)))
    return caption_status(name)


def _kind_status(name: str, kind: str) -> dict:
    return {"reel": status, "cover": cover_status, "caption": caption_status}[kind](name)


def stop(name: str) -> dict:
    """SIGTERM the run's process group, SIGKILL after 10 s, then record an operator stop."""
    info = live_run(name)
    if not info:
        current = status(name)
        if current["state"] == "running":
            raise StoreError("This run was started from the terminal, so stop it there (Ctrl+C).", 409)
        return current
    kind = info.get("kind", "reel")
    before = _kind_status(name, kind)
    pid = info["pid"]
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.time() + 10
    while time.time() < deadline and _pid_alive(pid, name):
        time.sleep(0.1)
    if _pid_alive(pid, name):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc = _procs.get(name)
        if proc is not None:
            proc.wait(timeout=5)
    live_run(name)  # reaps and removes the pid file
    status_file = runs_dir(name) / ("status.json" if kind == "reel" else f"{kind}_status.json")
    current = read_json(status_file, {}) or {}
    if current.get("state") not in ("stopped", "finished", "paused_for_narration"):
        if kind == "reel":
            steps = [{**s, "state": "waiting" if s["state"] in ("running", "retrying") else s["state"]}
                     for s in before["steps"]]
            current = {**current, "kind": "reel", "percent": before["percent"], "step": before["step"],
                       "steps": steps, "shots": current.get("shots", []),
                       "spent_usd": before["spent_usd"], "budget_usd": before["budget_usd"],
                       "started_at": before["started_at"]}
        current.update({"kind": kind, "state": "stopped", "stop_kind": "operator",
                        "stop_reason": "You stopped the run.", "updated_at": store.now_iso()})
        write_json(status_file, current)
    return _kind_status(name, kind)


def set_flag(name: str, enabled: bool) -> None:
    flag = pause_flag(name)
    if enabled:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(store.now_iso(), encoding="utf-8")
    else:
        flag.unlink(missing_ok=True)


def set_pause_after_narration(name: str, enabled: bool) -> dict:
    current = status(name)
    if enabled and current["state"] == "running" and current["step"] >= 3:
        raise StoreError("Too late for this run: the narration is already done.", 409)
    set_flag(name, enabled)
    return status(name)


# ------------------------------------------------------------------ logs

def _log_kind(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(2000)
    except OSError:
        return "reel"
    if "Making the Instagram cover" in head:
        return "cover"
    return "caption" if "Writing the Instagram caption" in head else "reel"


def log_files(name: str) -> list[Path]:
    folder = runs_dir(name)
    return sorted(folder.glob("run_*.log"), key=lambda p: p.name, reverse=True) if folder.is_dir() else []


def latest_log(name: str, kind: str | None = None) -> Path | None:
    return next((p for p in log_files(name) if kind is None or _log_kind(p) == kind), None)


def list_logs(name: str) -> list[dict]:
    return [{"name": p.name, "url": store.file_url(name, f"orchestrator_runs/{p.name}"),
             "at": _iso(p.stat().st_mtime), "kind": _log_kind(p)} for p in log_files(name)]


def read_log(name: str, offset: int = 0) -> dict:
    path = latest_log(name, "reel")  # the Run tab's log; the cover tab uses cover_status().log_tail
    if path is None:
        return {"text": "", "offset": 0, "name": None}
    with path.open("rb") as fh:
        size = fh.seek(0, 2)
        if offset < 0 or offset > size:
            offset = 0
        fh.seek(offset)
        data = fh.read()
    cut = data.rfind(b"\n") + 1  # hand out whole lines only
    return {"text": data[:cut].decode("utf-8", errors="replace"), "offset": offset + cut, "name": path.name}


def _tail(path: Path | None, n: int = 40) -> list[str]:
    if path is None:
        return []
    with path.open("rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - 64_000))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    return lines[-n:]


_CLOCK = re.compile(r"^\[(\d+):(\d\d):(\d\d)\]")
_PCT = re.compile(r"~(\d+)%")
_STEP = re.compile(r"Step (\d) of 6")
_TOOK = re.compile(r"Step (\d) took (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
_STOPPED = re.compile(r"STOPPED — (.*)")


def _parse_log(path: Path | None) -> dict:
    out = {"percent": 0, "step": 0, "took": {}, "stop_reason": None, "finished": False, "duration_s": None}
    if path is None:
        return out
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if m := _CLOCK.match(line):
            out["duration_s"] = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        for m in _PCT.finditer(line):
            out["percent"] = max(out["percent"], int(m[1]))
        if (m := _STEP.search(line)) and "▸" in line:
            out["step"] = int(m[1])
        if m := _TOOK.search(line):
            out["took"][int(m[1])] = int(m[2] or 0) * 3600 + int(m[3] or 0) * 60 + int(m[4] or 0)
        if m := _STOPPED.search(line):
            out["stop_reason"] = m[1].strip()
        if "Finished!" in line:
            out["finished"] = True
    return out


# ---------------------------------------------------------------- status

def _stop_kind(reason: str | None) -> str | None:
    if not reason:
        return None
    low = reason.lower()
    if "budget" in low:
        return "budget"
    if "attempt" in low or "tries" in low or "try " in low or "rejected" in low:
        return "attempts"
    return "vendor"


def _latest_failure(name: str) -> tuple[float, str] | None:
    """Newest failure_*.json that is not about the cover or caption: (mtime, reason)."""
    best = None
    for path in runs_dir(name).glob("failure_*.json"):
        if path.name.startswith(("failure_cover", "failure_caption")):
            continue
        mtime = path.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, path)
    if best is None:
        return None
    data = read_json(best[1], {}) or {}
    return best[0], str(data.get("reason") or "A step ran out of attempts.")


def _preview_url(name: str, rel: str | None) -> str | None:
    if not rel:
        return None
    path = store.project_dir(name) / rel
    return store.versioned_url(name, path) if path.is_file() else None


def _shots_from_plan(name: str, finished: bool) -> list[dict]:
    """Shot tiles for runs made before status.json existed, from the visual plan."""
    project = store.project_dir(name)
    plan = read_json(project / "metadata" / "visual_plan.json", {}) or {}
    used = ((read_json(runs_dir(name) / "config.json", {}) or {}).get("usage") or {}).get("attempts_used", {})
    limits = store.get_project_settings(name)["max_attempts"]
    out = []
    for shot in plan.get("shots") or []:
        n = shot.get("sequence")
        if not isinstance(n, int):
            continue
        video = shot.get("generation_mode") == "VEO"
        media = project / (f"generated/veo/shot_{n:02d}.mp4" if video else f"generated/stills/shot_{n:02d}.png")
        preview = f"generated/veo/previews/shot_{n:02d}_01.jpg" if video else f"generated/stills/shot_{n:02d}.png"
        if video and not (project / preview).is_file():
            preview = f"generated/veo_seeds/shot_{n:02d}_seed.png"
        key = f"{'veo' if video else 'stills'}_agent_shot_{n}"
        out.append({
            "number": n, "type": "video" if video else "still",
            "state": "approved" if media.is_file() else ("queued" if not used.get(key) else "failed"),
            "attempt": used.get(key, 1 if media.is_file() else 0),
            "max_attempts": limits["veo_agent" if video else "stills_agent"],
            "preview_url": _preview_url(name, preview), "prompt": shot.get("frame_composition", ""),
        })
    return out


def status(name: str) -> dict:
    """RunStatus for the project's reel (CONTRACT.md section 3)."""
    project = store.existing_project(name)
    folder = runs_dir(name)
    live = live_run(name)
    settings = store.get_project_settings(name)
    limits = settings["max_attempts"]
    config = read_json(folder / "config.json", {}) or {}
    usage = config.get("usage") or {}
    log_path = latest_log(name, "reel")
    st = read_json(folder / "status.json")
    st = st if isinstance(st, dict) and st.get("kind", "reel") == "reel" else None
    running_here = bool(live and live.get("kind") == "reel")
    now = time.time()
    stop_reason = stop_kind = None

    if st:
        state = STATE_MAP.get(st.get("state"), "stopped")
        updated = _parse_iso(st.get("updated_at")) or now
        if running_here:
            state = "running"
        elif state == "running" and (name in _ended or now - _mtime(log_path, updated) > TERMINAL_RUN_FRESH_S):
            # Nothing is behind it any more (a run from the terminal keeps its log fresh).
            state = "stopped"
            stop_reason = _parse_log(log_path)["stop_reason"] or "The run ended without finishing. Open the log for details."
        if state == "stopped":
            stop_reason = stop_reason or st.get("stop_reason") or "The run stopped."
            stop_kind = st.get("stop_kind") or _stop_kind(stop_reason)
        steps = []
        for i, step in enumerate(st.get("steps") or [], start=1):
            started, finished = _parse_iso(step.get("started_at")), _parse_iso(step.get("finished_at"))
            duration = step.get("duration_s")
            if duration is None and started:
                duration = (finished or (now if state == "running" else updated)) - started
            steps.append({
                "index": step.get("index", i), "name": step.get("name") or STEP_NAMES[i - 1],
                "state": step.get("state") or "waiting", "attempt": step.get("attempt") or 0,
                "max_attempts": step.get("max_attempts") or _step_max(i, limits),
                "duration_s": round(duration, 1) if duration is not None else None,
                "cost_usd": step.get("cost_usd") or 0.0,
            })
        shots = [{
            "number": s.get("number"), "type": s.get("type") or "still", "state": s.get("state") or "queued",
            "attempt": s.get("attempt") or 0, "max_attempts": s.get("max_attempts") or limits["stills_agent"],
            "preview_url": _preview_url(name, s.get("preview")) or s.get("preview_url"), "prompt": s.get("prompt") or "",
        } for s in st.get("shots") or []]
        started_at = _parse_iso(st.get("started_at"))
        if running_here and (_parse_iso(live.get("started_at")) or 0) > (started_at or 0):
            started_at = _parse_iso(live.get("started_at"))  # status.json is still the previous run's
        end = now if state == "running" else updated
        percent = 100 if state == "finished" else int(st.get("percent") or 0)
        step_no = int(st.get("step") or 0)
        spent = float(st.get("spent_usd") if st.get("spent_usd") is not None else usage.get("budget_spent_usd") or 0)
        budget = float(st.get("budget_usd") or settings["max_budget_usd"])
        waiting = now - updated if state == "paused" else 0
    else:
        parsed = _parse_log(log_path)
        failure = _latest_failure(name) if folder.is_dir() else None
        reel = project / "book_reel.mp4"
        if running_here:
            state = "running"
        elif failure and (not reel.is_file() or failure[0] > reel.stat().st_mtime):
            state, stop_reason = "stopped", parsed["stop_reason"] or failure[1]
        elif reel.is_file() or parsed["finished"]:
            state = "finished"
        elif log_path is not None:
            state = "stopped"
            stop_reason = parsed["stop_reason"] or "The last run did not finish. Resume to carry on."
        else:
            state = "idle"
        stop_kind = _stop_kind(stop_reason) if state == "stopped" else None
        step_no = parsed["step"]
        percent = 100 if state == "finished" else parsed["percent"]
        steps = []
        for i, step_name in enumerate(STEP_NAMES, start=1):
            if state == "finished" or i in parsed["took"]:
                s_state = "done"
            elif i == step_no:
                s_state = {"running": "running", "stopped": "failed"}.get(state, "waiting")
            else:
                s_state = "waiting"
            key = STEP_ATTEMPT_KEYS[i - 1]
            steps.append({"index": i, "name": step_name, "state": s_state,
                          "attempt": usage.get("attempts_used", {}).get(key, 0) if key else 0,
                          "max_attempts": _step_max(i, limits), "duration_s": parsed["took"].get(i),
                          "cost_usd": 0.0})
        shots = _shots_from_plan(name, state == "finished")
        started_at = _log_start(log_path) or (_parse_iso(live.get("started_at")) if live else None)
        end = now if state == "running" else (log_path.stat().st_mtime if log_path else now)
        spent = float(usage.get("budget_spent_usd") or 0)
        budget = float(settings["max_budget_usd"])
        waiting = 0

    if running_here:
        stop_reason = stop_kind = None
    # While paused the clock stops at the pause; the wait is reported separately.
    elapsed = (end - started_at) if started_at and state != "idle" else 0
    return {
        "state": state, "kind": "reel", "percent": percent, "step": step_no, "steps": steps, "shots": shots,
        "spent_usd": round(spent, 2), "budget_usd": budget,
        "elapsed_s": max(0, int(elapsed)), "waiting_s": max(0, int(waiting)),
        "started_at": _iso(started_at), "stop_reason": stop_reason, "stop_kind": stop_kind,
        "pause_after_narration": pause_flag(name).exists(),
        "log_tail": _tail(log_path) if log_path else [],
        "last_run_at": _iso(log_path.stat().st_mtime) if log_path else None,
    }


def _mtime(path: Path | None, default: float) -> float:
    return max(path.stat().st_mtime, default) if path is not None else default


def _step_max(index: int, limits: dict) -> int:
    key = STEP_ATTEMPT_KEYS[index - 1]
    return limits.get(key, 1) if key else 1


def _log_start(path: Path | None) -> float | None:
    if path is None:
        return None
    m = re.match(r"run_(\d{8}T\d{6})", path.name)
    if not m:
        return None
    return datetime.strptime(m[1], "%Y%m%dT%H%M%S").timestamp()


def _side_status(name: str, kind: str) -> dict:
    """Status of a cover or caption run, from its <kind>_status.json and its log."""
    store.existing_project(name)
    live = live_run(name)
    log_path = latest_log(name, kind)
    st = read_json(runs_dir(name) / f"{kind}_status.json", {}) or {}
    parsed = _parse_log(log_path)
    if live and live.get("kind") == kind:
        state, reason = "running", None
    elif st.get("state") in ("running", "stopped", "finished"):
        state, reason = st["state"], st.get("stop_reason")
        if state == "running":  # its process is gone
            state, reason = "stopped", parsed["stop_reason"] or f"The {kind} run ended without finishing."
    elif log_path is not None:
        state = "finished" if parsed["finished"] else "stopped"
        reason = None if state == "finished" else parsed["stop_reason"] or f"The {kind} run did not finish."
    else:
        state, reason = "idle", None
    percent = 100 if state == "finished" else int(st.get("percent") or 0)
    return {"state": state, "kind": kind, "percent": percent, "stop_reason": reason,
            "log_tail": _tail(log_path, 20) if log_path else []}


def cover_status(name: str) -> dict:
    return _side_status(name, "cover")


def caption_status(name: str) -> dict:
    return _side_status(name, "caption")


def summary(name: str) -> dict:
    """The run-related fields of a ProjectSummary."""
    current = status(name)
    reel_log = latest_log(name, "reel")
    return {
        "status": "draft" if current["state"] == "idle" else current["state"],
        "percent": current["percent"],
        "stop_reason": current["stop_reason"],
        "last_run_at": current["last_run_at"],
        "duration_s": current["elapsed_s"] or (_parse_log(reel_log)["duration_s"] if reel_log else None),
        "spent_usd": current["spent_usd"],
        "budget_usd": current["budget_usd"],
    }


# ---------------------------------------------------------------- health

HEALTH_SCRIPT = r'''
import dataclasses, json, os, shutil
from orchestrator.settings import load_dotenv_file, load_settings, SettingsError
from orchestrator import preflight as P
load_dotenv_file()
out = []
def add(name, failure, detail, hint):
    out.append({"name": name, "ok": failure is None, "detail": detail if failure is None else failure, "hint": None if failure is None else hint})
try:
    s = load_settings(); add("Settings", None, "All settings are valid", None)
except SettingsError as exc:
    s = None; add("Settings", str(exc), "", "Fix the value in Defaults or the .env file.")
try:
    import google.auth
    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    add("Google Cloud login", None, "Application-default credentials found" + (f" · {project}" if project else ""), None)
except Exception as exc:
    add("Google Cloud login", f"No usable credentials: {exc}".splitlines()[0][:300], "", "Run `gcloud auth application-default login`")
if s is not None:
    try:
        bucket = P.parse_bucket_name(s.gcs_bucket_uri)
        add("Storage bucket", None, f"gs://{bucket} (name checked; not contacted)", None)
    except ValueError as exc:
        add("Storage bucket", str(exc), "", "Set the bucket above, like gs://my-bucket/book_reels/veo/")
    r = P._check_character_reference(s)
    add("Character images", r and r.reason, "Character reference images are readable", "Fix the character in the Characters screen.")
    r = P._check_media_toolchain(s)
    add("ffmpeg", r and r.reason, "ffmpeg found", "Install ffmpeg, e.g. `brew install ffmpeg`")
    r = P._check_codex_image_provider(dataclasses.replace(s, image_provider="codex"))
    add("Codex login", r and r.reason, "Codex is installed and logged in", "Only needed when Codex draws images: install Codex and run `codex login`")
r = P.check_remotion_delivery_toolchain()
add("Remotion", r and r.reason, "Remotion is ready to render the final video", "Run `npm install` in remotion/")
keys = all(os.environ.get(k) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"))
add("Langfuse tracing", None if keys else "Tracing keys are not set", "Public and secret keys are set", "Optional: add LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to .env")
free = shutil.disk_usage(".").free / 1e9
add("Disk space", None if free > 5 else f"Only {free:.1f} GB free", f"{free:.0f} GB free", "Free some disk space; a run writes a few hundred MB.")
print(json.dumps(out))
'''


def health() -> list[dict]:
    """The pre-flight checks that make no paid calls, run in a child process
    so loading `.env` there never leaks into this server's environment."""
    env = {**os.environ, **settings_env(store.get_defaults())}
    try:
        done = subprocess.run([sys.executable, "-c", HEALTH_SCRIPT], cwd=store.REPO, env=env,
                              capture_output=True, text=True, timeout=120)
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, IndexError) as exc:
        detail = (locals().get("done") and (done.stderr or "").strip().splitlines()[-1:]) or [str(exc)]
        return [{"name": "Health check", "ok": False, "detail": f"The check itself failed: {detail[0]}",
                 "hint": "Run `python -m orchestrator.preflight` style checks from the terminal to see more."}]


def logins() -> list[dict]:
    adc = Path(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or
               Path.home() / ".config" / "gcloud" / "application_default_credentials.json")
    codex = shutil.which("codex")
    codex_auth = Path.home() / ".codex" / "auth.json"
    return [
        {"name": "Google Cloud login", "ok": adc.is_file(),
         "hint": None if adc.is_file() else "Run `gcloud auth application-default login`"},
        {"name": "Codex login", "ok": bool(codex) and codex_auth.is_file(),
         "hint": None if codex and codex_auth.is_file() else
         ("Run `codex login`" if codex else "Install the Codex CLI, then run `codex login`")},
    ]
