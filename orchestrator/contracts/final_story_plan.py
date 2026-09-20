"""Narration contract, folding `story_director.py`'s draft shape and
`story_quality_loop.py`'s proven quality bar into one pydantic model.

Passing `model_validate()` *is* passing the quality bar (AD-4): the
mechanical checks (word count, duration, scene sequence, narration
reconstruction, known source asset ids) and the numeric score thresholds
are both enforced as validators here, not in a separate reviewer artifact.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.contracts.text_normalization import normalize_text


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


# Ported verbatim from story_director.py / story_quality_loop.py -- the
# proven bar, not re-tuned.
TARGET_MIN_SECONDS = 40
TARGET_MAX_SECONDS = 50
# story_quality_loop.py's `deterministic_checks` gates `duration_range` with
# no tolerance (strict 40<=total<=50) -- matched exactly here.
DURATION_TOLERANCE_SECONDS = 0
TARGET_MIN_WORDS = 90
TARGET_MAX_WORDS = 115

MIN_APPROVAL_SCORE = 8
MIN_SOURCE_FIDELITY_SCORE = 9
MIN_INTERNAL_CONSISTENCY_SCORE = 9

# story_quality_loop.py's REVIEW_SCHEMA `scores` object -- all nine are
# `required`, not merely present-if-supplied.
REQUIRED_SCORE_DIMENSIONS = (
    "source_fidelity", "hook_strength", "spoken_naturalness", "narrative_coherence",
    "voice_alignment", "pacing", "scene_structure", "visual_support", "internal_consistency",
)


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE))


def _canonical_words(text: str) -> list[str]:
    """Word tokens for impact↔narration matching -- mirrors subtitle
    `normalize_cue_word` / `find_phrase_range` (casefold, strip punctuation).
    """
    return [
        re.sub(r"[^\w]+", "", token.replace("’", "'").replace("‘", "'").lower(), flags=re.UNICODE)
        for token in re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE)
        if re.sub(r"[^\w]+", "", token.replace("’", "'").replace("‘", "'"), flags=re.UNICODE)
    ]


def _contiguous_phrase_in(haystack: list[str], needle: list[str]) -> bool:
    if not needle:
        return False
    size = len(needle)
    for start in range(0, len(haystack) - size + 1):
        if haystack[start:start + size] == needle:
            return True
    return False


class VoiceDirection(ContractModel):
    persona: str
    tone: list[str]
    pace_wpm: int
    delivery_notes: list[str]
    emphasis_phrases: list[str]
    pause_after_phrases: list[str]


class StoryArc(ContractModel):
    opening_tension: str
    human_or_concrete_story: str
    reversal_or_reveal: str
    principle_explanation: str
    viewer_reflection: str


class Scene(ContractModel):
    sequence: int
    role: Literal[
        "HOOK", "SETUP", "CONTEXT", "HUMAN_STORY", "BUILDUP",
        "REVERSAL", "REVEAL", "EXPLANATION", "REFLECTION", "ENDING",
    ]
    estimated_duration_seconds: Annotated[float, Field(gt=0)]
    narration: Annotated[str, Field(min_length=1)]
    source_asset_ids: Annotated[list[str], Field(min_length=1)]
    source_support: str
    visual_intent: str
    suggested_visual_treatment: Literal[
        "USE_EXISTING_ART", "CROP_AND_RECOMPOSE", "SUBTLE_ANIMATION",
        "TEXT_LED", "AI_VIDEO_CANDIDATE", "MIXED",
    ]
    impact_text: str
    emotional_goal: str


class UnusedAsset(ContractModel):
    asset_id: str
    reason: str


class QualityReview(ContractModel):
    verdict: Literal["APPROVE"]
    ready_for_voice_generation: Literal[True]
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


class FinalStoryPlanContract(ContractModel):
    # Required, no default: a legacy `story_quality_loop.py` (Gemini)
    # manifest never has this field, so it fails validation here rather
    # than silently satisfying resume (AD-1, mirrors AnalyzedAssetsContract).
    produced_by: Literal["story_agent"]
    story_title: str
    core_thesis: str
    narrative_strategy: str
    target_duration_seconds: float
    target_word_count: int
    hook: str
    narration_script: str
    voice_direction: VoiceDirection
    story_arc: StoryArc
    scenes: Annotated[list[Scene], Field(min_length=1)]
    unused_assets: list[UnusedAsset]
    source_integrity_notes: list[str]
    quality_review: QualityReview

    @model_validator(mode="after")
    def word_count_in_range(self) -> "FinalStoryPlanContract":
        word_count = _count_words(self.narration_script)
        if not (TARGET_MIN_WORDS <= word_count <= TARGET_MAX_WORDS):
            raise ValueError(
                f"narration_script has {word_count} words; target is {TARGET_MIN_WORDS}-{TARGET_MAX_WORDS}."
            )
        return self

    @model_validator(mode="after")
    def duration_in_range(self) -> "FinalStoryPlanContract":
        total = sum(scene.estimated_duration_seconds for scene in self.scenes)
        low = TARGET_MIN_SECONDS - DURATION_TOLERANCE_SECONDS
        high = TARGET_MAX_SECONDS + DURATION_TOLERANCE_SECONDS
        if not (low <= total <= high):
            raise ValueError(
                f"Scene durations total {total:.1f}s; target is approximately "
                f"{TARGET_MIN_SECONDS}-{TARGET_MAX_SECONDS}s."
            )
        return self

    @model_validator(mode="after")
    def scene_sequence_is_continuous(self) -> "FinalStoryPlanContract":
        expected = list(range(1, len(self.scenes) + 1))
        actual = [scene.sequence for scene in self.scenes]
        if actual != expected:
            raise ValueError("Scene sequence numbers must start at 1 and increase continuously.")
        return self

    @model_validator(mode="after")
    def scene_narration_reconstructs_script(self) -> "FinalStoryPlanContract":
        joined = " ".join(scene.narration.strip() for scene in self.scenes)
        if normalize_text(joined) != normalize_text(self.narration_script):
            raise ValueError("Concatenated scene narration must reconstruct narration_script exactly.")
        return self

    @model_validator(mode="after")
    def impact_text_must_appear_in_narration(self) -> "FinalStoryPlanContract":
        """AD-12 early gate: every non-empty impact_text must be an exact
        contiguous word sequence inside narration_script (same rule the
        subtitle builder later enforces). Empty impact_text is allowed.
        Failures become story_agent validation feedback and retry.
        """
        narration_words = _canonical_words(self.narration_script)
        bad: list[str] = []
        for scene in self.scenes:
            phrase = (scene.impact_text or "").strip()
            if not phrase:
                continue
            phrase_words = _canonical_words(phrase)
            if not phrase_words or not _contiguous_phrase_in(narration_words, phrase_words):
                bad.append(f"scene {scene.sequence} impact_text {phrase!r}")
        if bad:
            raise ValueError(
                "Each non-empty impact_text must appear verbatim (same words, "
                "same order) inside narration_script. Copy a short phrase from "
                "the narration; do not paraphrase. Violations: " + "; ".join(bad)
            )
        return self

    @model_validator(mode="after")
    def compute_target_metrics(self) -> "FinalStoryPlanContract":
        # story_quality_loop.py:709-711 always overwrites target_word_count/
        # target_duration_seconds with the actually-computed values rather
        # than trusting the model's own estimate.
        self.target_word_count = _count_words(self.narration_script)
        self.target_duration_seconds = round(
            sum(scene.estimated_duration_seconds for scene in self.scenes), 2
        )
        return self

    def validate_sources(self, analyzed_asset_ids: set[str]) -> None:
        """Every `source_asset_ids`/`unused_assets[].asset_id` reference must
        be a known current asset id -- a subset, not full coverage, since
        (unlike Story 1.2's `AnalyzedAssetsContract`) not every analyzed
        asset need be used in the final narration. Doubles as the
        resume-staleness guard and the mechanical known-id check ported
        from story_director.py/story_quality_loop.py.
        """
        used = {asset_id for scene in self.scenes for asset_id in scene.source_asset_ids}
        unused = {unused.asset_id for unused in self.unused_assets}
        overlap = used & unused
        if overlap:
            raise ValueError(
                f"source_asset_ids and unused_assets both claim these asset ids: {sorted(overlap)}"
            )
        unknown = (used | unused) - analyzed_asset_ids
        if unknown:
            raise ValueError(
                f"source_asset_ids/unused_assets reference unknown or stale asset ids: {sorted(unknown)}"
            )
