from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.visual_plan import VisualPlanContract

ASSET_IDS = ["img_aaaaaaaaaaaa", "img_bbbbbbbbbbbb", "img_cccccccccccc"]


def _scene(sequence: int, words: list[str], asset_id: str, duration: float = 15.0) -> dict:
    return {
        "sequence": sequence,
        "role": "SETUP",
        "estimated_duration_seconds": duration,
        "narration": " ".join(words),
        "source_asset_ids": [asset_id],
        "source_support": "Directly supports this beat.",
        "visual_intent": "A calm visual.",
        "suggested_visual_treatment": "USE_EXISTING_ART",
        "impact_text": "",
        "emotional_goal": "calm",
    }


def make_story_plan(asset_ids: list[str]) -> FinalStoryPlanContract:
    """A minimal but fully valid `FinalStoryPlanContract`, one scene per
    asset id, each scene citing only its own asset -- so validate_sources
    tests can tell "known somewhere in the plan" apart from "known to this
    shot's own scene".
    """
    words_per_scene = 33
    total_words = words_per_scene * len(asset_ids)
    all_words = [f"word{i}" for i in range(total_words)]
    scenes = []
    for index, asset_id in enumerate(asset_ids):
        chunk = all_words[index * words_per_scene:(index + 1) * words_per_scene]
        scenes.append(_scene(index + 1, chunk, asset_id))
    narration_script = " ".join(all_words)
    data = {
        "produced_by": "story_agent",
        "story_title": "The Backwards Law",
        "core_thesis": "Acceptance beats striving.",
        "narrative_strategy": "Hook, human story, reveal, explanation, reflection.",
        "target_duration_seconds": 45.0,
        "target_word_count": total_words,
        "hook": scenes[0]["narration"],
        "narration_script": narration_script,
        "voice_direction": {
            "persona": "Mature, thoughtful guide", "tone": ["calm"], "pace_wpm": 130,
            "delivery_notes": [], "emphasis_phrases": [], "pause_after_phrases": [],
        },
        "story_arc": {
            "opening_tension": "t", "human_or_concrete_story": "s",
            "reversal_or_reveal": "r", "principle_explanation": "p", "viewer_reflection": "v",
        },
        "scenes": scenes,
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


def shot_for(
    sequence: int, asset_id: str, generation_mode: str = "STILL",
    visual_treatment: str = "USE_EXISTING_ART",
) -> dict:
    return {
        "sequence": sequence,
        "generation_mode": generation_mode,
        "visual_treatment": visual_treatment,
        "source_asset_ids": [asset_id],
        "shot_goal": "Establish the beat.",
        "frame_composition": "Center the subject.",
        "motion_plan": "Slow push-in.",
        "text_overlay": "",
        "source_support": "Directly grounded in the cited asset.",
    }


def base_contract(asset_ids: list[str] = ASSET_IDS) -> dict:
    shots = [shot_for(index + 1, asset_id) for index, asset_id in enumerate(asset_ids)]
    return {
        "produced_by": "visual_agent",
        "overall_visual_style": "Reflective, calm, textured halftone illustrations.",
        "shots": shots,
        "quality_review": {
            "verdict": "APPROVE", "ready_for_generation": True, "confidence": 0.9,
            "scores": {
                "source_fidelity": 9, "generation_mode_appropriateness": 9,
                "visual_coherence": 9, "narrative_alignment": 9, "internal_consistency": 9,
            },
        },
    }


def test_valid_contract_passes():
    VisualPlanContract.model_validate(base_contract())


def test_shot_sequence_gap_rejected():
    data = base_contract()
    data["shots"][2]["sequence"] = 10
    with pytest.raises(ValidationError, match="sequence"):
        VisualPlanContract.model_validate(data)


def test_veo_requires_eligible_treatment_rejected():
    data = base_contract()
    data["shots"][0]["generation_mode"] = "VEO"  # visual_treatment stays USE_EXISTING_ART
    with pytest.raises(ValidationError, match="VEO"):
        VisualPlanContract.model_validate(data)


@pytest.mark.parametrize("treatment", ["AI_VIDEO_CANDIDATE", "MIXED"])
def test_veo_allowed_with_eligible_treatment(treatment):
    data = base_contract()
    data["shots"][0]["generation_mode"] = "VEO"
    data["shots"][0]["visual_treatment"] = treatment
    VisualPlanContract.model_validate(data)


@pytest.mark.parametrize("treatment", [
    "USE_EXISTING_ART", "CROP_AND_RECOMPOSE", "SUBTLE_ANIMATION",
    "TEXT_LED", "AI_VIDEO_CANDIDATE", "MIXED",
])
def test_still_allowed_with_any_treatment(treatment):
    # AD-4 is asymmetric: VEO is restricted to two treatments, but STILL is
    # never restricted -- every treatment (including the two VEO-eligible
    # ones) must remain valid when paired with STILL.
    data = base_contract()
    data["shots"][0]["generation_mode"] = "STILL"
    data["shots"][0]["visual_treatment"] = treatment
    VisualPlanContract.model_validate(data)


def test_empty_source_asset_ids_rejected():
    data = base_contract()
    data["shots"][0]["source_asset_ids"] = []
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)


