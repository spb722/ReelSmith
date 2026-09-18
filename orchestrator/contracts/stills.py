"""Contracts for one autonomous STILL shot.

Mirrors `veo.py`'s `VeoResultContract`/`VeoFailureContract`/`VeoOutcomeContract`
shape (Code Map), dropping the seed/operation-dump concept entirely: a still
is a single-stage Gemini image-edit call per shot -- confirmed unnecessary by
`ProductionAssetEntry`'s existing STILL branch and the legacy script's own
one-call flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.contracts.veo import sha256_file


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


STILLS_DIR = Path("generated/stills")


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


class StillResultContract(ContractModel):
    produced_by: Literal["still_tool"] = "still_tool"
    shot_sequence: Annotated[int, Field(ge=1)]
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_asset_id: Annotated[str, Field(min_length=1)]
    local_image_path: Annotated[str, Field(min_length=1)]
    image_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    model: Annotated[str, Field(min_length=1)]
    approved: bool = False
    qa_summary: str = ""
    cost_usd: Annotated[float, Field(ge=0)] = 0.0

    @model_validator(mode="after")
    def validate_result_provenance(self) -> "StillResultContract":
        image_path = Path(self.local_image_path)
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise ValueError(f"Still result does not contain a usable local PNG: {image_path}")
        if sha256_file(image_path) != self.image_sha256:
            raise ValueError("Still result content no longer matches image_sha256")
        if not _is_within(image_path, STILLS_DIR):
            raise ValueError("Still output must be under generated/stills/")
        if image_path.name != f"shot_{self.shot_sequence:02d}.png":
            raise ValueError("Still output must use the deterministic per-shot PNG filename")
        if self.approved and not self.qa_summary.strip():
            raise ValueError("An approved still requires a non-empty qa_summary")
        return self


class StillFailureContract(ContractModel):
    produced_by: Literal["stills_agent", "still_tool", "orchestrator"]
    shot_sequence: Annotated[int, Field(ge=1)]
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    stage: Literal["still_generation", "still_qa", "budget", "contract"]
    code: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    retryable: bool
    attempt: Annotated[int, Field(ge=1)]
    partial_artifact_paths: list[str] = Field(default_factory=list)
    cost_usd: Annotated[float, Field(ge=0)] = 0.0


class StillOutcomeContract(ContractModel):
    produced_by: Literal["stills_agent", "still_tool"]
    status: Literal["SUCCESS", "FAILURE"]
    shot_sequence: Annotated[int, Field(ge=1)]
    generation_mode: Literal["STILL"] = "STILL"
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    result: StillResultContract | None = None
    failure: StillFailureContract | None = None

    @model_validator(mode="after")
    def exactly_one_payload(self) -> "StillOutcomeContract":
        if self.status == "SUCCESS":
            if self.result is None or self.failure is not None:
                raise ValueError("SUCCESS requires result and forbids failure")
            if self.produced_by == "stills_agent" and not self.result.approved:
                raise ValueError("A successful agent outcome requires an approved still")
            if self.result.shot_sequence != self.shot_sequence:
                raise ValueError("Outcome and result shot_sequence differ")
            if self.result.shot_fingerprint != self.shot_fingerprint:
                raise ValueError("Outcome and result shot fingerprints differ")
        else:
            if self.failure is None or self.result is not None:
                raise ValueError("FAILURE requires failure and forbids result")
            if self.failure.shot_sequence != self.shot_sequence:
                raise ValueError("Outcome and failure shot_sequence differ")
            if self.failure.shot_fingerprint != self.shot_fingerprint:
                raise ValueError("Outcome and failure shot fingerprints differ")
        return self

    @property
    def cost_usd(self) -> float:
        if self.result is not None:
            return self.result.cost_usd
        return self.failure.cost_usd
