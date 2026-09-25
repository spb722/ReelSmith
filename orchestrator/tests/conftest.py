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
from claude_agent_sdk import ResultMessage

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
def _no_live_claude_calls():
    """A test that forgets to mock `query` fails at once instead of spending.

    Every agent resolves `claude_agent_sdk.query` at call time (Story 4.6),
    so replacing it here covers them all. A private MonkeyPatch, so this
    doesn't reorder the shared `monkeypatch` fixture's teardown.
    """

    import claude_agent_sdk

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real claude_agent_sdk.query; mock it")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(claude_agent_sdk, "query", refuse)
        yield


def _approving_result(output: dict) -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="reviewer", total_cost_usd=0.0, structured_output=output,
    )


@pytest.fixture(autouse=True)
def _reviewers_approve_for_free(monkeypatch):
    """Story 4.5: the story critic and visual reviewer approve at no cost.

    Every stage test that predates them keeps its behaviour, and no test can
    reach the real SDK through them. Tests of the reviewers override these.
    """

    import orchestrator.run as run

    async def critique_story(*args, **kwargs):
        return _approving_result({"produced_by": "story_critic", "verdict": "APPROVE", "issues": []})

    async def review_visual_asset(**kwargs):
        return _approving_result({
            "produced_by": "visual_reviewer", "approved": True, "summary": "Approved by the test reviewer.",
        })

    monkeypatch.setattr(run, "critique_story", critique_story)
    monkeypatch.setattr(run, "review_visual_asset", review_visual_asset)


@pytest.fixture(autouse=True)
def _restore_workspace_state():
    before_cwd = os.getcwd()
    before_project = workspace.active_project()
    yield
    os.chdir(before_cwd)
    workspace._active_project = before_project