def test_missing_produced_by_rejected():
    data = base_contract()
    del data["produced_by"]
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)


def test_score_below_approval_threshold_rejected():
    data = base_contract()
    data["quality_review"]["scores"]["visual_coherence"] = 7
    with pytest.raises(ValidationError, match="every score"):
        VisualPlanContract.model_validate(data)


def test_source_fidelity_below_its_own_threshold_rejected():
    data = base_contract()
    data["quality_review"]["scores"]["source_fidelity"] = 8
    with pytest.raises(ValidationError, match="source_fidelity"):
        VisualPlanContract.model_validate(data)


def test_internal_consistency_below_its_own_threshold_rejected():
    data = base_contract()
    data["quality_review"]["scores"]["internal_consistency"] = 8
    with pytest.raises(ValidationError, match="internal_consistency"):
        VisualPlanContract.model_validate(data)


def test_missing_score_dimension_rejected():
    data = base_contract()
    del data["quality_review"]["scores"]["narrative_alignment"]
    with pytest.raises(ValidationError, match="missing required dimensions"):
        VisualPlanContract.model_validate(data)


def test_verdict_must_be_approve():
    data = base_contract()
    data["quality_review"]["verdict"] = "REVISE"
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)


def test_ready_for_generation_must_be_true():
    data = base_contract()
    data["quality_review"]["ready_for_generation"] = False
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)


def test_validate_sources_accepts_matching_sequences_and_own_scene_assets():
    story_plan = make_story_plan(ASSET_IDS)
    contract = VisualPlanContract.model_validate(base_contract())
    contract.validate_sources(story_plan)


def test_validate_sources_rejects_sequence_mismatch():
    story_plan = make_story_plan(ASSET_IDS)
    data = base_contract()
    data["shots"] = data["shots"][:-1]  # 2 shots for 3 scenes
    contract = VisualPlanContract.model_validate(data)
    with pytest.raises(ValueError, match="scene sequences"):
        contract.validate_sources(story_plan)


def test_validate_sources_rejects_asset_id_not_owned_by_that_shots_own_scene():
    story_plan = make_story_plan(ASSET_IDS)
    data = base_contract()
    # img_bbbbbbbbbbbb is a real asset id in the plan, but belongs to scene
    # 2, not scene 1 -- must still be rejected for shot 1.
    data["shots"][0]["source_asset_ids"] = [ASSET_IDS[1]]
    contract = VisualPlanContract.model_validate(data)
    with pytest.raises(ValueError, match="not in its scene's own"):
        contract.validate_sources(story_plan)


@pytest.mark.parametrize("mutation", ["missing_field", "bad_verdict", "score_too_low"])
def test_contract_rejects_invalid_quality_plan(mutation):
    data = copy.deepcopy(base_contract())
    if mutation == "missing_field":
        del data["overall_visual_style"]
    elif mutation == "bad_verdict":
        data["quality_review"]["verdict"] = "REVISE"
    else:
        data["quality_review"]["scores"]["internal_consistency"] = 5
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)
