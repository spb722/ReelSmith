"""Scoped agent that chooses the cover's frame, hook and art direction.

Judgment, not measurement: which moment of a reel is most arresting, and which
of its own spoken lines works hardest as a hook, are taste questions with no
number that settles them. That is the same reason `story_agent` and
`visual_agent` are agents, and the opposite of the subtitle segmentation and
audio-chunking decisions, which stay deterministic.

The agent has no tools. It reads the plan it is handed and returns one
`CoverPlanContract`; the caller does every paid and every irreversible thing.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage, query

from orchestrator.agents._stream import log_tool_uses
from orchestrator.contracts.cover import MAX_HOOK_WORDS, CoverPlanContract
from orchestrator.settings import load_settings


cover_agent = AgentDefinition(
    description="Choose the frame, hook line and art direction for one reel's Instagram cover.",
    tools=[],
    prompt=f"""You design the Instagram cover for one finished short-form reel.
You have no tools. Read the supplied plan and return only the JSON schema.

You are given the reel's story arc, its narration, its per-shot goals, and the
list of shots whose artwork was actually generated and approved. Choose:

1. base_shot_sequence -- the shot whose artwork makes the strongest cover. It
must be one of the approved sequences supplied to you; never invent one. Prefer
the frame that carries the story's central image or turn, and that has a
readable area of sky, wall, shadow or empty ground where a line of text can sit
without covering a face. A frame showing the reel's key metaphor beats a frame
that is merely pretty.

2. hook_text -- at most {MAX_HOOK_WORDS} words, and ideally six or fewer. It
must be taken from the supplied narration: quote a phrase from it, or lightly
compress one. Do NOT invent marketing copy, do NOT write a call to action, and
do NOT promise anything the reel does not deliver -- the cover and the first
seconds of voiceover must agree. A line that makes the viewer examine their own
situation works harder than a line that describes the video. Drop a trailing
full stop; keep a question mark if the line is a question.

3. art_direction -- how to recompose that frame as a 1080x1920 cover. Say what
to push in on, what to let fall away, and where to keep a calm, uncluttered
area for the hook. State the mood in terms of the reel's own palette and art
style, and never ask for a different style. Say nothing about the headline's
size, font or placement -- the designer handles that, and your instruction
would only fight it.

4. rationale -- one or two sentences on why this frame and this line.

Remember the cover is first seen as a small thumbnail in a profile grid, which
crops the tall image to a portrait slice around its middle. Keep the important
subject near the centre, not at the extreme top or bottom.
""",
)


async def plan_cover(
    *,
    story_arc: dict,
    narration_script: str,
    shots: list[dict],
    approved_sequences: list[int],
    max_budget_usd: float,
    correction: str = "",
) -> ResultMessage:
    payload = {
        "story_arc": story_arc,
        "narration_script": narration_script,
        "shots": shots,
        "approved_shot_sequences": approved_sequences,
        "max_hook_words": MAX_HOOK_WORDS,
    }
    prompt = "Design the Instagram cover for this reel:\n" + json.dumps(payload, ensure_ascii=False)
    if correction:
        prompt += "\nYour previous answer was rejected; correct only this:\n" + correction

    options = ClaudeAgentOptions(
        model=load_settings().claude_model,
        system_prompt=cover_agent.prompt,
        tools=cover_agent.tools,
        allowed_tools=cover_agent.tools,
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        cwd=Path.cwd(),
        max_budget_usd=max_budget_usd,
        max_turns=3,
        output_format={"type": "json_schema", "schema": CoverPlanContract.model_json_schema()},
    )
    result = None
    async for message in query(prompt=prompt, options=options):
        log_tool_uses(message)
        if isinstance(message, ResultMessage):
            result = message
    if result is not None:
        return result
    raise RuntimeError("Cover agent returned no ResultMessage")
