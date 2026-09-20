"""Scoped visual-QA agent for one already-assigned STILL shot."""

from __future__ import annotations

import json
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage, query

from orchestrator.contracts.stills import StillOutcomeContract
from orchestrator.tools.gemini_tools import gemini_server
from orchestrator.settings import load_settings


TOOLS = ["mcp__gemini__generate_still"]


stills_agent = AgentDefinition(
    description="Generate and visually validate one STILL-mode production shot.",
    tools=TOOLS,
    prompt="""You are the stills generation and visual-QA agent for exactly one shot.
The supplied validated shot is authoritative. It is already assigned STILL mode;
never change its mode, timing, source assets, narration, or subtitle linkage.
You have exactly one tool and no filesystem, shell, web, or shared-state tool.
Each invocation is a fresh session with no memory of any earlier attempt for
this shot; the supplied correction and previous attempt outcome (if any) are
your only record of prior work.

1. Call generate_still exactly once this session using the supplied shot,
cited analyzed asset, subtitle context, and settings -- you must still call it
even on a retry, since you hold no image object across attempts.
2. If the tool returns FAILURE, preserve every failure field exactly in your
StillOutcomeContract.
3. If it returns an unapproved result, inspect the returned image. Reject it
if it contains app UI, visible text, letters, logos, or watermarks, drifts to
photorealism, or -- the concrete defect this stage exists to catch -- shows
the same focal subject or scene duplicated anywhere in frame (e.g. two
tombstones, two of the same figure). Approve only if it shows exactly one
instance of the focal subject, matches the shot's composition and goal, and
leaves clean space for later Remotion subtitles/typography.
When the tool result includes character reference image(s) after the generated
still, the scene is one that already contained a person and that person must now
be the referenced character. Those references are NOT under QA -- never judge the
still against a reference's background, pose, crop, or framing. Approve only if
the person in the still is unmistakably that character: same face shape, eyes,
eyebrows, nose, mouth, beard, hairstyle, hair colour and skin tone, with the face
clearly visible, in focus, and detailed. Reject a faceless, blank, featureless,
blurred, obscured or turned-away figure, reject a face simplified into dots or a
plain oval, reject a generic person who is not the reference, and reject a frame
whose whole scene has been restyled to match the character -- only the person may
be drawn in the character's style, the scene keeps the source's art style,
palette and lighting. Other people in the scene, such as background silhouettes,
must stay as the source drew them. When the tool result includes NO character
reference, the source scene has no person in it: that still is correct without
one -- never reject it for a missing character and never ask for a person to be
added. Return SUCCESS
with result.approved=true and a specific qa_summary describing what you
verified. If visual QA fails, return a retryable still_qa failure. Include
the still's cost_usd and local_image_path in partial_artifact_paths, plus a
concrete correction reason for the next attempt.

The top-level produced_by is always "stills_agent" and generation_mode is
always "STILL". Return only the supplied JSON schema. Never write
production_assets.json, invoke scripts, generate a VEO clip, or ask the
operator questions.
""",
)


async def generate_still_asset(
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
    prompt = "Generate and visually validate this one STILL shot:\n" + json.dumps(payload, ensure_ascii=False)
    if previous_outcome is not None:
        prompt += "\nPrevious attempt outcome; correct only its QA/failure reason:\n"
        prompt += json.dumps(previous_outcome, ensure_ascii=False)
    options = ClaudeAgentOptions(
        model=load_settings().claude_model,
        system_prompt=stills_agent.prompt,
        tools=stills_agent.tools,
        allowed_tools=stills_agent.tools,
        mcp_servers={"gemini": gemini_server},
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        cwd=Path.cwd(),
        max_budget_usd=max_budget_usd,
        max_turns=6,
        output_format={"type": "json_schema", "schema": StillOutcomeContract.model_json_schema()},
        max_buffer_size=20 * 1024 * 1024,
    )
    result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    # Exhaust the stream so SDK cleanup finishes in this task before returning.
    if result is not None:
        return result
    raise RuntimeError("Stills agent returned no ResultMessage")
