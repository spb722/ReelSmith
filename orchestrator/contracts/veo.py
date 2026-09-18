"""Contracts for one autonomous Veo shot.

The contracts deliberately carry the complete provenance needed to decide
whether an approved clip is still usable on resume.  They are also the safety
boundary in front of the paid Veo call: an approved recomposed seed with a
matching shot fingerprint is required before generation can begin.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.contracts.visual_plan import Shot


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


def shot_fingerprint(shot: Shot | dict) -> str:
    """Return a stable fingerprint of every field that defines a source shot."""

    if isinstance(shot, Shot):
        payload = shot.model_dump(mode="json")
    else:
        payload = Shot.model_validate(shot).model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


class VeoSeedContract(ContractModel):
    produced_by: Literal["gemini_veo_seed"] = "gemini_veo_seed"
    shot_sequence: Annotated[int, Field(ge=1)]
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_asset_id: Annotated[str, Field(min_length=1)]
    source_image_path: Annotated[str, Field(min_length=1)]
    source_image_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    local_path: Annotated[str, Field(min_length=1)]
    seed_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    prompt: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    approved: bool = False
    qa_summary: str = ""
    cost_usd: Annotated[float, Field(ge=0)] = 0.0

    @model_validator(mode="after")
    def validate_seed_provenance(self) -> "VeoSeedContract":
        seed_path = Path(self.local_path)
        if not seed_path.is_file():
            raise ValueError(f"Veo seed file does not exist: {seed_path}")
        if not _is_within(seed_path, Path("generated/veo_seeds")):
            raise ValueError("Veo seed must be under generated/veo_seeds/")
        if _is_within(seed_path, Path("source_images")):
            raise ValueError("A source_images/ path can never be used as a Veo seed")
        if sha256_file(seed_path) != self.seed_sha256:
            raise ValueError("Veo seed content no longer matches seed_sha256")

        source_path = Path(self.source_image_path)
        if not source_path.is_file():
            raise ValueError(f"Seed source image does not exist: {source_path}")
        if sha256_file(source_path) != self.source_image_sha256:
            raise ValueError("Seed source content no longer matches source_image_sha256")
        if self.approved and not self.qa_summary.strip():
            raise ValueError("An approved Veo seed requires a non-empty qa_summary")
        return self

    def validate_for_shot(self, shot: Shot) -> None:
        # Re-run filesystem/hash checks at the exact paid-call boundary.
        # This also protects callers that received an already-instantiated
        # model whose file was moved or modified after initial validation.
        self.validate_seed_provenance()
        if shot.generation_mode != "VEO":
            raise ValueError(f"Shot {shot.sequence} is not assigned VEO mode")
        if self.shot_sequence != shot.sequence:
            raise ValueError("Veo seed shot_sequence does not match the requested shot")
        if self.shot_fingerprint != shot_fingerprint(shot):
            raise ValueError("Veo seed was produced for a different or stale source shot")
        if self.source_asset_id not in shot.source_asset_ids:
            raise ValueError("Veo seed source asset is not cited by the source shot")
        if not self.approved:
            raise ValueError("Veo seed has not passed semantic visual QA")


class VeoResultContract(ContractModel):
    produced_by: Literal["veo_tool"] = "veo_tool"
    shot_sequence: Annotated[int, Field(ge=1)]
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    seed: VeoSeedContract
    local_video_path: Annotated[str, Field(min_length=1)]
    video_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    gcs_uri: str | None = None
    operation_name: str | None = None
    operation_dump_path: Annotated[str, Field(min_length=1)]
    preview_paths: list[str] = Field(default_factory=list)
    model: Annotated[str, Field(min_length=1)]
    approved: bool = False
    qa_summary: str = ""
    cost_usd: Annotated[float, Field(ge=0)] = 0.0

    @model_validator(mode="after")
    def validate_result_provenance(self) -> "VeoResultContract":
        if self.shot_sequence != self.seed.shot_sequence:
            raise ValueError("Veo result and seed shot_sequence differ")
        if self.shot_fingerprint != self.seed.shot_fingerprint:
            raise ValueError("Veo result and seed shot fingerprints differ")
        if not self.seed.approved:
            raise ValueError("A Veo result cannot originate from an unapproved seed")
        video_path = Path(self.local_video_path)
        if not video_path.is_file() or video_path.stat().st_size == 0:
            raise ValueError(f"Veo result does not contain a usable local MP4: {video_path}")
        if sha256_file(video_path) != self.video_sha256:
            raise ValueError("Veo result content no longer matches video_sha256")
        if not _is_within(video_path, Path("generated/veo")):
            raise ValueError("Veo output must be under generated/veo/")
        if video_path.name != f"shot_{self.shot_sequence:02d}.mp4":
            raise ValueError("Veo output must use the deterministic per-shot MP4 filename")
        if not Path(self.operation_dump_path).is_file():
            raise ValueError("Veo result requires its preserved operation dump")
        missing_previews = [path for path in self.preview_paths if not Path(path).is_file()]
        if missing_previews:
            raise ValueError(f"Veo preview files are missing: {missing_previews}")
        outside_previews = [path for path in self.preview_paths if not _is_within(Path(path), Path("generated/veo/previews"))]
        if outside_previews:
            raise ValueError(f"Veo previews must be under generated/veo/previews/: {outside_previews}")
        if self.approved:
            if not self.qa_summary.strip():
                raise ValueError("An approved Veo clip requires a non-empty qa_summary")
            if not self.preview_paths:
                raise ValueError("An approved Veo clip requires inspected preview images")
        return self


class VeoFailureContract(ContractModel):
    produced_by: Literal["veo_agent", "veo_tool", "orchestrator"]
    shot_sequence: Annotated[int, Field(ge=1)]
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    stage: Literal["seed_generation", "seed_qa", "veo_generation", "clip_qa", "budget", "contract"]
    code: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    retryable: bool
    attempt: Annotated[int, Field(ge=1)]
    operation_name: str | None = None
    operation_dump_path: str | None = None
    rai_media_filtered_count: Annotated[int, Field(ge=0)] = 0
    rai_media_filtered_reasons: list[str] = Field(default_factory=list)
    operation_error: object | None = None
    partial_artifact_paths: list[str] = Field(default_factory=list)
    cost_usd: Annotated[float, Field(ge=0)] = 0.0

    @model_validator(mode="after")
    def operation_failure_keeps_dump(self) -> "VeoFailureContract":
        if self.operation_name and not self.operation_dump_path:
            raise ValueError("A completed Veo operation failure must preserve operation_dump_path")
        if self.operation_dump_path and not Path(self.operation_dump_path).is_file():
            raise ValueError("operation_dump_path does not exist")
        return self


class VeoOutcomeContract(ContractModel):
    produced_by: Literal["veo_agent", "veo_tool"]
    status: Literal["SUCCESS", "FAILURE"]
    shot_sequence: Annotated[int, Field(ge=1)]
    generation_mode: Literal["VEO"] = "VEO"
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    result: VeoResultContract | None = None
    failure: VeoFailureContract | None = None

    @model_validator(mode="after")
    def exactly_one_payload(self) -> "VeoOutcomeContract":
        if self.status == "SUCCESS":
            if self.result is None or self.failure is not None:
                raise ValueError("SUCCESS requires result and forbids failure")
            if self.produced_by == "veo_agent" and not self.result.approved:
                raise ValueError("A successful agent outcome requires an approved clip")
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
            return self.result.seed.cost_usd + self.result.cost_usd
        return self.failure.cost_usd
