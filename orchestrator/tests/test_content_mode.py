"""SOURCE_MODE=content: screenshots give only content, pictures are invented."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from orchestrator.agents import visual_agent
from orchestrator.contracts.veo import shot_fingerprint
from orchestrator.settings import SettingsError, load_settings
from orchestrator.tests.test_codex_image_tools import fake_codex, workspace_of
from orchestrator.tests.test_gemini_tools import (
    FakeCandidate, FakeContent, FakeGeminiClient, FakeInlineData, FakePart, FakeResponse,
    _enable_character, _png_bytes, _write_character, _write_source_image, asset_without_figure,
    shot_dict,
)
from orchestrator.tests.test_skills_plugin import result_message
from orchestrator.tools import codex_image_tools as codex
from orchestrator.tools.gemini_tools import (
    VISUAL_PLAN_FILE, build_seed_spec, build_still_spec, generate_still_image,
)

STYLE = "Flat editorial illustration, teal, cream and coral, soft grain, warm window light."


@pytest.fixture(autouse=True)
def working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def content_mode(monkeypatch):
    monkeypatch.setenv("SOURCE_MODE", "content")


def write_plan(*, shows_character: bool = False, sequence: int = 1) -> None:
    VISUAL_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    VISUAL_PLAN_FILE.write_text(json.dumps({
        "overall_visual_style": STYLE,
        "shots": [{"sequence": sequence, "shows_character": shows_character}],
    }), encoding="utf-8")


def text_page(tmp_path: Path) -> dict:
    source = tmp_path / "source_images" / "page.png"
    _write_source_image(source)
    asset = asset_without_figure(source)
    asset["analysis"]["visual"]["description"] = "A phone screenshot of a page of text"
    return asset


# --- The setting -------------------------------------------------------------

def test_source_mode_defaults_to_content_and_images(monkeypatch):
    monkeypatch.delenv("SOURCE_MODE", raising=False)
    assert load_settings().source_mode == "content_and_images"


def test_source_mode_accepts_content_in_any_case(monkeypatch):
    monkeypatch.setenv("SOURCE_MODE", " Content ")
    assert load_settings().source_mode == "content"


def test_unknown_source_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("SOURCE_MODE", "images")
    with pytest.raises(SettingsError, match="SOURCE_MODE"):
        load_settings()


# --- The shot field keeps old fingerprints ---------------------------------

def test_shows_character_false_keeps_the_fingerprint_of_a_shot_saved_before_it_existed():
    """Otherwise every approved shot of every existing reel would read as stale."""
    old = shot_dict()
    old.pop("shows_character")
    assert shot_fingerprint(old) == shot_fingerprint(shot_dict(shows_character=False))
    assert shot_fingerprint(old) != shot_fingerprint(shot_dict(shows_character=True))


def test_fingerprint_of_a_shot_without_the_field_is_unchanged():
    # Pinned: computed by the code before `shows_character` existed (commit c8e04f0).
    assert shot_fingerprint(shot_dict()) == (
        "7a28951749bf0731e490a938b4995b8372cfe687ce178dd8e123d5c83bc0dc00"
    )


# --- The visual agent's skills ------------------------------------------------

def _visual_agent_options(monkeypatch):
    monkeypatch.setattr(visual_agent, "load_settings", lambda: SimpleNamespace(claude_model="m"))
    captured = []

    async def fake_query(*, prompt, options):
        captured.append(options)
        yield result_message()

    monkeypatch.setattr(visual_agent, "query", fake_query)
    asyncio.run(visual_agent.plan_visuals({}, [], max_budget_usd=1.0))
    return captured[0]


def test_content_mode_loads_the_content_skills(monkeypatch, content_mode):
    options = _visual_agent_options(monkeypatch)
    assert options.skills == ["book-reels:visual-planning-content", "book-reels:source-fidelity-content"]
    assert "load book-reels:visual-planning-content and book-reels:source-fidelity-content" in (
        options.system_prompt
    )
    assert "{" not in options.system_prompt


def test_default_mode_keeps_the_original_skills_and_prompt(monkeypatch):
    monkeypatch.delenv("SOURCE_MODE", raising=False)
    options = _visual_agent_options(monkeypatch)
    assert options.skills == ["book-reels:visual-planning", "book-reels:source-fidelity"]
    assert options.system_prompt == visual_agent.visual_agent.prompt


# --- Image requests -----------------------------------------------------------

@pytest.mark.parametrize("builder,mode", [(build_seed_spec, "VEO"), (build_still_spec, "STILL")])
def test_content_spec_draws_from_the_plan_not_the_screenshot(builder, mode, tmp_path, content_mode):
    write_plan()
    spec = builder(shot_dict(generation_mode=mode, frame_composition="A woman at a window."),
                   text_page(tmp_path), "Narration.")

    assert spec["send_source_image"] is False
    prompt = spec["prompt"]
    assert STYLE in prompt
    assert "A woman at a window." in prompt
    assert "screenshot" not in prompt.lower()
    assert "page of text" not in prompt
    assert prompt.endswith(
        "produce the clean plate without it."
    ), "the no-text rule must stay last"
    # Still names the cited page, which the seed and still records point back to.
    assert spec["source_image_path"].endswith("page.png")


@pytest.mark.parametrize("builder,mode", [(build_seed_spec, "VEO"), (build_still_spec, "STILL")])
def test_character_follows_the_plan_in_content_mode(builder, mode, tmp_path, monkeypatch, content_mode):
    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    asset = text_page(tmp_path)  # a text page never has a figure

    write_plan(shows_character=True)
    with_character = builder(shot_dict(generation_mode=mode), asset, "Narration.")
    assert with_character["character_reference_paths"] == [str(body), str(face)]
    assert "The main person in the scene above is this character" in with_character["prompt"]
    assert [rung["reference_image_paths"] for rung in with_character["prompt_ladder"]][-1] == []

    write_plan(shows_character=False)
    without = builder(shot_dict(generation_mode=mode), asset, "Narration.")
    assert without["character_reference_paths"] == []
    assert "CHARACTER SUBSTITUTION" not in without["prompt"]


def test_seed_start_level_skips_to_the_rung_without_the_character(tmp_path, monkeypatch, content_mode):
    """After Veo refuses the character's face, the retry must drop the character."""
    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    write_plan(shows_character=True)
    for level in (1, 2, 5):
        spec = build_seed_spec(shot_dict(), text_page(tmp_path), "Narration.", start_level=level)
        assert spec["character_reference_paths"] == []


