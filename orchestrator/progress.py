"""Timestamped run progress for the console and the run log.

Stage output used to be bare `print()` at stage boundaries only. That reads
fine for a fast run and badly for this one: a Claude agent turn, a Veo poll,
or a Codex image call each sit silent for minutes, so an unattended run is
indistinguishable from a hung one. Every line now carries how long the run has
been going, and the same trace is written to
`orchestrator_runs/run_<timestamp>.log` so it survives the console scrollback.

Deliberately not the `logging` module: the existing 45 call sites are plain
prints in a house style that reads well, and a framework migration would
rewrite all of them to gain levels and handlers nothing here asks for.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

_run_start = time.monotonic()
_log_file: TextIO | None = None
# Heartbeats fire from a daemon thread while the main thread blocks, so two
# writers can reach the same line buffer. Serialize them.
_write_lock = threading.Lock()


# Share of a typical reel run, in percent, for each of the six steps --
# measured on the complete self-org-desk run of 2026-09-25 (20m10s). An
# estimate: retries and slow vendor calls stretch a step, so the percentage
# can pause, but it never goes backwards.
STEP_WEIGHTS = (9, 26, 10, 4, 49, 2)


class RunProgress:
    """Turns step and shot events into an estimated percent complete.

    Only numbers, no display: the console adds them to its lines today, and a
    UI can read the same numbers later.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.steps_done = 0
        self.shots_done = 0
        self.shots_total = 0

    def step_finished(self, step: int) -> None:
        self.steps_done = step
        self.shots_done = self.shots_total = 0

    def shots_planned(self, done: int, total: int) -> None:
        """The current step makes `total` shots, `done` of them already made."""
        self.shots_done, self.shots_total = done, total

    def shot_finished(self) -> None:
        self.shots_done += 1

    def percent(self) -> int:
        done = sum(STEP_WEIGHTS[:self.steps_done])
        if self.shots_total and self.steps_done < len(STEP_WEIGHTS):
            done += STEP_WEIGHTS[self.steps_done] * self.shots_done / self.shots_total
        return min(100, round(done))


run_progress = RunProgress()


def start_run(log_dir: Path | None = None) -> Path | None:
    """Reset the run clock and open the run log, returning its path."""

    global _run_start, _log_file
    _run_start = time.monotonic()
    run_progress.reset()
    if log_dir is None:
        return None
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"run_{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
    _log_file = path.open("a", encoding="utf-8")
    return path


def elapsed_seconds() -> float:
    return time.monotonic() - _run_start


