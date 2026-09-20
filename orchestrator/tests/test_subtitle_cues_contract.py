from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.subtitle_cues import SubtitleCuesContract

ASSET_ID = "img_aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _isolated_cwd_with_audio_file(tmp_path, monkeypatch):
    """`validate_sources`'s AD-11 check looks at the real filesystem
    (`audio/narration.wav`) -- run every test in this file from an isolated
    tmp dir with that file present by default, so these tests never
    accidentally depend on (or get broken by) the repo's own real
    `audio/narration.wav` from a manual pipeline run.
    """
    monkeypatch.chdir(tmp_path)
    audio_path = tmp_path / "audio" / "narration.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-audio-bytes")


def make_story_plan(narration_script: str) -> FinalStoryPlanContract:
    """A minimal but fully valid `FinalStoryPlanContract` with the given
    narration_script -- word count 90-115, duration 40-50s (single scene).
    """
    data = {
        "produced_by": "story_agent",
        "story_title": "The Backwards Law",
        "core_thesis": "Acceptance beats striving.",
        "narrative_strategy": "Hook, human story, reveal, explanation, reflection.",
        "target_duration_seconds": 45.0,
        "target_word_count": 0,
        "hook": narration_script,
        "narration_script": narration_script,
        "voice_direction": {
            "persona": "Mature, thoughtful guide", "tone": ["calm"], "pace_wpm": 130,
            "delivery_notes": [], "emphasis_phrases": [], "pause_after_phrases": [],
        },
        "story_arc": {
            "opening_tension": "t", "human_or_concrete_story": "s",
            "reversal_or_reveal": "r", "principle_explanation": "p", "viewer_reflection": "v",
        },
        "scenes": [{
            "sequence": 1,
            "role": "SETUP",
            "estimated_duration_seconds": 45.0,
            "narration": narration_script,
            "source_asset_ids": [ASSET_ID],
            "source_support": "Directly supports this beat.",
            "visual_intent": "A calm visual.",
            "suggested_visual_treatment": "USE_EXISTING_ART",
            "impact_text": "",
            "emotional_goal": "calm",
        }],
        "unused_assets": [],
        "source_integrity_notes": [],
        "quality_review": {
            "verdict": "APPROVE", "ready_for_voice_generation": True, "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "hook_strength": 9, "spoken_naturalness": 9,
                "narrative_coherence": 9, "voice_alignment": 9, "pacing": 9,
                "scene_structure": 9, "visual_support": 9, "internal_consistency": 9,
            },
        },
    }
    return FinalStoryPlanContract.model_validate(data)


NARRATION_SCRIPT = " ".join(f"word{i}" for i in range(100))


def cue_for(cue_id: str, words: list[str], start: float) -> dict:
    word_entries = []
    cursor = start
    for word in words:
        word_entries.append({
            "index": 1, "word": word, "start_seconds": round(cursor, 3), "end_seconds": round(cursor + 0.3, 3),
        })
        cursor += 0.4
    return {
        "cue_id": cue_id,
        "start_seconds": round(start, 3),
        "end_seconds": round(cursor, 3),
        "word_count": len(words),
        "text": " ".join(words),
        "words": word_entries,
    }


def valid_contract_data(narration_script: str = NARRATION_SCRIPT) -> dict:
    return {
        "produced_by": "voice_agent",
        "source_narration_script": narration_script,
        "alignment_ratio": 0.99,
        "cues": [cue_for("cue_001", ["word0", "word1"], 0.0)],
    }


def test_valid_contract_round_trips():
    contract = SubtitleCuesContract.model_validate(valid_contract_data())
    contract.validate_sources(make_story_plan(NARRATION_SCRIPT))


def test_style_hint_defaults_to_normal_when_absent():
    # Story 2.2: `style_hint` is `SkipJsonSchema` -- a cue that predates this
    # story (no `style_hint` key at all) must still validate, defaulting to
    # "NORMAL" rather than failing as a missing required field.
    data = valid_contract_data()
    assert "style_hint" not in data["cues"][0]
    contract = SubtitleCuesContract.model_validate(data)
    assert contract.cues[0].style_hint == "NORMAL"


@pytest.mark.parametrize("style_hint", ["NORMAL", "IMPACT", "EMPHASIS", "REFLECTION"])
def test_style_hint_accepts_every_valid_literal(style_hint):
    data = valid_contract_data()
    data["cues"][0]["style_hint"] = style_hint
    contract = SubtitleCuesContract.model_validate(data)
    assert contract.cues[0].style_hint == style_hint


def test_style_hint_rejects_invalid_literal():
    data = valid_contract_data()
    data["cues"][0]["style_hint"] = "SHOUTY"
    with pytest.raises(ValidationError):
        SubtitleCuesContract.model_validate(data)


def test_missing_produced_by_rejected():
    data = valid_contract_data()
    del data["produced_by"]
    with pytest.raises(ValidationError):
        SubtitleCuesContract.model_validate(data)


def test_wrong_produced_by_rejected():
    data = valid_contract_data()
    data["produced_by"] = "build_subtitle_cues.py"
    with pytest.raises(ValidationError):
        SubtitleCuesContract.model_validate(data)


def test_empty_cues_rejected():
    data = valid_contract_data()
    data["cues"] = []
    with pytest.raises(ValidationError):
        SubtitleCuesContract.model_validate(data)


