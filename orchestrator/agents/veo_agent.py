"""Scoped visual-QA agent for one already-assigned VEO shot."""

from __future__ import annotations

import json
from contextlib import aclosing
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage

from orchestrator.agents._guard import PaidToolGuard
from orchestrator.agents._skills import (
    PIPELINE_ENV,
    PLUGIN,
    assert_isolated,
    check_init_message,
    merge_hooks,
    plugin_read_guard,
    plugin_read_rule,
    qualified,
)
from orchestrator.agents._stream import log_tool_uses
from orchestrator.contracts.veo import VeoOutcomeContract
from orchestrator.tools.gemini_tools import gemini_server
from orchestrator.tools.veo_tools import veo_server
from orchestrator.settings import load_settings
from orchestrator.tracing import agent_query

# Resolved through the SDK module at call time, so the tracing instrumentation
# takes effect; tests replace this name (Story 4.6).
query = agent_query("veo_agent")


GENERATION_TOOLS = ["mcp__gemini__generate_veo_seed", "mcp__veo__generate_veo_clip"]
SKILLS = qualified("veo-production", "visual-qa", "character-likeness-qa")
# Read exists only to open skill reference files; a hook denies anything else.
TOOLS = [*GENERATION_TOOLS, "Skill", "Read"]


veo_agent = AgentDefinition(
    description="Generate and visually validate one VEO-mode production shot.",
    tools=TOOLS,
    prompt="""You are the Veo generation and visual-QA agent for exactly one shot.
The supplied validated shot is authoritative. It is already assigned VEO mode;
never change its mode, timing, source assets, narration, or subtitle linkage.
You have exactly two generation tools, plus Skill and a Read limited to your
skills' files, and no shell, web, or shared-state tool.
Each invocation is a fresh session with no memory of any earlier attempt for
this shot; the supplied correction and previous attempt outcome (if any) are
your only record of prior work.

Before calling any tool, load book-reels:veo-production, book-reels:visual-qa
and book-reels:character-likeness-qa with the Skill tool and follow them:
veo-production is your step-by-step procedure, visual-qa and
character-likeness-qa are the criteria you judge the seed and clip by.

The top-level produced_by is always "veo_agent" and generation_mode is always
"VEO". Return only the supplied JSON schema. Never write production_assets.json,
invoke scripts, generate a still, or ask the operator questions.
""",
)


async def generate_veo_asset(
    *,
    shot: dict,
    asset: dict,
    subtitle_text: str,
    settings: dict,
    attempt: int,
    max_budget_usd: float,
    correction: str = "",
    previous_outcome: object = None,
    guard: PaidToolGuard | None = None,
) -> ResultMessage:
    payload = {
        "shot": shot,
        "asset": asset,
        "subtitle_text": subtitle_text,
        "settings": settings,
        "attempt": attempt,
        "correction": correction,
    }
    prompt = "Generate and visually validate this one VEO shot:\n" + json.dumps(payload, ensure_ascii=False)
    if previous_outcome is not None:
        prompt += "\nPrevious attempt outcome; correct only its QA/failure reason:\n"
        prompt += json.dumps(previous_outcome, ensure_ascii=False)
    options = ClaudeAgentOptions(
        model=load_settings().claude_model,
        system_prompt=veo_agent.prompt,
        tools=TOOLS,
        allowed_tools=[*GENERATION_TOOLS, plugin_read_rule()],
        skills=SKILLS,
        plugins=[PLUGIN],
        env=dict(PIPELINE_ENV),
        # The Read guard and the paid-tool guard both stay bound.
        hooks=merge_hooks(plugin_read_guard(), guard.hooks() if guard else {}),
        mcp_servers={"gemini": gemini_server, "veo": veo_server},
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        cwd=Path.cwd(),
        max_budget_usd=max_budget_usd,
        # Skill loads take turns too; max_budget_usd still bounds the spend.
        max_turns=12,
        output_format={"type": "json_schema", "schema": VeoOutcomeContract.model_json_schema()},
        max_buffer_size=20 * 1024 * 1024,
    )
    assert_isolated(options)
    result = None
    async with aclosing(query(prompt=prompt, options=options)) as stream:
        async for message in stream:
            check_init_message(message, SKILLS)
            log_tool_uses(message)
            if isinstance(message, ResultMessage):
                result = message
    # Exhaust the stream so SDK cleanup finishes in this task before returning.
    if result is not None:
        return result
    raise RuntimeError("Veo agent returned no ResultMessage")
