"""Scoped visual-QA agent for one already-assigned VEO shot."""

from __future__ import annotations

import json
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage, query

from orchestrator.contracts.veo import VeoOutcomeContract
from orchestrator.tools.gemini_tools import gemini_server
from orchestrator.tools.veo_tools import veo_server


TOOLS = ["mcp__gemini__generate_veo_seed", "mcp__veo__generate_veo_clip"]


veo_agent = AgentDefinition(
    description="Generate and visually validate one VEO-mode production shot.",
    tools=TOOLS,
    prompt="""You are the Veo generation and visual-QA agent for exactly one shot.
The supplied validated shot is authoritative. It is already assigned VEO mode;
never change its mode, timing, source assets, narration, or subtitle linkage.
You have exactly two tools and no filesystem, shell, web, or shared-state tool.
Each invocation is a fresh session with no memory of any earlier attempt for
this shot; the supplied correction and previous attempt outcome (if any) are
your only record of prior work.

1. Call generate_veo_seed exactly once this session using the supplied shot,
cited analyzed asset, subtitle context, and settings -- you must still call it
even on a retry, since you hold no seed object across attempts. If the
supplied previous attempt outcome's failure.stage is "veo_generation" or
"clip_qa" (the seed already passed QA before; only the clip needed
correction), do not re-litigate the seed: confirm it is still structurally
sound and approve it without repeating full visual scrutiny, so the correction
budget goes to the clip. Otherwise -- no previous outcome, or its
failure.stage is "seed_qa" or "seed_generation" -- give the seed your normal
full inspection: reject it if it contains app UI, visible text, duplicate/extra
subjects, wrong scene or emotion, photorealistic drift, or a composition that
does not serve the shot. On rejection, return a structured retryable seed_qa
failure. Include the seed's cost and path in partial_artifact_paths. Do not
call Veo.
2. On seed approval, copy the seed payload exactly, set approved=true, and add a
specific qa_summary describing what you verified. Call generate_veo_clip exactly
once with that approved seed and the supplied settings/attempt/correction.
3. If the tool returns FAILURE, preserve every failure field exactly in your
VeoOutcomeContract. Never flatten RAI reasons or operation errors.
4. If it returns an unapproved result, inspect every returned preview image.
Approve only if the clip preserves the seed's scene/style, has coherent restrained
motion, no UI/text/extra subjects/distortion, and serves the shot. Return SUCCESS
with result.approved=true and a specific clip qa_summary. If no preview images are
available or visual QA fails, return a retryable clip_qa failure preserving the
operation name/dump, paths, costs, and a concrete correction reason for the next
attempt.

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
        system_prompt=veo_agent.prompt,
        tools=veo_agent.tools,
        allowed_tools=veo_agent.tools,
        mcp_servers={"gemini": gemini_server, "veo": veo_server},
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        cwd=Path.cwd(),
        max_budget_usd=max_budget_usd,
        max_turns=8,
        output_format={"type": "json_schema", "schema": VeoOutcomeContract.model_json_schema()},
        max_buffer_size=20 * 1024 * 1024,
    )
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            return message
    raise RuntimeError("Veo agent returned no ResultMessage")
