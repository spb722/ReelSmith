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

import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TextIO

_run_start = time.monotonic()
_log_file: TextIO | None = None
# Heartbeats fire from a daemon thread while the main thread blocks, so two
# writers can reach the same line buffer. Serialize them.
_write_lock = threading.Lock()


def start_run(log_dir: Path | None = None) -> Path | None:
    """Reset the run clock and open the run log, returning its path."""

    global _run_start, _log_file
    _run_start = time.monotonic()
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
