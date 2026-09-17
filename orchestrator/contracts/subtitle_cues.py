"""Subtitle-cues contract. Unlike `FinalStoryPlanContract`/`VisualPlanContract`
(which invent a fresh scored quality bar because their producing agent's own
creative judgment is the quality gate), `extract_word_timing.py`'s 0.85
exact-match-ratio gate and `build_subtitle_cues.py`'s 0.98 alignment recheck
already raise on their own mechanical thresholds before this contract is ever
constructed -- `SubtitleCuesContract`'s job is provenance and staleness, not
re-deriving a quality gate (Story 1.5 Design Notes).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.text_normalization import normalize_text


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class Word(ContractModel):
    index: int
    word: str
    start_seconds: float
    end_seconds: float


class Cue(ContractModel):
    cue_id: str
    start_seconds: float
    end_seconds: float
    word_count: int
    text: str
    words: Annotated[list[Word], Field(min_length=1)]


class SubtitleCuesContract(ContractModel):
    # Required, no default: a legacy `build_subtitle_cues.py` (manually-run)
    # manifest never has this field, so it fails validation here rather
    # than silently satisfying resume forever (mirrors FinalStoryPlanContract/
    # VisualPlanContract's AD-1/AD-6 pattern).
    produced_by: Literal["voice_agent"]
    source_narration_script: str
    alignment_ratio: float
    cues: Annotated[list[Cue], Field(min_length=1)]

    @model_validator(mode="after")
    def cues_are_ordered_and_non_overlapping(self) -> "SubtitleCuesContract":
        previous_end = None
        for cue in self.cues:
            if cue.end_seconds <= cue.start_seconds:
                raise ValueError(f"Cue {cue.cue_id} has end_seconds <= start_seconds.")
            if previous_end is not None and cue.start_seconds < previous_end:
                raise ValueError(
                    f"Cue {cue.cue_id} overlaps or is out of order relative to the "
                    "previous cue -- cues must be sorted by start_seconds and non-overlapping."
                )
            previous_end = cue.end_seconds
        return self

    def validate_sources(self, story_plan: FinalStoryPlanContract) -> None:
        """Resume-staleness guard: `source_narration_script` must equal the
        current `FinalStoryPlanContract.narration_script` exactly
        (normalized) -- a narration change must never be silently ignored.
        """
        if normalize_text(self.source_narration_script) != normalize_text(story_plan.narration_script):
            raise ValueError(
                "source_narration_script does not match the current "
                "FinalStoryPlanContract's narration_script."
            )
