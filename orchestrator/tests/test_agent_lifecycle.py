"""SDK streams must finish before a stage returns, including trailing cleanup."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from claude_agent_sdk import ResultMessage


@pytest.fixture(params=[
    ("asset_analyst", "analyze_assets", {"assets": []}),
    ("story_agent", "write_story", {"analyzed_assets": []}),
    ("visual_agent", "plan_visuals", {"final_story_plan": {}, "analyzed_assets": []}),
    ("veo_agent", "generate_veo_asset", {
        "shot": {}, "asset": {}, "subtitle_text": "", "settings": {}, "attempt": 1,
    }),
    ("stills_agent", "generate_still_asset", {
        "shot": {}, "asset": {}, "subtitle_text": "", "settings": {}, "attempt": 1,
    }),
], ids=["assets", "story", "visuals", "veo", "stills"])
def agent(request, monkeypatch):
    module_name, function_name, kwargs = request.param
    module = importlib.import_module(f"orchestrator.agents.{module_name}")
    monkeypatch.setattr(module, "load_settings", lambda: SimpleNamespace(claude_model="test-model"))

    async def invoke():
        return await getattr(module, function_name)(max_budget_usd=1.0, **kwargs)

    return module, invoke


def result_message():
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="lifecycle-test", total_cost_usd=0.1,
        structured_output={"complete": True},
    )


def test_stream_finishes_in_calling_task_before_result_returns(agent, monkeypatch):
    module, invoke = agent
    expected = result_message()
    events = []
    stream_tasks = []

    async def fake_query(**kwargs):
        stream_tasks.append(asyncio.current_task())
        try:
            yield object()
            yield result_message()
            yield expected
            await asyncio.sleep(0)
            events.append("drained")
            yield object()
        finally:
            await asyncio.sleep(0)
            stream_tasks.append(asyncio.current_task())
            events.append("cleaned")

    monkeypatch.setattr(module, "query", fake_query)

    async def check():
        actual = await invoke()
        assert actual is expected
        assert events == ["drained", "cleaned"]
        assert stream_tasks == [asyncio.current_task(), asyncio.current_task()]

    asyncio.run(check())


def test_cleanup_failure_after_result_surfaces(agent, monkeypatch):
    module, invoke = agent

    async def fake_query(**kwargs):
        try:
            yield result_message()
        finally:
            await asyncio.sleep(0)
            raise RuntimeError("SDK cleanup failed")

    monkeypatch.setattr(module, "query", fake_query)
    with pytest.raises(RuntimeError, match="SDK cleanup failed"):
        asyncio.run(invoke())


def test_missing_result_still_fails_after_stream_cleanup(agent, monkeypatch):
    module, invoke = agent
    cleaned = []

    async def fake_query(**kwargs):
        try:
            yield object()
        finally:
            cleaned.append(True)

    monkeypatch.setattr(module, "query", fake_query)
    with pytest.raises(RuntimeError, match="returned no ResultMessage"):
        asyncio.run(invoke())
    assert cleaned == [True]