def test_cue_with_no_words_rejected():
    data = valid_contract_data()
    data["cues"][0]["words"] = []
    with pytest.raises(ValidationError):
        SubtitleCuesContract.model_validate(data)


@pytest.mark.parametrize("missing_field", ["cue_id", "start_seconds", "end_seconds", "word_count", "text"])
def test_cue_missing_required_field_rejected(missing_field):
    data = valid_contract_data()
    del data["cues"][0][missing_field]
    with pytest.raises(ValidationError):
        SubtitleCuesContract.model_validate(data)


def test_staleness_guard_rejects_mismatched_narration():
    contract = SubtitleCuesContract.model_validate(valid_contract_data())
    other_plan = make_story_plan(" ".join(f"other{i}" for i in range(100)))
    with pytest.raises(ValueError):
        contract.validate_sources(other_plan)


def test_staleness_guard_normalizes_whitespace_and_quotes():
    contract = SubtitleCuesContract.model_validate(
        valid_contract_data(narration_script=NARRATION_SCRIPT.replace(" ", "  "))
    )
    # Normalized (whitespace-collapsed) match against the plan's own
    # single-spaced narration_script must still pass.
    contract.validate_sources(make_story_plan(NARRATION_SCRIPT))


def test_staleness_guard_is_exact_after_normalization():
    contract = SubtitleCuesContract.model_validate(valid_contract_data(narration_script=NARRATION_SCRIPT + " extra"))
    with pytest.raises(ValueError):
        contract.validate_sources(make_story_plan(NARRATION_SCRIPT))


def test_alignment_ratio_below_minimum_rejected():
    """AD-10: defense in depth -- even a schema-valid, current-narration
    persisted file must be rejected if its own claimed alignment_ratio is
    below the tools' own 0.98 recheck threshold."""
    data = valid_contract_data()
    data["alignment_ratio"] = 0.90
    contract = SubtitleCuesContract.model_validate(data)
    with pytest.raises(ValueError, match="alignment_ratio"):
        contract.validate_sources(make_story_plan(NARRATION_SCRIPT))


def test_missing_audio_file_rejected():
    """AD-11: cues without their audio are not a valid skip target."""
    contract = SubtitleCuesContract.model_validate(valid_contract_data())
    Path("audio/narration.wav").unlink()
    with pytest.raises(ValueError, match="audio"):
        contract.validate_sources(make_story_plan(NARRATION_SCRIPT))


# -- atomic blocks ----------------------------------------------------------
#
# A block sits between two mandatory boundaries and can never be widened, so a
# block shorter than MIN_WORDS_PER_CUE has no legal split. Raising there halted
# a live run twice: once on "Just" (stranded between a full stop and the impact
# phrase "average at almost everything") and once on "Handwashing" (a one-word
# sentence).


def _spans_for(narration: str) -> list[dict]:
    from orchestrator.tools.deterministic_tools import WORD_RE

    return [
        {
            "index": i + 1,
            "word": m.group(),
            "char_start": m.start(),
            "char_end": m.end(),
            "start_seconds": i * 0.6,
            "end_seconds": i * 0.6 + 0.5,
        }
        for i, m in enumerate(WORD_RE.finditer(narration))
    ]


def test_word_stranded_before_an_impact_phrase_becomes_its_own_cue():
    from orchestrator.tools import deterministic_tools as dt

    narration = "The team wasn't terrible. Just average at almost everything."
    spans = _spans_for(narration)
    impact_ranges = dt.resolve_impact_ranges(spans, ["average at almost everything"])

    ranges = dt.build_ranges(narration, spans, impact_ranges, [])

    assert (5, 5) in ranges, f"orphan 'Just' should be its own cue, got {ranges}"
    # the impact phrase still starts and ends exactly on cue boundaries
    assert (6, 9) in ranges


def test_one_word_sentence_becomes_its_own_cue():
    from orchestrator.tools import deterministic_tools as dt

    narration = "Bike seats. Handwashing. The pillows his riders slept on."
    spans = _spans_for(narration)

    ranges = dt.build_ranges(narration, spans, [], [])

    assert (3, 3) in ranges, f"one-word sentence should be its own cue, got {ranges}"


def test_atomic_block_does_not_absorb_the_impact_phrase():
    """The orphan must not merge forward: validate_rendered_text requires an
    IMPACT cue to reproduce its declared phrase verbatim."""
    from orchestrator.tools import deterministic_tools as dt

    narration = "The team wasn't terrible. Just average at almost everything."
    spans = _spans_for(narration)
    impacts = ["average at almost everything"]
    impact_ranges = dt.resolve_impact_ranges(spans, impacts)

    ranges = dt.build_ranges(narration, spans, impact_ranges, [])
    cues = dt.build_cues(narration, spans, ranges, {}, impact_ranges, None)
    dt.validate_rendered_text(cues, impacts, impact_ranges)

    impact_cues = [c for c in cues if c["style_hint"] == "IMPACT"]
    rendered = " ".join(w["word"] for c in impact_cues for w in c["words"])
    assert rendered == "average at almost everything"


def test_blocks_longer_than_the_maximum_are_still_split():
    """The relaxation must not disable segmentation of divisible blocks."""
    from orchestrator.tools import deterministic_tools as dt

    narration = "Most people never discover what their habits could have become."
    spans = _spans_for(narration)

    ranges = dt.build_ranges(narration, spans, [], [])

    assert len(ranges) > 1
    assert all(end - start + 1 <= dt.MAX_WORDS_PER_CUE for start, end in ranges)
