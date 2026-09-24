from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from orchestrator import cover
from orchestrator.contracts.cover import (
    COVER_HEIGHT,
    COVER_WIDTH,
    GRID_CROP_ASPECT_RATIO,
    CoverPlanContract,
)
from orchestrator.tools import cover_tools

NARRATION = (
    "Mark's finger hovered over the delete key. The resignation letter was already "
    "written. But suddenly, that boring desk job felt like a warm blanket. Then he saw "
    "it. The dread lacked logic. It was simply fear wearing a mask. "
    "What has your fear disguised as common sense?"
)


def artwork(tmp_path: Path, colour="navy", size=(540, 960)) -> Path:
    path = tmp_path / "art.png"
    Image.new("RGB", size, colour).save(path)
    return path


# --- the hook must be the reel's own words ---------------------------------


def test_a_quoted_phrase_is_accepted():
    assert cover.hook_is_from_narration("fear wearing a mask", NARRATION)


def test_a_lightly_compressed_phrase_is_accepted():
    """The agent is allowed to tighten a line, just not to invent one."""
    assert cover.hook_is_from_narration("The dread lacked logic", NARRATION)
    assert cover.hook_is_from_narration("fear disguised as common sense", NARRATION)


def test_case_and_punctuation_do_not_matter():
    assert cover.hook_is_from_narration("FEAR WEARING A MASK.", NARRATION)


def test_invented_marketing_copy_is_rejected():
    """The cover and the opening voiceover have to agree, so copy the reel
    never says must not reach the image."""
    assert not cover.hook_is_from_narration("Buy my course now", NARRATION)
    assert not cover.hook_is_from_narration("Ten habits of winners", NARRATION)


def test_a_single_invented_word_is_enough_to_reject():
    assert not cover.hook_is_from_narration("fear wearing a costume", NARRATION)


def test_an_empty_hook_is_rejected():
    assert not cover.hook_is_from_narration("", NARRATION)
    assert not cover.hook_is_from_narration("   ", NARRATION)


# --- the art brief ---------------------------------------------------------


def plan_for(**overrides) -> CoverPlanContract:
    base = dict(
        base_shot_sequence=6,
        hook_text="fear wearing a mask",
        art_direction="Push in on the cracking mask; keep the sky clear.",
        rationale="It carries the reel's central image.",
    )
    base.update(overrides)
    return CoverPlanContract(**base)


def test_the_brief_asks_for_the_headline_spelled_exactly():
    """Codex draws the words now. Side by side in test/try_cover.py the
    composited version looked like type dropped on a picture; this one looked
    designed, and spelled the headline right every time."""
    prompt = cover.build_cover_prompt(plan_for(), "The turn: the mask comes apart.")
    assert '"fear wearing a mask"' in prompt
    assert "spelled letter for letter" in prompt
    assert "only text in the image" in prompt


def test_the_brief_makes_the_symbol_the_hero():
    """The instruction that won the sweep was not about type at all -- it was
    enlarging the story's symbolic object and shrinking the character."""
    prompt = cover.build_cover_prompt(plan_for(), "goal")
    assert "central symbolic object" in prompt
    assert "smaller and lower" in prompt


def test_the_brief_carries_the_art_direction_and_the_shot_goal():
    prompt = cover.build_cover_prompt(plan_for(), "The turn: the mask comes apart.")
    assert "cracking mask" in prompt
    assert "The turn: the mask comes apart." in prompt


def test_the_brief_asks_for_a_recomposition_not_a_restyle():
    prompt = cover.build_cover_prompt(plan_for(), "goal")
    assert "not a restyle" in prompt
    assert "photorealism" in prompt


# --- the profile grid crop -------------------------------------------------


def test_the_grid_preview_is_the_portrait_slice(tmp_path):
    cover_path = tmp_path / "cover.png"
    Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), "navy").save(cover_path)
    preview = cover_tools.write_grid_preview(cover_path, tmp_path / "grid.png")

    with Image.open(preview) as image:
        assert image.width == COVER_WIDTH
        assert image.height == pytest.approx(COVER_WIDTH / GRID_CROP_ASPECT_RATIO, abs=2)
        # Taller than it is wide, and shorter than the full cover.
        assert image.width < image.height < COVER_HEIGHT


# --- frame resolution ------------------------------------------------------


def test_a_still_shot_is_used_directly(tmp_path):
    path = artwork(tmp_path)
    assert cover.resolve_base_frame({"local_path": str(path)}) == path


def test_a_video_shot_has_a_frame_taken_out_of_it(tmp_path, monkeypatch):
    """Video shots must be usable as covers too, not silently unavailable."""
    called = {}

    def fake_extract(video_path, output_path, **kwargs):
        called["video"] = video_path
        Image.new("RGB", (10, 20), "red").save(output_path)
        return output_path

    monkeypatch.setattr(cover, "extract_video_frame", fake_extract)
    monkeypatch.setattr(cover, "COVER_BASE_FRAME_FILE", tmp_path / "base.png")

    result = cover.resolve_base_frame({"local_path": "generated/veo/shot_01.mp4"})
    assert called["video"] == Path("generated/veo/shot_01.mp4")
    assert result.is_file()


# --- the contract ----------------------------------------------------------


def test_hook_word_count_counts_words():
    assert plan_for(hook_text="fear wearing a mask").hook_word_count() == 4


def test_the_plan_rejects_unknown_fields():
    with pytest.raises(Exception):
        CoverPlanContract(
            base_shot_sequence=1, hook_text="a", art_direction="b",
            rationale="c", nonsense="d",
        )


def test_the_brief_pins_the_headline_to_the_upper_half():
    """Regression: told only to avoid "the extreme edges", Codex set the
    headline across the bottom. The grid preview showed the last line -- and
    the question mark with it -- cropped away entirely."""
    prompt = cover.build_cover_prompt(plan_for(), "goal")
    # Bounded at BOTH ends: "upper half" alone was read as "at the very top",
    # and the grid crop then ate the first line instead of the last.
    assert "one-fifth of the way down" in prompt
    assert "halfway down" in prompt
    assert "never in the bottom third" in prompt
    # The reason has to travel with the rule, or it reads as arbitrary taste.
    assert "caption" in prompt and "crops the tall image" in prompt
