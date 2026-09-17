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
