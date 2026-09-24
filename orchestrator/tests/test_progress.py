from __future__ import annotations

import time

from orchestrator import progress


def test_format_duration_reads_plainly():
    assert progress.format_duration(0) == "0s"
    assert progress.format_duration(42) == "42s"
    assert progress.format_duration(68) == "1m08s"
    assert progress.format_duration(3720) == "1h02m"


def test_format_duration_never_reports_negative_time():
    """A clock that goes backwards should read as zero, not as a bug on screen."""
    assert progress.format_duration(-5) == "0s"


def test_log_carries_elapsed_time(capsys):
    progress.start_run(None)
    progress.log("doing the thing")
    out = capsys.readouterr().out
    assert "doing the thing" in out
    assert out.startswith("[00:00:")


def test_banner_is_flush_left_and_indent_nests(capsys):
    progress.start_run(None)
    progress.banner("Step 1 of 7")
    progress.log("under it", indent=2)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("▸ Step 1 of 7")
    assert "] " in lines[0] and lines[0].split("] ")[1].startswith("▸")
    assert lines[1].split("] ")[1] == "    under it"


def test_errors_go_to_stderr(capsys):
    progress.start_run(None)
    progress.log("that did not work", error=True)
    captured = capsys.readouterr()
    assert "that did not work" in captured.err
    assert captured.out == ""


def test_start_run_writes_a_log_file(tmp_path, capsys):
    """The console scrollback is gone by the time a 30-minute run halts, so the
    same trace has to survive on disk."""
    path = progress.start_run(tmp_path)
    try:
        progress.log("first line")
        progress.log("second line", error=True)
        capsys.readouterr()
        contents = path.read_text(encoding="utf-8")
    finally:
        progress.start_run(None)

    assert path.parent == tmp_path
    assert "first line" in contents
    # stderr lines belong in the file too -- a failure is exactly what someone
    # goes back to the log for.
    assert "second line" in contents


def test_heartbeat_reports_while_the_work_blocks(capsys):
    progress.start_run(None)
    with progress.heartbeat("still drawing", interval=0.05):
        time.sleep(0.3)
    out = capsys.readouterr().out
    assert "still drawing" in out
    assert out.count("still drawing") >= 2


def test_heartbeat_stops_when_the_block_exits(capsys):
    progress.start_run(None)
    with progress.heartbeat("working", interval=0.05):
        time.sleep(0.15)
    capsys.readouterr()
    time.sleep(0.3)
    assert "working" not in capsys.readouterr().out


def test_heartbeat_is_silent_for_fast_work(capsys):
    """A call that returns quickly should not add a line of noise."""
    progress.start_run(None)
    with progress.heartbeat("working", interval=5.0):
        pass
    assert "working" not in capsys.readouterr().out
