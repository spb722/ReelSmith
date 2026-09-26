"""Story 4.1: agent know-how lives in the `book-reels` plugin, loaded per agent,
and no developer tooling reaches a pipeline agent."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage

import orchestrator.run as run
from orchestrator.agents._skills import (
    PIPELINE_ENV,
    PLUGIN_DIR,
    SkillIsolationError,
    assert_isolated,
    check_init_message,
    plugin_read_guard,
    plugin_read_rule,
)
from orchestrator.state.run_manifest import load_run_manifest
from orchestrator.tests.test_full_chain_resume import (  # noqa: F401 -- `chain` is a fixture
    chain,
    still_visual_plan_for,
    subtitle_cues_for,
    veo_visual_plan_for,
)
from orchestrator.tests.test_preflight import make_settings
from orchestrator.tests.test_story_agent import stage  # noqa: F401 -- fixture

SKILLS_DIR = PLUGIN_DIR / "skills"
EXPECTED_SKILLS = {
    "asset-analysis", "story-craft", "story-craft-easy", "visual-planning", "source-fidelity",
    "veo-production", "still-production", "visual-qa", "character-likeness-qa",
}
AGENT_SKILLS = {
    "asset_analyst": ["book-reels:asset-analysis"],
    "story_agent": ["book-reels:story-craft", "book-reels:source-fidelity"],
    "visual_agent": ["book-reels:visual-planning", "book-reels:source-fidelity"],
    "veo_agent": ["book-reels:veo-production", "book-reels:visual-qa", "book-reels:character-likeness-qa"],
    "stills_agent": ["book-reels:still-production", "book-reels:visual-qa", "book-reels:character-likeness-qa"],
}
AGENT_CALLS = {
    "asset_analyst": ("analyze_assets", {"assets": []}),
    "story_agent": ("write_story", {"analyzed_assets": []}),
    "visual_agent": ("plan_visuals", {"final_story_plan": {}, "analyzed_assets": []}),
    "veo_agent": ("generate_veo_asset", {
        "shot": {}, "asset": {}, "subtitle_text": "", "settings": {}, "attempt": 1,
    }),
    "stills_agent": ("generate_still_asset", {
        "shot": {}, "asset": {}, "subtitle_text": "", "settings": {}, "attempt": 1,
    }),
}
GUARDED = {"story_agent", "visual_agent", "veo_agent", "stills_agent"}


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def init_message(skills, plugins=("book-reels",)):
    return SystemMessage(subtype="init", data={
        "skills": list(skills),
        "plugins": [{"name": name, "path": "/p", "source": f"{name}@inline"} for name in plugins],
    })


def result_message():
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="skills-test", total_cost_usd=0.1, structured_output={},
    )


# --- The plugin itself -------------------------------------------------------

def test_plugin_manifest_names_book_reels():
    manifest = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "book-reels"
    assert PLUGIN_DIR.is_absolute()


def test_plugin_holds_exactly_the_nine_skills():
    assert {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()} == EXPECTED_SKILLS
    for name in EXPECTED_SKILLS:
        assert (SKILLS_DIR / name / "SKILL.md").is_file()


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_meets_nfr6_hygiene(name):
    skill_md = SKILLS_DIR / name / "SKILL.md"
    fields = frontmatter(skill_md)
    assert fields["name"] == name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert "claude" not in name and "anthropic" not in name
    description = fields["description"]
    assert 0 < len(description) <= 1024
    assert "Use when" in description  # says when, after saying what
    assert len(skill_md.read_text(encoding="utf-8").splitlines()) <= 500


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_reference_files_are_one_level_deep_and_cited(name):
    skill_dir = SKILLS_DIR / name
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    for path in skill_dir.rglob("*"):
        if path.is_dir() or path.name == "SKILL.md":
            continue
        relative = path.relative_to(skill_dir)
        assert relative.parts[0] == "references" and len(relative.parts) == 2, relative
        assert relative.suffix == ".md"
        assert f"({relative.as_posix()})" in body, f"{name} does not cite {relative}"
    for cited in re.findall(r"\]\((references/[^)]+)\)", body):
        assert (skill_dir / cited).is_file(), cited


def test_likeness_ladder_stays_in_code():
    from orchestrator.tools import gemini_tools

    assert callable(gemini_tools.build_prompt_ladder)
    for skill_md in SKILLS_DIR.rglob("*.md"):
        assert "build_prompt_ladder" not in skill_md.read_text(encoding="utf-8")


# --- Per-agent options -------------------------------------------------------

def _capture_options(module_name, monkeypatch):
    module = importlib.import_module(f"orchestrator.agents.{module_name}")
    monkeypatch.setattr(module, "load_settings", lambda: SimpleNamespace(claude_model="test-model"))
    captured = []

    async def fake_query(*, prompt, options):
        captured.append(options)
        yield result_message()

    monkeypatch.setattr(module, "query", fake_query)
    function_name, kwargs = AGENT_CALLS[module_name]
    asyncio.run(getattr(module, function_name)(max_budget_usd=1.0, **kwargs))
    return module, captured[0]


@pytest.mark.parametrize("module_name", sorted(AGENT_SKILLS))
def test_agent_options_carry_exactly_its_skills_and_the_plugin(module_name, monkeypatch):
    module, options = _capture_options(module_name, monkeypatch)
    assert options.skills == AGENT_SKILLS[module_name]
    assert options.plugins == [{"type": "local", "path": str(PLUGIN_DIR)}]
    assert Path(options.plugins[0]["path"]).is_absolute()
    assert options.env == PIPELINE_ENV
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    # Split lists: bare Skill is a tool, never an allow rule.
    assert "Skill" in options.tools and "Read" in options.tools
    assert "Skill" not in options.allowed_tools
    assert plugin_read_rule() in options.allowed_tools
    assert options.tools != options.allowed_tools
    assert not options.add_dirs or module_name == "asset_analyst"
    prompt = options.system_prompt
    for skill in AGENT_SKILLS[module_name]:
        assert skill in prompt, f"{module_name} prompt does not tell the agent to use {skill}"


@pytest.mark.parametrize(("module_name", "function_name", "kwargs"), [
    ("story_agent", "write_story", {"analyzed_assets": []}),
    ("story_critic", "critique_story", {"draft_plan": {}, "analyzed_assets": []}),
])
def test_story_skill_setting_swaps_the_writing_skill(module_name, function_name, kwargs, monkeypatch):
    monkeypatch.setenv("STORY_SKILL", "story-craft-easy")
    module = importlib.import_module(f"orchestrator.agents.{module_name}")
    monkeypatch.setattr(module, "load_settings",
                        lambda: SimpleNamespace(claude_model="m", critic_model="m"))
    captured = []

    async def fake_query(*, prompt, options):
        captured.append(options)
        yield result_message()

    monkeypatch.setattr(module, "query", fake_query)
    asyncio.run(getattr(module, function_name)(max_budget_usd=1.0, **kwargs))
    options = captured[0]
    assert options.skills == ["book-reels:story-craft-easy", "book-reels:source-fidelity"]
    assert "load book-reels:story-craft-easy and" in options.system_prompt


@pytest.mark.parametrize("module_name", sorted(AGENT_SKILLS))
def test_read_guard_applies_to_every_agent_but_the_analyst(module_name, monkeypatch):
    _, options = _capture_options(module_name, monkeypatch)
    if module_name in GUARDED:
        assert [m.matcher for m in options.hooks["PreToolUse"]] == ["Read"]
    else:
        assert not options.hooks
        assert "Read" in options.allowed_tools  # unrestricted, for the screenshots


def test_veo_and_stills_turn_ceilings_leave_room_for_skill_loads(monkeypatch):
    assert _capture_options("veo_agent", monkeypatch)[1].max_turns == 12
    assert _capture_options("stills_agent", monkeypatch)[1].max_turns == 10


def test_plugin_read_rule_is_absolute():
    assert plugin_read_rule() == f"Read(/{PLUGIN_DIR}/**)"
    assert plugin_read_rule().startswith("Read(//")


# --- Isolation checks --------------------------------------------------------

def test_setting_sources_must_be_empty():
    assert_isolated(ClaudeAgentOptions(setting_sources=[]))
    for bad in (["project"], None):
        with pytest.raises(SkillIsolationError, match="setting_sources"):
            assert_isolated(ClaudeAgentOptions(setting_sources=bad))


def test_clean_init_passes_and_logs(capsys):
    own = AGENT_SKILLS["story_agent"]
    check_init_message(init_message([f"book-reels:{s}" for s in sorted(EXPECTED_SKILLS)] + ["doctor"]), own)
    out = capsys.readouterr().out
    assert "book-reels:story-craft" in out and "plugin book-reels" in out


def test_skill_reference_reads_are_not_logged_as_screenshots(capsys):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    from orchestrator.agents._stream import log_tool_uses

    ref = str(SKILLS_DIR / "story-craft" / "references" / "self-review.md")
    log_tool_uses(AssistantMessage(content=[
        ToolUseBlock(id="1", name="Read", input={"file_path": ref}),
        ToolUseBlock(id="2", name="Read", input={"file_path": "/work/source_images/a.png"}),
    ], model="m"))
    out = capsys.readouterr().out
    assert "reading a skill reference: self-review.md" in out
    assert out.count("opening a screenshot") == 1


@pytest.mark.parametrize("foreign", ["bmad-build", "lossless-teacher", "debug"])
def test_foreign_skill_in_init_halts(foreign):
    with pytest.raises(SkillIsolationError, match=foreign):
        check_init_message(init_message(["book-reels:story-craft", "book-reels:source-fidelity", foreign]),
                           AGENT_SKILLS["story_agent"])


def test_missing_own_skill_in_init_halts():
    with pytest.raises(SkillIsolationError, match="missing: book-reels:source-fidelity"):
        check_init_message(init_message(["book-reels:story-craft"]), AGENT_SKILLS["story_agent"])


@pytest.mark.parametrize("plugins", [(), ("book-reels", "other"), ("other",)])
def test_wrong_plugins_in_init_halts(plugins):
    with pytest.raises(SkillIsolationError, match="plugin"):
        check_init_message(init_message(AGENT_SKILLS["story_agent"], plugins), AGENT_SKILLS["story_agent"])


def test_non_init_messages_are_ignored():
    check_init_message(SystemMessage(subtype="status", data={}), AGENT_SKILLS["story_agent"])
    check_init_message(result_message(), AGENT_SKILLS["story_agent"])


@pytest.mark.parametrize("module_name", sorted(AGENT_SKILLS))
def test_foreign_init_raises_before_any_model_turn_and_closes_stream(module_name, monkeypatch):
    module = importlib.import_module(f"orchestrator.agents.{module_name}")
    monkeypatch.setattr(module, "load_settings", lambda: SimpleNamespace(claude_model="test-model"))
    events = []

    async def fake_query(*, prompt, options):
        try:
            yield init_message([*options.skills, "lossless-teacher"])
            events.append("model turn")
            yield result_message()
        finally:
            events.append("closed")

    monkeypatch.setattr(module, "query", fake_query)
    function_name, kwargs = AGENT_CALLS[module_name]
    with pytest.raises(SkillIsolationError, match="lossless-teacher"):
        asyncio.run(getattr(module, function_name)(max_budget_usd=1.0, **kwargs))
    assert events == ["closed"]


# --- Read guard --------------------------------------------------------------

def _guard_decision(file_path, cwd="/tmp"):
    callback = plugin_read_guard()["PreToolUse"][0].hooks[0]
    return asyncio.run(callback({"tool_name": "Read", "tool_input": {"file_path": file_path}, "cwd": cwd}, "id", {}))


def test_read_guard_allows_plugin_files():
    target = SKILLS_DIR / "story-craft" / "references" / "self-review.md"
    assert _guard_decision(str(target)) == {}
    assert _guard_decision("self-review.md", cwd=str(target.parent)) == {}


@pytest.mark.parametrize("path", [
    "/etc/hosts",
    "CLAUDE.md",
    str(PLUGIN_DIR.parent / "run.py"),
    str(PLUGIN_DIR / ".." / "run.py"),
    str(PLUGIN_DIR) + "-sibling/x.md",
    "",
])
def test_read_guard_denies_outside_plugin(path):
    decision = _guard_decision(path)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert str(PLUGIN_DIR) in decision["permissionDecisionReason"]


# --- Stage halts ---------------------------------------------------------------

def test_bounded_stage_halts_on_isolation_error_without_retry(stage, monkeypatch):  # noqa: F811
    source, _, _ = stage
    agent_module = importlib.import_module("orchestrator.agents.story_agent")
    calls = []

    async def fake_query(*, prompt, options):
        calls.append(options)
        yield init_message([*options.skills, "bmad-build"])
        yield result_message()

    monkeypatch.setattr(agent_module, "query", fake_query)
    assert run.main(["--project", source.parent.name]) == 1
    assert len(calls) == 1
    reports = list(run.RUN_STATE_DIR.glob("failure_story_agent_*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert "bmad-build" in report["reason"]
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.iteration_counts["story_agent"] == 0
    assert manifest.budget_spent_usd == 0


@pytest.mark.parametrize("kind", ["veo", "stills"])
def test_shot_stage_isolation_halt_charges_nothing(kind, chain, monkeypatch):  # noqa: F811
    _, asset_id = chain
    plan = veo_visual_plan_for(asset_id) if kind == "veo" else still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    calls = []

    async def isolated_agent(**kwargs):
        calls.append(kwargs["attempt"])
        raise SkillIsolationError("skills from outside the book-reels plugin: lossless-teacher")

    monkeypatch.setattr(run, "generate_veo_asset" if kind == "veo" else "generate_still_asset", isolated_agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    spent_before = manifest.budget_spent_usd
    stage_fn = run.run_veo_stage if kind == "veo" else run.run_stills_stage
    assert asyncio.run(stage_fn(make_settings(), manifest)) == 1
    assert calls == [1]
    assert manifest.budget_spent_usd == spent_before
    shot = plan.shots[0].sequence
    assert load_run_manifest(run.RUN_STATE_DIR).iteration_counts[f"{kind}_agent_shot_{shot}"] == 0
    reports = sorted(run.RUN_STATE_DIR.glob(f"failure_{kind}_agent_*.json"))
    assert reports
    assert "lossless-teacher" in json.loads(reports[-1].read_text())["reason"]
