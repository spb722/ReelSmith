"""Per-query guard on the paid MCP tools (Story 4.2, FR9/FR10).

One `PaidToolGuard` is built by the orchestrator for each Veo or stills
query and bound to it through `hooks=`. Its closure is the record the
orchestrator reads once the query returns:

- `PreToolUse` denies a paid call when the paid cost already allowed in
  this query, plus this call's configured cost, would exceed `limit_usd`.
  The running total makes a second `generate_veo_seed` count too.
- `PostToolUse` parses the tool's JSON text (spike 4.1 (g): `tool_response`
  is a list of content blocks) and records whether the call billed and any
  `failure` contract it returned. The handlers never raise, so this is where
  a vendor refusal inside a tool shows up.
- `PostToolUseFailure` covers only an unexpected handler crash.

Classifying the recorded failures is the orchestrator's job (`run.py`),
which keeps the one classifier in one place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import HookMatcher

VEO_SEED_TOOL = "mcp__gemini__generate_veo_seed"
STILL_TOOL = "mcp__gemini__generate_still"
VEO_CLIP_TOOL = "mcp__veo__generate_veo_clip"
IMAGE_TOOLS = (VEO_SEED_TOOL, STILL_TOOL)


@dataclass(frozen=True)
class ToolFailure:
    tool_name: str
    code: str
    reason: str
    # "gemini" / "codex" for the image tools, "veo" for the clip tool.
    provider: str
    billed: bool


def _tool_payload(tool_response: Any) -> dict:
    """The first JSON object among the response's text blocks, else `{}`."""

    for block in tool_response if isinstance(tool_response, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                payload = json.loads(block.get("text") or "")
            except ValueError:
                continue
            if isinstance(payload, dict):
                return payload
    return {}


def _text_notes(tool_response: Any) -> list[str]:
    """The response's text blocks that aren't JSON."""

    notes = []
    for block in tool_response if isinstance(tool_response, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text") or ""
            try:
                json.loads(text)
            except ValueError:
                notes.append(text)
    return notes


def _provider(tool_name: str, tool_input: dict) -> str:
    if tool_name == VEO_CLIP_TOOL:
        return "veo"
    return str((tool_input or {}).get("image_provider") or "gemini")


def _failure_billed(tool_name: str, failure: dict) -> bool:
    """Whether a failed paid call still cost money.

    An image tool that returned a failure drew no image and reports $0. A Veo
    clip bills once it was submitted: only a refused submit (an `SDK_ERROR`
    with no operation to dump) or a seed rejected before submitting is free.
    A poll failure after a successful submit is billed.
    """

    if tool_name != VEO_CLIP_TOOL:
        return False
    code = str(failure.get("code") or "")
    if code == "UNSAFE_SEED":
        return False
    if code == "SDK_ERROR" and not failure.get("operation_dump_path"):
        return False
    return True


class PaidToolGuard:
    def __init__(self, *, limit_usd: float, costs: dict[str, float]):
        self.limit_usd = limit_usd
        self.costs = dict(costs)
        self.allowed_usd = 0.0
        self.billed_usd = 0.0
        self.denial: str | None = None
        self.failures: list[ToolFailure] = []
        # Every plain-text block a successful image call returned after its
        # JSON payload -- among them the note naming the likeness-ladder rung
        # the image was really drawn at, which the Story 4.5 reviewer needs.
        self.image_notes: list[str] = []

    async def _pre_tool_use(self, input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        name = input_data.get("tool_name", "")
        if name not in self.costs:
            return {}
        cost = self.costs[name]
        # The epsilon keeps float sums from denying a call the budget exactly covers.
        if self.allowed_usd + cost > self.limit_usd + 1e-9:
            reason = (
                f"Budget guard: {name} costs ${cost:.4f}, ${self.allowed_usd:.4f} of paid calls were "
                f"already allowed in this session, and this session's paid-call limit is ${self.limit_usd:.4f}"
            )
            self.denial = reason
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        self.allowed_usd += cost
        return {}

    async def _post_tool_use(self, input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        name = input_data.get("tool_name", "")
        if name not in self.costs:
            return {}
        failure = _tool_payload(input_data.get("tool_response")).get("failure")
        if not isinstance(failure, dict):
            self.billed_usd += self.costs[name]
            if name in IMAGE_TOOLS:
                self.image_notes.extend(_text_notes(input_data.get("tool_response")))
            return {}
        billed = _failure_billed(name, failure)
        if billed:
            self.billed_usd += self.costs[name]
        else:
            # Nothing was spent, so a retry in this session isn't counted twice.
            self.allowed_usd -= self.costs[name]
        self.failures.append(ToolFailure(
            tool_name=name,
            code=str(failure.get("code") or ""),
            reason=str(failure.get("reason") or ""),
            provider=_provider(name, input_data.get("tool_input") or {}),
            billed=billed,
        ))
        return {}

    async def _post_tool_use_failure(self, input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        name = input_data.get("tool_name", "")
        if name not in self.costs:
            return {}
        # A crashed handler may have reached its vendor first; charge it.
        self.billed_usd += self.costs[name]
        self.failures.append(ToolFailure(
            tool_name=name,
            code="HANDLER_CRASH",
            reason=str(input_data.get("error") or ""),
            provider=_provider(name, input_data.get("tool_input") or {}),
            billed=True,
        ))
        return {}

    def hooks(self) -> dict:
        names = list(self.costs)
        return {
            "PreToolUse": [HookMatcher(matcher=name, hooks=[self._pre_tool_use]) for name in names],
            "PostToolUse": [HookMatcher(matcher=name, hooks=[self._post_tool_use]) for name in names],
            "PostToolUseFailure": [
                HookMatcher(matcher=name, hooks=[self._post_tool_use_failure]) for name in names
            ],
        }
