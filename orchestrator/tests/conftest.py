"""Shared test isolation.

`enter_project` sets process-wide state -- the working directory and the
active project -- because that is how one reel's relative paths are kept
separate from another's. Process-wide state leaks between tests, so it is
restored here for every test rather than in each module that happens to
touch it.
"""

from __future__ import annotations

import os

import pytest

from orchestrator import workspace


@pytest.fixture(autouse=True)
def _tmp_path_is_addressable_as_a_project(tmp_path, monkeypatch):
    """Let a test call `main(["--project", tmp_path.name])`.

    The suite already builds its fixtures directly in tmp_path and isolates
    with chdir. Pointing PROJECTS_DIR at tmp_path's parent makes that same
    layout a valid project, so the entrypoint change costs the call site and
    nothing else.
    """

    monkeypatch.setattr(workspace, "PROJECTS_DIR", tmp_path.parent)


@pytest.fixture(autouse=True)
def _restore_workspace_state():
    before_cwd = os.getcwd()
    before_project = workspace.active_project()
    yield
    os.chdir(before_cwd)
    workspace._active_project = before_project