def test_content_spec_without_a_saved_plan_fails_loudly(tmp_path, content_mode):
    with pytest.raises(FileNotFoundError):
        build_still_spec(shot_dict(generation_mode="STILL"), text_page(tmp_path), "Narration.")


def test_content_spec_for_a_shot_missing_from_the_plan_fails_loudly(tmp_path, content_mode):
    write_plan(sequence=2)
    with pytest.raises(ValueError, match="Shot 1"):
        build_still_spec(shot_dict(generation_mode="STILL"), text_page(tmp_path), "Narration.")


def test_default_mode_still_recomposes_the_screenshot(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_MODE", raising=False)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), text_page(tmp_path), "Narration.")
    assert "send_source_image" not in spec
    assert "screenshot" in spec["prompt"]


# --- Gemini gets no screenshot --------------------------------------------------

def test_gemini_call_sends_no_screenshot_in_content_mode(tmp_path, monkeypatch, content_mode):
    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    write_plan(shows_character=True)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), text_page(tmp_path), "Narration.")
    response = FakeResponse([FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))])
    client = FakeGeminiClient(response)

    result, _ = generate_still_image(client, spec, model="m", cost_usd=0.04)

    contents = client.models.calls[0]["contents"]
    # Only the two character references and the prompt: no scene image.
    assert len(contents) == 3
    assert [image.size for image in contents[:2]] == [(12, 30), (10, 10)]
    assert contents[-1] == spec["prompt"]
    assert Path(result.local_image_path).is_file()


# --- Codex gets no scene file -------------------------------------------------

