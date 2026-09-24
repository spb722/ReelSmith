"""The pipeline's skills plugin and the checks that keep developer tooling out.

Every pipeline agent loads its know-how from the one local `book-reels`
plugin at `orchestrator/skills_plugin/`. Paths decided by Story 4.1's spike
(`docs/implementation-artifacts/spike-4-1-sdk-skills.md`):

- skills are named in the qualified `book-reels:<skill>` form;
- the init message's `skills` list is unfiltered, so the check is that every
  listed skill comes from the plugin (or is the CLI built-in `doctor`), the
  only plugin is `book-reels`, and the agent's own skills are all present;
- bundled CLI skills are switched off through the environment;
- `Read` can't be narrowed by permission rules, so a `PreToolUse` hook denies
  any `Read` outside the plugin folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, SystemMessage

from orchestrator.progress import log

PLUGIN_NAME = "book-reels"
# Absolute, because `enter_project` changes the cwd to `projects/<name>`.
PLUGIN_DIR = (Path(__file__).resolve().parent.parent / "skills_plugin").resolve()
PLUGIN = {"type": "local", "path": str(PLUGIN_DIR)}
PIPELINE_ENV = {"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}
# The one CLI built-in skill the bundled-skills switch leaves in the listing.
_CLI_BUILTIN_SKILLS = frozenset({"doctor"})


class SkillIsolationError(RuntimeError):
    """A pipeline agent would run with settings or skills from outside its plugin."""


def qualified(*names: str) -> list[str]:
    return [f"{PLUGIN_NAME}:{name}" for name in names]


def plugin_read_rule() -> str:
    # `//` marks an absolute path in a permission rule.
    return f"Read(/{PLUGIN_DIR}/**)"


async def _deny_read_outside_plugin(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    raw = (input_data.get("tool_input") or {}).get("file_path") or ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(input_data.get("cwd") or Path.cwd()) / path
    path = path.resolve()
    if raw and (path == PLUGIN_DIR or PLUGIN_DIR in path.parents):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Read is limited to the skills plugin folder {PLUGIN_DIR}",
        }
    }


def plugin_read_guard() -> dict:
    return {"PreToolUse": [HookMatcher(matcher="Read", hooks=[_deny_read_outside_plugin])]}


def assert_isolated(options: ClaudeAgentOptions) -> None:
    """`setting_sources=[]` is what structurally keeps CLAUDE.md and `.claude/` out."""

    if options.setting_sources != []:
        raise SkillIsolationError(
            f"Pipeline agents must run with setting_sources=[], got {options.setting_sources!r}"
        )


def check_init_message(message: object, skills: list[str]) -> None:
    """Halt on any skill or plugin from outside `book-reels`; log what loaded."""

    if not (isinstance(message, SystemMessage) and message.subtype == "init"):
        return
    listed = list(message.data.get("skills") or [])
    plugins = [plugin.get("name") for plugin in message.data.get("plugins") or []]
    foreign = [
        name for name in listed
        if not name.startswith(f"{PLUGIN_NAME}:") and name not in _CLI_BUILTIN_SKILLS
    ]
    missing = [name for name in skills if name not in listed]
    problems = []
    if foreign:
        problems.append(f"skills from outside the {PLUGIN_NAME} plugin: {', '.join(foreign)}")
    if missing:
        problems.append(f"this agent's skills are missing: {', '.join(missing)}")
    if plugins != [PLUGIN_NAME]:
        problems.append(f"expected only the {PLUGIN_NAME} plugin, got {plugins}")
    if problems:
        raise SkillIsolationError("Agent session is not isolated -- " + "; ".join(problems))
    loaded = [name for name in listed if name in skills]
    log(f"Skills for this stage: {', '.join(loaded)} (plugin {', '.join(plugins)})", indent=2)
