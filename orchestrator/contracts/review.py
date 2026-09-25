"""Verdicts from the fresh-context reviewers (Story 4.5, AD-18).

A producing agent's own QA is necessary but not sufficient: its output is
approved only once a separate query, sequenced by the orchestrator and holding
none of the producer's context, approves it too.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


# A reviewer's likeness rejection uses this code. It contains "character", one
# of `SEED_QA_CHARACTER_MARKERS`, so a Veo `seed_qa` rejection with it moves
# the likeness ladder, and `_prior_character_rejections` sees it on resume.
CHARACTER_LIKENESS_CODE = "CHARACTER_LIKENESS"


class StoryCritiqueIssue(ContractModel):
    dimension: Annotated[str, Field(min_length=1)]
    evidence: Annotated[str, Field(min_length=1)]
    fix: Annotated[str, Field(min_length=1)]


class StoryCritiqueContract(ContractModel):
    produced_by: Literal["story_critic"]
    verdict: Literal["APPROVE", "REVISE"]
    issues: list[StoryCritiqueIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def revise_names_its_issues(self) -> "StoryCritiqueContract":
        if self.verdict == "REVISE" and not self.issues:
            raise ValueError("A REVISE verdict must list at least one issue")
        return self


class VisualQAVerdictContract(ContractModel):
    produced_by: Literal["visual_reviewer"]
    approved: bool
    failure_stage: Literal["seed_qa", "clip_qa", "still_qa"] | None = None
    failure_code: str | None = None
    reasons: list[str] = Field(default_factory=list)
    summary: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def rejection_is_specific(self) -> "VisualQAVerdictContract":
        if self.approved:
            if self.failure_stage is not None or self.failure_code is not None:
                raise ValueError("An approval carries no failure_stage or failure_code")
        else:
            if self.failure_stage is None or not (self.failure_code or "").strip():
                raise ValueError("A rejection requires failure_stage and failure_code")
            if not any(reason.strip() for reason in self.reasons):
                raise ValueError("A rejection requires at least one reason")
        return self