def test_codex_stages_and_attaches_no_scene_without_a_screenshot(monkeypatch, tmp_path):
    monkeypatch.setattr(codex.shutil, "which", lambda name: "/usr/local/bin/codex")
    sheet = tmp_path / "character.png"
    Image.new("RGB", (200, 400), "green").save(sheet)
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_codex(recorder=calls))

    codex.run_codex_image_edit(
        source_image_path=None, prompt="a quiet room", reference_image_paths=[sheet],
        timeout_seconds=60,
    )

    command, kwargs = calls[0]
    workspace = workspace_of(command)
    attached = [command[i + 1] for i, part in enumerate(command) if part == "--image"]
    assert attached == [str(workspace / "character_01.png")]
    assert codex.SCENE_FILENAME not in kwargs["input"]
    assert "Create one new image from the description below." in kwargs["input"]
    assert '"character_01.png" is the character sheet' in kwargs["input"]


def test_codex_prompt_with_no_scene_and_no_character_mentions_neither():
    prompt = codex.build_codex_prompt("a quiet room", [], has_scene=False)
    assert codex.SCENE_FILENAME not in prompt
    assert "character sheet" not in prompt
    assert "a quiet room" in prompt


# --- The independent reviewer agrees with the image tool -----------------------

def _reviewer_references(asset: dict, level_note: str | None = None):
    import orchestrator.run as run
    from orchestrator.tools.gemini_tools import describe_ladder_outcome

    guard = SimpleNamespace(image_notes=[describe_ladder_outcome({"level": level_note})] if level_note is not None else [])
    wrapped = SimpleNamespace(model_dump=lambda mode=None: asset)
    return run._reviewer_character_context(wrapped, 1, guard, 0)


def test_reviewer_gets_the_character_when_the_plan_says_so(tmp_path, monkeypatch, content_mode):
    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    write_plan(shows_character=True)
    references, _ = _reviewer_references(text_page(tmp_path), level_note=0)
    assert references == [str(body), str(face)]
    # Drawn at the rung without the character: nothing to compare against.
    assert _reviewer_references(text_page(tmp_path), level_note=2)[0] == []


def test_reviewer_ignores_a_figure_in_the_screenshot_when_the_plan_says_no(tmp_path, monkeypatch, content_mode):
    from orchestrator.tests.test_gemini_tools import asset_with_figure

    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    write_plan(shows_character=False)
    source = tmp_path / "source_images" / "drawn.png"
    _write_source_image(source)
    assert _reviewer_references(asset_with_figure(source)) == ([], "")


# --- One mode per reel ------------------------------------------------------------

from orchestrator.tests.test_visual_agent import (  # noqa: E402
    failure_report, mock_query, sdk_result, stage,  # noqa: F401 -- stage is a fixture
)


def test_a_fresh_plan_records_its_mode(stage, monkeypatch, content_mode):
    source, _, output = stage
    mock_query(monkeypatch, [sdk_result(output)])
    import orchestrator.run as run

    assert run.main(["--project", source.parent.name]) == 0
    assert json.loads(run.VISUAL_PLAN_FILE.read_text())["source_mode"] == "content"


@pytest.mark.parametrize("saved,current", [
    (None, "content"),  # a plan from before SOURCE_MODE existed was made from artwork
    ("content", "content_and_images"),
])
def test_resuming_in_the_other_mode_halts_before_any_call(stage, monkeypatch, saved, current):
    import orchestrator.run as run

    source, _, output = stage
    if saved is not None:
        output = {**output, "source_mode": saved}
    run.VISUAL_PLAN_FILE.write_text(json.dumps(output))
    before = run.VISUAL_PLAN_FILE.read_bytes()
    monkeypatch.setenv("SOURCE_MODE", current)
    calls = mock_query(monkeypatch, [])

    assert run.main(["--project", source.parent.name]) == 1
    assert calls == []
    assert "Re-run with SOURCE_MODE=" in failure_report()["reason"]
    assert run.VISUAL_PLAN_FILE.read_bytes() == before


def test_resuming_in_the_same_mode_skips_as_before(stage, monkeypatch, content_mode):
    import orchestrator.run as run

    source, _, output = stage
    run.VISUAL_PLAN_FILE.write_text(json.dumps({**output, "source_mode": "content"}))
    calls = mock_query(monkeypatch, [])
    assert run.main(["--project", source.parent.name]) == 0
    assert calls == []


