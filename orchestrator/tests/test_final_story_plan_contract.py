from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from orchestrator.contracts.final_story_plan import FinalStoryPlanContract


def _scenes(word_count: int, per_scene: int = 20, duration: float = 9.0, asset_id: str = "img_aaaaaaaaaaaa"):
    words = [f"word{i}" for i in range(word_count)]
    scenes = []
    for i in range(0, word_count, per_scene):
        chunk = words[i:i + per_scene]
        scenes.append({
            "sequence": i // per_scene + 1,
            "role": "SETUP",
            "estimated_duration_seconds": duration,
            "narration": " ".join(chunk),
            "source_asset_ids": [asset_id],
            "source_support": "Directly supports this beat.",
            "visual_intent": "A calm visual.",
            "suggested_visual_treatment": "USE_EXISTING_ART",
            "impact_text": "",
            "emotional_goal": "calm",
        })
    narration_script = " ".join(words)
    return narration_script, scenes


def base_contract() -> dict:
    narration_script, scenes = _scenes(100)
    return {
        "produced_by": "story_agent",
        "story_title": "The Backwards Law",
        "core_thesis": "Acceptance beats striving.",
        "narrative_strategy": "Hook, human story, reveal, explanation, reflection.",
        "target_duration_seconds": 45.0,
        "target_word_count": 100,
        "hook": scenes[0]["narration"],
        "narration_script": narration_script,
        "voice_direction": {
            "persona": "Mature, thoughtful guide",
            "tone": ["calm", "confident"],
            "pace_wpm": 130,
            "delivery_notes": ["Steady pace."],
            "emphasis_phrases": [],
            "pause_after_phrases": [],
        },
        "story_arc": {
            "opening_tension": "A paradox.",
            "human_or_concrete_story": "A concrete example.",
            "reversal_or_reveal": "The reveal.",
            "principle_explanation": "The principle.",
            "viewer_reflection": "A closing question.",
        },
        "scenes": scenes,
        "unused_assets": [],
        "source_integrity_notes": [],
        "quality_review": {
            "verdict": "APPROVE",
            "ready_for_voice_generation": True,
            "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "hook_strength": 9, "spoken_naturalness": 9,
                "narrative_coherence": 9, "voice_alignment": 9, "pacing": 9,
                "scene_structure": 9, "visual_support": 9, "internal_consistency": 9,
            },
        },
    }


def test_valid_contract_passes():
    FinalStoryPlanContract.model_validate(base_contract())


@pytest.mark.parametrize("word_count", [70, 130])
def test_word_count_out_of_range_rejected(word_count):
    data = base_contract()
    narration_script, scenes = _scenes(word_count)
    data["narration_script"] = narration_script
    data["scenes"] = scenes
    with pytest.raises(ValidationError, match="words"):
        FinalStoryPlanContract.model_validate(data)


def test_duration_out_of_range_rejected():
    data = base_contract()
    for scene in data["scenes"]:
        scene["estimated_duration_seconds"] = 1.0
    with pytest.raises(ValidationError, match="duration|Scene durations"):
        FinalStoryPlanContract.model_validate(data)


def test_scene_sequence_gap_rejected():
    data = base_contract()
    data["scenes"][2]["sequence"] = 10
    with pytest.raises(ValidationError, match="sequence"):
        FinalStoryPlanContract.model_validate(data)


def test_scene_narration_reconstruction_mismatch_rejected():
    data = base_contract()
    data["scenes"][0]["narration"] += " extraword"
    with pytest.raises(ValidationError, match="reconstruct"):
        FinalStoryPlanContract.model_validate(data)


def test_impact_text_verbatim_in_narration_accepted():
    data = base_contract()
    # First scene narration is "word0 word1 ... word19"
    data["scenes"][0]["impact_text"] = "word0 word1 word2"
    FinalStoryPlanContract.model_validate(data)


def test_impact_text_paraphrase_rejected():
    data = base_contract()
    data["scenes"][0]["impact_text"] = "a slogan not spoken anywhere"
    with pytest.raises(ValidationError, match="impact_text must appear verbatim"):
        FinalStoryPlanContract.model_validate(data)


def test_impact_text_reordered_words_rejected():
    data = base_contract()
    data["scenes"][0]["impact_text"] = "word2 word1 word0"
    with pytest.raises(ValidationError, match="impact_text must appear verbatim"):
        FinalStoryPlanContract.model_validate(data)


def test_empty_impact_text_still_allowed():
    data = base_contract()
    assert data["scenes"][0]["impact_text"] == ""
    FinalStoryPlanContract.model_validate(data)


def test_validate_sources_accepts_known_ids():
    contract = FinalStoryPlanContract.model_validate(base_contract())
    contract.validate_sources({"img_aaaaaaaaaaaa", "img_bbbbbbbbbbbb"})


def test_validate_sources_rejects_unknown_or_stale_ids():
    contract = FinalStoryPlanContract.model_validate(base_contract())
    with pytest.raises(ValueError, match="unknown or stale"):
        contract.validate_sources({"img_bbbbbbbbbbbb"})


def test_unused_asset_id_also_checked_by_validate_sources():
    data = base_contract()
    data["unused_assets"] = [{"asset_id": "img_cccccccccccc", "reason": "Not needed."}]
    contract = FinalStoryPlanContract.model_validate(data)
    with pytest.raises(ValueError, match="unknown or stale"):
        contract.validate_sources({"img_aaaaaaaaaaaa"})


def test_score_below_approval_threshold_rejected():
    data = base_contract()
    data["quality_review"]["scores"]["pacing"] = 7
    with pytest.raises(ValidationError, match="every score"):
        FinalStoryPlanContract.model_validate(data)


def test_source_fidelity_below_its_own_threshold_rejected():
    data = base_contract()
    data["quality_review"]["scores"]["source_fidelity"] = 8
    with pytest.raises(ValidationError, match="source_fidelity"):
        FinalStoryPlanContract.model_validate(data)


def test_internal_consistency_below_its_own_threshold_rejected():
    data = base_contract()
    data["quality_review"]["scores"]["internal_consistency"] = 8
    with pytest.raises(ValidationError, match="internal_consistency"):
        FinalStoryPlanContract.model_validate(data)


def test_verdict_must_be_approve():
    data = base_contract()
    data["quality_review"]["verdict"] = "REVISE"
    with pytest.raises(ValidationError):
        FinalStoryPlanContract.model_validate(data)


def test_ready_for_voice_generation_must_be_true():
    data = base_contract()
    data["quality_review"]["ready_for_voice_generation"] = False
    with pytest.raises(ValidationError):
        FinalStoryPlanContract.model_validate(data)


def test_missing_produced_by_rejected():
    data = base_contract()
    del data["produced_by"]
    with pytest.raises(ValidationError):
        FinalStoryPlanContract.model_validate(data)


def test_deep_copy_of_valid_contract_still_passes():
    # Sanity check that base_contract() itself is a stable, reusable fixture.
    FinalStoryPlanContract.model_validate(copy.deepcopy(base_contract()))