def format_duration(seconds: float) -> str:
    """Compact human duration: `42s`, `1m08s`, `1h02m`."""

    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, remainder = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _clock() -> str:
    total = int(elapsed_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def log(message: str, *, indent: int = 1, error: bool = False) -> None:
    """Write one timestamped line to the console and the run log."""

    line = f"[{_clock()}] {'  ' * indent}{message}"
    with _write_lock:
        print(line, file=sys.stderr if error else sys.stdout, flush=True)
        if _log_file is not None:
            _log_file.write(line + "\n")
            _log_file.flush()
    # Every printed line is a moment worth showing: it refreshes the spend,
    # the percent and `updated_at` in the UI's status file.
    run_status.refresh()


def banner(message: str) -> None:
    """A stage heading, flush left so stages stand out when scanning back."""

    log(f"▸ {message}", indent=0)


@contextmanager
def heartbeat(label: str, *, interval: float = 15.0):
    """Print `label` with a running elapsed time until the block exits.

    For the waits with no natural progress event of their own -- a Codex
    subprocess, a Veo operation poll -- where silence would otherwise look
    like a hang.
    """

    stop = threading.Event()
    started = time.monotonic()

    def beat() -> None:
        while not stop.wait(interval):
            log(f"{label} … {format_duration(time.monotonic() - started)}", indent=2)

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# The UI's status file (ui/CONTRACT.md section 5)
# ---------------------------------------------------------------------------

STEP_NAMES = (
    "Looking at your screenshots",
    "Writing the narration",
    "Planning the shots",
    "Recording the voice and building subtitles",
    "Making the video clips and still pictures",
    "Putting the final video together",
)

# How a halt's reason code reads as the UI's `stop_kind`.
STOP_KINDS = {
    "retry_ceiling": "attempts",
    "clip_qa_repeated": "attempts",
    "budget_exhausted": "budget",
    "vendor_limit": "vendor",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: dict) -> None:
    """Write via a unique temp file and `os.replace`, so a reader never sees half a file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def stop_kind_for(reason: str, reason_code: str | None) -> str | None:
    kind = STOP_KINDS.get(reason_code or "")
    if kind is None and reason_code in (None, "unclassified"):
        # Some halts (the voice step's) carry no reason code of their own.
        lowered = reason.lower()
        if lowered.startswith("retry ceiling exhausted"):
            kind = "attempts"
        elif "budget" in lowered:
            kind = "budget"
    return kind


class RunStatus:
    """What the run is doing, rewritten to `status.json` on every event.

    Inactive (every method a no-op) until `begin`, so tests and helpers that
    never start a run write nothing. All mutation is under one re-entrant lock:
    the heartbeat thread refreshes the file too, and the SIGTERM handler may
    write while the main thread is mid-write.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.path: Path | None = None

    @property
    def active(self) -> bool:
        return self.path is not None

    def begin(self, path: Path, *, kind: str = "reel") -> None:
        with self._lock:
            self.path = Path(path).resolve()
            self.kind = kind
            self.state = "running"
            self.step: int | None = None
            self.steps = [
                {"index": i, "name": name, "state": "waiting", "attempt": None, "max_attempts": None,
                 "started_at": None, "finished_at": None, "cost_usd": None}
                for i, name in enumerate(STEP_NAMES, start=1)
            ] if kind == "reel" else []
            self.shots: dict[int, dict] = {}
            self.budget_usd: float | None = None
            self._spent: Callable[[], float] | None = None
            self._step_spend_start = 0.0
            self.started_at = _now()
            self.stop_reason: str | None = None
            self.stop_kind: str | None = None
            self.percent_override: int | None = None
            self._write()

    def attach_budget(self, spent: Callable[[], float], budget_usd: float | None) -> None:
        """Where the running spend is read from (the run manifest), and the ceiling."""

        with self._lock:
            if not self.active:
                return
            self._spent, self.budget_usd = spent, budget_usd
            self._write()

    def _spent_usd(self) -> float | None:
        if self._spent is None:
            return None
        try:
            return round(float(self._spent()), 4)
        except Exception:
            return None

    def _current(self) -> dict | None:
        if self.step is None or not self.steps:
            return None
        return self.steps[self.step - 1]

    def step_started(self, index: int, *, attempt: int | None = None, max_attempts: int | None = None) -> None:
        with self._lock:
            if not self.active:
                return
            self.step = index
            self._step_spend_start = self._spent_usd() or 0.0
            self.steps[index - 1].update(
                state="running", attempt=attempt or None, max_attempts=max_attempts,
                started_at=_now(), finished_at=None, cost_usd=None,
            )
            self._write()

    def attempt(self, number: int, max_attempts: int | None = None) -> None:
        """The current step started try `number`; a second or later try reads as retrying."""

        with self._lock:
            step = self._current() if self.active else None
            if step is None:
                return
            step["attempt"] = number
            if max_attempts is not None:
                step["max_attempts"] = max_attempts
            step["state"] = "retrying" if number > 1 else "running"
            self._write()

    def _close_step(self, state: str) -> None:
        step = self._current()
        if step is None:
            return
        step["state"] = state
        step["finished_at"] = _now() if state in ("done", "failed") else None
        spent = self._spent_usd()
        if spent is not None:
            step["cost_usd"] = round(max(0.0, spent - self._step_spend_start), 4)

    def step_finished(self, index: int) -> None:
        with self._lock:
            if not self.active:
                return
            self.step = index
            self._close_step("done")
            self._write()

    def shots_planned(self, shots: list[dict]) -> None:
        """`shots`: one dict per shot with the contract's Shot fields."""

        with self._lock:
            if not self.active:
                return
            self.shots = {shot["number"]: {
                "number": shot["number"], "type": shot.get("type", "still"),
                "state": shot.get("state", "queued"), "attempt": shot.get("attempt"),
                "max_attempts": shot.get("max_attempts"), "preview": shot.get("preview"),
                "prompt": shot.get("prompt"),
            } for shot in shots}
            self._write()

    def shot(self, number: int, **fields) -> None:
        """Change one shot's fields (state, attempt, max_attempts, preview, type)."""

        with self._lock:
            if not self.active:
                return
            entry = self.shots.setdefault(number, {
                "number": number, "type": "still", "state": "queued", "attempt": None,
                "max_attempts": None, "preview": None, "prompt": None,
            })
            entry.update(fields)
            self._write()

    def pause_for_narration(self) -> None:
        with self._lock:
            if not self.active:
                return
            self.state = "paused_for_narration"
            self.step = 2
            self.steps[1]["state"] = "waiting_for_you"
            self._write()

    def halt(self, reason: str, reason_code: str | None = None, *, stop_kind: str | None = None) -> None:
        with self._lock:
            if not self.active or self.state != "running":
                return
            self.state = "stopped"
            self.stop_reason = reason
            self.stop_kind = stop_kind or stop_kind_for(reason, reason_code)
            self._close_step("failed")
            self._write()

    def finish(self) -> None:
        with self._lock:
            if not self.active or self.state != "running":
                return
            self.state = "finished"
            self.percent_override = 100
            self._write()

    def set_percent(self, percent: int) -> None:
        """For runs with no steps (the cover): an explicit percent."""

        with self._lock:
            if not self.active:
                return
            self.percent_override = percent
            self._write()

    def close(self, exit_code: int | None) -> None:
        """At process end: a run still marked running finished (code 0) or stopped."""

        with self._lock:
            if not self.active:
                return
            if self.state == "running":
                if exit_code == 0:
                    self.finish()
                else:
                    self.halt("The run stopped unexpectedly — see the run log")
            self.path = None

    def refresh(self) -> None:
        with self._lock:
            if self.active:
                self._write()

    def snapshot(self) -> dict:
        percent = self.percent_override
        if percent is None:
            percent = run_progress.percent() if self.kind == "reel" else 0
        data = {
            "kind": self.kind,
            "state": self.state,
            "percent": percent,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at,
            "updated_at": _now(),
        }
        if self.kind == "reel":
            data.update({
                "step": self.step,
                "steps": [dict(step) for step in self.steps],
                "shots": [dict(self.shots[number]) for number in sorted(self.shots)],
                "spent_usd": self._spent_usd(),
                "budget_usd": self.budget_usd,
                "stop_kind": self.stop_kind,
            })
        else:
            data["stop_kind"] = self.stop_kind
        return data

    def _write(self) -> None:
        try:
            write_json_atomic(self.path, self.snapshot())
        except OSError:
            # The UI's view is never worth stopping a paid run for.
            pass


run_status = RunStatus()
