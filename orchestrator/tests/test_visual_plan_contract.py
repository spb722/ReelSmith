from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.visual_plan import (
    MIN_VIDEO_NOMINATIONS,
    StillMotion,
    VisualPlanContract,
    _scene_content_fingerprint,
)

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


def _story_plan_dict(asset_ids: list[str]) -> dict:
    """A minimal but fully valid `FinalStoryPlanContract` payload, one scene
    per asset id, each scene citing only its own asset -- so validate_sources
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
    return {
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


def make_story_plan(asset_ids: list[str]) -> FinalStoryPlanContract:
    return FinalStoryPlanContract.model_validate(_story_plan_dict(asset_ids))


def shot_for(
    sequence: int, asset_id: str, generation_mode: str = "STILL",
    visual_treatment: str = "USE_EXISTING_ART",
) -> dict:
    shot = {
        "sequence": sequence,
        "generation_mode": generation_mode,
        "visual_treatment": visual_treatment,
        "source_asset_ids": [asset_id],
        "shot_goal": "Establish the beat.",
        "frame_composition": "Center the subject.",
        "motion_plan": "Slow push-in.",
        "text_overlay": "",
        "source_support": "Directly grounded in the cited asset.",
        "fade_in_frames": 6,
        "fade_out_frames": 4,
    }
    # Every shot carries still_motion now, whatever its mode, so a video shot
    # can be demoted back to a still without losing its fallback.
    shot["still_motion"] = {"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"}
    if generation_mode == "VEO":
        # A VEO shot must trace back to a visual_agent nomination.
        shot["video_candidate_rank"] = 1
        shot["video_motion_intent"] = "Slow drift across the frame."
    return shot


def base_contract(asset_ids: list[str] = ASSET_IDS) -> dict:
    shots = [shot_for(index + 1, asset_id) for index, asset_id in enumerate(asset_ids)]
    # visual_agent must nominate min(MIN_VIDEO_NOMINATIONS, len(shots)) shots,
    # ranked 1..k with no gaps.
    for rank, shot in enumerate(shots[:MIN_VIDEO_NOMINATIONS], start=1):
        shot["video_candidate_rank"] = rank
        shot["video_motion_intent"] = "Slow drift across the frame."
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


def test_veo_without_a_video_nomination_rejected():
    """A shot's mode must trace back to visual_agent's own judgement: only a
    nominated shot may be promoted to VEO (AD-15)."""
    data = base_contract()
    data["shots"][0]["generation_mode"] = "VEO"
    data["shots"][0]["video_candidate_rank"] = None
    data["shots"][0]["video_motion_intent"] = ""
    with pytest.raises(ValidationError, match="video_candidate_rank"):
        VisualPlanContract.model_validate(data)


@pytest.mark.parametrize("treatment", [
    "USE_EXISTING_ART", "CROP_AND_RECOMPOSE", "SUBTLE_ANIMATION",
    "TEXT_LED", "AI_VIDEO_CANDIDATE", "MIXED",
])
def test_veo_allowed_with_any_treatment_when_nominated(treatment):
    """`visual_treatment` no longer gates video. It describes how the source
    art is handled, not whether the beat wants motion -- and the old gate
    excluded USE_EXISTING_ART shots, which are often the best candidates."""
    data = base_contract()
    data["shots"][0]["generation_mode"] = "VEO"
    data["shots"][0]["visual_treatment"] = treatment
    contract = VisualPlanContract.model_validate(data)
    assert contract.shots[0].generation_mode == "VEO"


def test_too_few_video_nominations_rejected():
    """Prompt wording alone previously produced zero video shots; the floor is
    enforced mechanically so the retry loop gets real feedback."""
    data = base_contract()
    for shot in data["shots"]:
        shot["video_candidate_rank"] = None
    with pytest.raises(ValidationError, match="video_candidate_rank"):
        VisualPlanContract.model_validate(data)


def test_duplicate_video_nomination_rank_rejected():
    data = base_contract()
    data["shots"][1]["video_candidate_rank"] = data["shots"][0]["video_candidate_rank"]
    with pytest.raises(ValidationError, match="contiguous"):
        VisualPlanContract.model_validate(data)


def test_non_contiguous_video_nomination_ranks_rejected():
    data = base_contract()
    data["shots"][-1]["video_candidate_rank"] = 9
    with pytest.raises(ValidationError, match="contiguous"):
        VisualPlanContract.model_validate(data)


def test_shot_renderer_fields_default_when_absent():
    # Legacy persisted plans may omit fade keys (default 0) but STILL shots
    # still require `still_motion` once validated as a full contract.
    data = base_contract()
    for shot in data["shots"]:
        del shot["fade_in_frames"]
        del shot["fade_out_frames"]
    contract = VisualPlanContract.model_validate(data)
    assert all(shot.fade_in_frames == 0 for shot in contract.shots)
    assert all(shot.fade_out_frames == 0 for shot in contract.shots)
    assert all(shot.still_motion is not None for shot in contract.shots)


def test_still_shot_without_still_motion_rejected():
    data = base_contract()
    data["shots"][0]["still_motion"] = None
    with pytest.raises(ValidationError, match="still_motion"):
        VisualPlanContract.model_validate(data)


def test_agent_json_schema_includes_renderer_motion_fields():
    schema = VisualPlanContract.model_json_schema()
    shot_props = schema["$defs"]["Shot"]["properties"]
    assert "fade_in_frames" in shot_props
    assert "fade_out_frames" in shot_props
    assert "still_motion" in shot_props


def test_still_motion_accepts_a_well_formed_shot():
    data = base_contract()
    data["shots"][0]["still_motion"] = {"scale_from": 1.0, "scale_to": 1.07, "easing": "linear"}
    contract = VisualPlanContract.model_validate(data)
    assert contract.shots[0].still_motion == StillMotion(scale_from=1.0, scale_to=1.07, easing="linear")


@pytest.mark.parametrize("missing_field", ["scale_from", "scale_to"])
def test_still_motion_rejects_missing_required_field(missing_field):
    data = base_contract()
    still_motion = {"scale_from": 1.0, "scale_to": 1.07, "easing": "linear"}
    del still_motion[missing_field]
    data["shots"][0]["still_motion"] = still_motion
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)


def test_still_motion_rejects_invalid_easing_literal():
    data = base_contract()
    data["shots"][0]["still_motion"] = {"scale_from": 1.0, "scale_to": 1.07, "easing": "bounce"}
    with pytest.raises(ValidationError):
        VisualPlanContract.model_validate(data)


def test_veo_shot_keeps_still_motion():
    """A video shot keeps its Ken-Burns fallback: it may still be demoted to a
    still if the budget runs out, and the renderer ignores the field for a
    video asset anyway."""
    data = base_contract()
    data["shots"][0]["generation_mode"] = "VEO"
    contract = VisualPlanContract.model_validate(data)
    assert contract.shots[0].still_motion is not None


def test_veo_shot_without_still_motion_rejected():
    data = base_contract()
    data["shots"][0]["generation_mode"] = "VEO"
    data["shots"][0]["still_motion"] = None
    with pytest.raises(ValidationError, match="still_motion"):
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


def test_validate_sources_stamps_content_fingerprint_on_a_fresh_contract():
    # visual_agent never sees or sets this field (SkipJsonSchema); a freshly
    # parsed contract always starts with the "" default.
    story_plan = make_story_plan(ASSET_IDS)
    contract = VisualPlanContract.model_validate(base_contract())
    assert all(shot.scene_content_fingerprint == "" for shot in contract.shots)
    contract.validate_sources(story_plan)
    scenes_by_sequence = {scene.sequence: scene for scene in story_plan.scenes}
    for shot in contract.shots:
        assert shot.scene_content_fingerprint == _scene_content_fingerprint(scenes_by_sequence[shot.sequence])


@pytest.mark.parametrize("field", ["narration", "visual_intent", "suggested_visual_treatment"])
def test_validate_sources_rejects_scene_content_drift_under_unchanged_sequence_and_assets(field):
    # AD-7: a regenerated FinalStoryPlanContract with the same scene count/
    # asset ids but changed narration/visual_intent/suggested_visual_treatment
    # must not silently satisfy resume.
    story_plan = make_story_plan(ASSET_IDS)
    contract = VisualPlanContract.model_validate(base_contract())
    contract.validate_sources(story_plan)  # stamps fingerprints against the original content

    changed = _story_plan_dict(ASSET_IDS)
    if field == "narration":
        # Prepend rather than replace, so total word count stays in the
        # 90-115 range FinalStoryPlanContract itself still enforces.
        changed["scenes"][0]["narration"] = "changed " + changed["scenes"][0]["narration"]
        changed["narration_script"] = " ".join(scene["narration"] for scene in changed["scenes"])
        changed["hook"] = changed["scenes"][0]["narration"]
    elif field == "visual_intent":
        changed["scenes"][0]["visual_intent"] = "An entirely different visual mood."
    else:
        changed["scenes"][0]["suggested_visual_treatment"] = "TEXT_LED"
    changed_story_plan = FinalStoryPlanContract.model_validate(changed)

    with pytest.raises(ValueError, match="changed since this visual plan"):
        contract.validate_sources(changed_story_plan)


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
