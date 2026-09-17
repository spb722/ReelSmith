"""Visual/shot-mode planning contract. Shots map 1:1 onto
`FinalStoryPlanContract`'s scenes by sequence (AD-2 -- no independent timing
source exists pre-audio; subtitle cues are Story 1.5's concern).

Passing `model_validate()` *is* passing the quality bar (AD-3): the
mechanical checks (shot-sequence continuity, `generation_mode`/
`visual_treatment` consistency, non-empty `source_asset_ids`) and the
numeric score thresholds are both enforced as validators here, mirroring
`FinalStoryPlanContract`'s pattern. `visual_director.py` has no scored-
reviewer precedent to port -- these five dimensions are freshly designed
this story.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from orchestrator.contracts.final_story_plan import FinalStoryPlanContract, Scene


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


MIN_APPROVAL_SCORE = 8
MIN_SOURCE_FIDELITY_SCORE = 9
MIN_INTERNAL_CONSISTENCY_SCORE = 9

REQUIRED_SCORE_DIMENSIONS = (
    "source_fidelity", "generation_mode_appropriateness", "visual_coherence",
    "narrative_alignment", "internal_consistency",
)

# AD-4: generation_mode may be VEO only for these visual_treatment values;
# every other treatment must be STILL.
VEO_ELIGIBLE_TREATMENTS = frozenset({"AI_VIDEO_CANDIDATE", "MIXED"})


def _scene_content_fingerprint(scene: Scene) -> str:
    """AD-7: a deterministic snapshot of the scene content a shot is
    planned against -- narration/visual_intent/suggested_visual_treatment
    changing under an unchanged sequence/asset-id must not silently skip.
    """
    raw = "\x1f".join((scene.narration, scene.visual_intent, scene.suggested_visual_treatment))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Shot(ContractModel):
    sequence: int
    generation_mode: Literal["STILL", "VEO"]
    # Reused verbatim from visual_director.py's RESPONSE_SCHEMA -- matches
    # Scene.suggested_visual_treatment exactly (Code Map).
    visual_treatment: Literal[
        "USE_EXISTING_ART", "CROP_AND_RECOMPOSE", "SUBTLE_ANIMATION",
        "TEXT_LED", "AI_VIDEO_CANDIDATE", "MIXED",
    ]
    source_asset_ids: Annotated[list[str], Field(min_length=1)]
    shot_goal: Annotated[str, Field(min_length=1)]
    frame_composition: Annotated[str, Field(min_length=1)]
    motion_plan: Annotated[str, Field(min_length=1)]
    text_overlay: str
    source_support: Annotated[str, Field(min_length=1)]
    # AD-7 content-staleness snapshot. `SkipJsonSchema` keeps this out of the
    # schema shown to `visual_agent` entirely, so a freshly generated shot
    # always parses with the "" default (never a stray agent-supplied
    # value) -- `validate_sources` stamps the real fingerprint the first
    # time a fresh attempt validates, then compares against it on every
    # later resume check.
    scene_content_fingerprint: SkipJsonSchema[str] = ""
    # Story 2.1: additive timeline-conversion fields the `timeline.json`
    # converter needs (Design Notes). Like `scene_content_fingerprint`,
    # `SkipJsonSchema` keeps these out of visual_agent's schema entirely --
    # AD-2/visual_agent's own prompt is explicit that no independent shot
    # timing or subtitle-cue linkage exists yet at that stage of the
    # pipeline, so the agent must never see or invent them. Real values are
    # populated later (this story: a one-time backfill of the existing
    # reference reel's persisted plan from `remotion/src/timeline.ts`'s
    # hand-authored timing; future reels: a later stage once cues exist).
    start_seconds: SkipJsonSchema[float] = 0.0
    end_seconds: SkipJsonSchema[float] = 0.0
    primary_subtitle_cue_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)


class QualityReview(ContractModel):
    verdict: Literal["APPROVE"]
    ready_for_generation: Literal[True]
    confidence: Annotated[float, Field(ge=0, le=1)]
    scores: dict[str, Annotated[int, Field(ge=1, le=10)]]

    @model_validator(mode="after")
    def score_thresholds(self) -> "QualityReview":
        missing = [name for name in REQUIRED_SCORE_DIMENSIONS if name not in self.scores]
        if missing:
            raise ValueError(f"quality_review.scores is missing required dimensions: {missing}")
        below = {
            name: self.scores[name] for name in REQUIRED_SCORE_DIMENSIONS
            if self.scores[name] < MIN_APPROVAL_SCORE
        }
        if below:
            raise ValueError(
                f"quality_review requires every score >= {MIN_APPROVAL_SCORE}. Below threshold: {below}"
            )
        if self.scores["source_fidelity"] < MIN_SOURCE_FIDELITY_SCORE:
            raise ValueError(f"quality_review requires source_fidelity >= {MIN_SOURCE_FIDELITY_SCORE}.")
        if self.scores["internal_consistency"] < MIN_INTERNAL_CONSISTENCY_SCORE:
            raise ValueError(
                f"quality_review requires internal_consistency >= {MIN_INTERNAL_CONSISTENCY_SCORE}."
            )
        return self


class VisualPlanContract(ContractModel):
    # Required, no default: a legacy `visual_director.py` (Gemini) manifest
    # never has this field, so it fails validation here rather than silently
    # satisfying resume forever (AD-6, mirrors FinalStoryPlanContract).
    produced_by: Literal["visual_agent"]
    overall_visual_style: Annotated[str, Field(min_length=1)]
    shots: Annotated[list[Shot], Field(min_length=1)]
    quality_review: QualityReview

    @model_validator(mode="after")
    def shot_sequence_is_continuous(self) -> "VisualPlanContract":
        expected = list(range(1, len(self.shots) + 1))
        actual = [shot.sequence for shot in self.shots]
        if actual != expected:
            raise ValueError("Shot sequence numbers must start at 1 and increase continuously.")
        return self

    @model_validator(mode="after")
    def generation_mode_matches_visual_treatment(self) -> "VisualPlanContract":
        invalid = [
            shot.sequence for shot in self.shots
            if shot.generation_mode == "VEO" and shot.visual_treatment not in VEO_ELIGIBLE_TREATMENTS
        ]
        if invalid:
            raise ValueError(
                "generation_mode VEO requires visual_treatment AI_VIDEO_CANDIDATE or MIXED; "
                f"violated by shot sequence(s): {invalid}"
            )
        return self

    def validate_sources(self, story_plan: FinalStoryPlanContract) -> None:
        """Resume-staleness guard: shot sequences must exactly match the
        current `FinalStoryPlanContract`'s scene sequences (AD-2) -- one
        shot per scene, not merely a subset -- each shot's `source_asset_ids`
        must be a subset of that same scene's own `source_asset_ids`, and
        (AD-7) each shot's scene content must not have drifted since this
        plan was generated.

        A shot with no fingerprint yet (`scene_content_fingerprint == ""`)
        is a freshly generated attempt -- `visual_agent` never sees or sets
        this field (`SkipJsonSchema`) -- so it is stamped here from the
        current scene content rather than compared; a loaded, previously
        persisted shot always already carries a real fingerprint, which is
        compared, not overwritten.
        """
        expected_sequences = [scene.sequence for scene in story_plan.scenes]
        actual_sequences = [shot.sequence for shot in self.shots]
        if actual_sequences != expected_sequences:
            raise ValueError(
                "Shot sequences must exactly match the current FinalStoryPlanContract's "
                f"scene sequences. Expected {expected_sequences}, got {actual_sequences}."
            )
        scenes_by_sequence = {scene.sequence: scene for scene in story_plan.scenes}
        for shot in self.shots:
            scene = scenes_by_sequence[shot.sequence]
            unknown = set(shot.source_asset_ids) - set(scene.source_asset_ids)
            if unknown:
                raise ValueError(
                    f"Shot {shot.sequence} references asset ids not in its scene's own "
                    f"source_asset_ids: {sorted(unknown)}"
                )
            expected_fingerprint = _scene_content_fingerprint(scene)
            if not shot.scene_content_fingerprint:
                shot.scene_content_fingerprint = expected_fingerprint
            elif shot.scene_content_fingerprint != expected_fingerprint:
                raise ValueError(
                    f"Shot {shot.sequence}'s scene narration/visual_intent/"
                    "suggested_visual_treatment has changed since this visual plan was "
                    "generated (AD-7); it must be regenerated."
                )
