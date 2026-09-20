"""Subtitle-cues contract. Unlike `FinalStoryPlanContract`/`VisualPlanContract`
(which invent a fresh scored quality bar because their producing agent's own
creative judgment is the quality gate), `extract_word_timing.py`'s 0.85
exact-match-ratio gate and `build_subtitle_cues.py`'s 0.98 alignment recheck
already raise on their own mechanical thresholds before this contract is ever
constructed -- `SubtitleCuesContract`'s job is provenance and staleness, not
re-deriving a quality gate (Story 1.5 Design Notes).
"""

from __future__ import annotations

import os
import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.text_normalization import normalize_text
from orchestrator.state.voice_cache import AUDIO_CACHE_FILE, validate_audio_provenance, selected_voice_name

# Duplicated from orchestrator.tools.deterministic_tools.MIN_ALIGNMENT_RATIO
# rather than imported: contracts stay free of the tools layer's heavier
# dependencies (google-genai/google-auth). Must be kept equal to that
# module's own DP-recheck threshold (AD-10) -- defense in depth, since the
# tool already enforces this at generation time, but a persisted file's own
# claimed ratio must also be checked on every resume-skip.
MIN_ALIGNMENT_RATIO = 0.98

# AD-11: a persisted contract without its own audio is not a valid skip
# target (cues without their audio can't be assembled downstream).
AUDIO_FILE = Path("audio/narration.wav")


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
    # Story 2.2: additive rendering hint `remotion/src/components/Subtitles.tsx`
    # already reads (selects a typography variant) -- not part of this
    # contract's own provenance/staleness quality gate (module docstring),
    # just carried through so the renderer never re-derives it.
    # `SkipJsonSchema`, matching `visual_plan.py`'s Story 2.1/2.2 fields: the
    # producing pipeline stage doesn't see or set this in its own schema.
    style_hint: SkipJsonSchema[Literal["NORMAL", "IMPACT", "EMPHASIS", "REFLECTION"]] = "NORMAL"


class SubtitleCuesContract(ContractModel):
    # Required, no default: a legacy `build_subtitle_cues.py` (manually-run)
    # manifest never has this field, so it fails validation here rather
    # than silently satisfying resume forever (mirrors FinalStoryPlanContract/
    # VisualPlanContract's AD-1/AD-6 pattern).
    produced_by: Literal["voice_agent"]
    source_narration_script: str
    alignment_ratio: float
    audio_provenance: dict | None = None
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
        if not math.isfinite(self.alignment_ratio) or not MIN_ALIGNMENT_RATIO <= self.alignment_ratio <= 1.0:
            raise ValueError(
                f"alignment_ratio {self.alignment_ratio} is below the required minimum "
                f"{MIN_ALIGNMENT_RATIO} (AD-10)."
            )
        if not AUDIO_FILE.exists():
            raise ValueError(f"Referenced audio file {AUDIO_FILE} does not exist (AD-11).")
        selected_voice_name()  # An explicitly empty override is invalid even on resume.
        if self.audio_provenance is not None:
            validate_audio_provenance(
                self.audio_provenance, story_plan.narration_script,
                story_plan.voice_direction.model_dump(mode="json"),
            )
        elif "TTS_VOICE_NAME" in os.environ or AUDIO_CACHE_FILE.exists():
            raise ValueError("Legacy subtitle cues have no verifiable voice/audio provenance")