# --- Art styles ---------------------------------------------------------------------

from orchestrator import styles  # noqa: E402


def test_every_shipped_style_loads_and_the_default_exists():
    names = styles.available_styles()
    assert styles.DEFAULT_STYLE in names
    assert {"flat_editorial", "watercolour", "childrens_gouache", "risograph"} <= set(names)
    for name in names:
        guide = styles.load_style(name).guide()
        assert guide and ".." not in guide


@pytest.mark.parametrize("body,match", [
    (None, "No style named 'nope'"),
    ({"name": "nope", "description": "d", "palette": ["a"], "lighting": "l", "mood": "m",
      "avoid": [], "colour": "typo"}, "not valid"),
    ({"name": "other", "description": "d", "palette": ["a"], "lighting": "l", "mood": "m",
      "avoid": []}, "must be 'nope'"),
])
def test_a_bad_style_is_refused_clearly(tmp_path, monkeypatch, body, match):
    monkeypatch.setattr(styles, "STYLES_DIR", tmp_path)
    if body is not None:
        (tmp_path / "nope.json").write_text(json.dumps(body))
    with pytest.raises(styles.StyleError, match=match):
        styles.load_style("nope")


def _run(stage_args, *extra):
    import orchestrator.run as run

    source = stage_args[0]
    return run.main(["--project", source.parent.name, *extra])


def test_a_fresh_content_plan_is_stamped_with_the_chosen_style(stage, monkeypatch, content_mode):
    import orchestrator.run as run

    source, _, output = stage
    calls = mock_query(monkeypatch, [sdk_result({**output, "overall_visual_style": "agent's own idea"})])

    assert _run(stage, "--style", "watercolour") == 0
    saved = json.loads(run.VISUAL_PLAN_FILE.read_text())
    guide = styles.load_style("watercolour").guide()
    assert saved["overall_visual_style"] == guide
    assert saved["style_name"] == "watercolour"
    assert guide in calls[0][0], "the agent is told the style it must plan for"


def test_the_default_style_is_flat_editorial(stage, monkeypatch, content_mode):
    import orchestrator.run as run

    _, _, output = stage
    mock_query(monkeypatch, [sdk_result(output)])
    assert _run(stage) == 0
    assert json.loads(run.VISUAL_PLAN_FILE.read_text())["style_name"] == "flat_editorial"


def test_an_unknown_style_stops_before_any_call(stage, monkeypatch, content_mode):
    import orchestrator.run as run

    calls = mock_query(monkeypatch, [])
    assert _run(stage, "--style", "vaporwave") == 1
    assert calls == []
    report = json.loads(next(run.RUN_STATE_DIR.glob("failure_settings_*.json")).read_text())
    assert "No style named 'vaporwave'" in report["reason"]


def test_resuming_in_another_style_halts(stage, monkeypatch, content_mode):
    import orchestrator.run as run

    _, _, output = stage
    run.VISUAL_PLAN_FILE.write_text(json.dumps({**output, "source_mode": "content", "style_name": "risograph"}))
    calls = mock_query(monkeypatch, [])
    assert _run(stage, "--style", "watercolour") == 1
    assert calls == []
    assert "Re-run with --style risograph" in failure_report()["reason"]


def test_a_content_plan_from_before_styles_resumes_untouched(stage, monkeypatch, content_mode):
    import orchestrator.run as run

    _, _, output = stage
    run.VISUAL_PLAN_FILE.write_text(json.dumps({**output, "source_mode": "content"}))
    before = run.VISUAL_PLAN_FILE.read_bytes()
    calls = mock_query(monkeypatch, [])
    assert _run(stage, "--style", "watercolour") == 0
    assert calls == []
    assert run.VISUAL_PLAN_FILE.read_bytes() == before


def test_style_is_ignored_when_pictures_come_from_the_screenshots(stage, monkeypatch):
    import orchestrator.run as run

    _, _, output = stage
    mock_query(monkeypatch, [sdk_result(output)])
    assert _run(stage, "--style", "watercolour") == 0
    saved = json.loads(run.VISUAL_PLAN_FILE.read_text())
    assert saved["style_name"] == ""
    assert saved["overall_visual_style"] == output["overall_visual_style"]
