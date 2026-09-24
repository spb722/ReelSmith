"""Live tool-call reporting for the agent stages.

Each agent already iterates the SDK stream and keeps only the `ResultMessage`,
so a stage that runs for minutes printed nothing between "Try 1 of 4" and its
result. The tool-use blocks were streaming past and being dropped. Logging
them as they arrive is what makes an unattended run look alive.
"""

from __future__ import annotations

from claude_agent_sdk import AssistantMessage, ToolUseBlock

from orchestrator.progress import log

# Tool names are internal ("mcp__gemini__generate_still"). Someone watching
# the run wants to know what is happening, not which MCP server owns the tool.
_PLAIN_TOOL_NAMES = {
    "Read": "opening a screenshot to look at it",
    "generate_still": "drawing the still picture",
    "generate_veo_seed": "drawing the opening frame for the video clip",
    "generate_veo_clip": "generating the moving video clip (this is the slow one)",
}


def log_tool_uses(message: object) -> None:
    """Print one plain line for every tool the agent invokes."""

    if not isinstance(message, AssistantMessage):
        return
    for block in message.content:
        if isinstance(block, ToolUseBlock):
            short_name = block.name.rsplit("__", 1)[-1]
            log(_PLAIN_TOOL_NAMES.get(short_name, short_name), indent=2)
