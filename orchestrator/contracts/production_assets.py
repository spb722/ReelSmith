"""Authoritative, partial production-asset manifest.

Story 2.3 records approved Veo clips.  Story 2.4 will add still entries and
perform the final completeness check, so this contract intentionally permits a
validated subset of the visual plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.contracts.veo import VeoResultContract, sha256_file


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ProductionAssetEntry(ContractModel):
    shot_sequence: Annotated[int, Field(ge=1)]
    shot_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    generation_mode: Literal["VEO", "STILL"]
    asset_type: Literal["video", "still"]
    local_path: Annotated[str, Field(min_length=1)]
    asset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    approved: Literal[True] = True
    source_asset_ids: Annotated[list[str], Field(min_length=1)]
    seed_path: str | None = None
    seed_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    generation_model: Annotated[str, Field(min_length=1)]
    gcs_uri: str | None = None
    operation_name: str | None = None
    operation_dump_path: str | None = None
    preview_paths: list[str] = Field(default_factory=list)
    qa_summary: Annotated[str, Field(min_length=1)]
    cost_usd: Annotated[float, Field(ge=0)] = 0.0

    @model_validator(mode="after")
    def asset_is_materialized(self) -> "ProductionAssetEntry":
        path = Path(self.local_path)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Approved production asset is missing or empty: {path}")
        if sha256_file(path) != self.asset_sha256:
            raise ValueError("Approved production asset content no longer matches asset_sha256")
        if self.generation_mode == "VEO":
            if self.asset_type != "video":
                raise ValueError("A VEO production entry must have asset_type='video'")
            if not self.seed_path or not self.seed_sha256:
                raise ValueError("A VEO production entry requires seed provenance")
            if not Path(self.seed_path).is_file():
                raise ValueError("A VEO production entry's seed file is missing")
            if not Path(self.seed_path).resolve().is_relative_to(Path("generated/veo_seeds").resolve()):
                raise ValueError("A VEO seed must be under generated/veo_seeds/")
            if sha256_file(Path(self.seed_path)) != self.seed_sha256:
                raise ValueError("A VEO production entry's seed hash no longer matches")
            if not self.operation_dump_path or not Path(self.operation_dump_path).is_file():
                raise ValueError("A VEO production entry requires its operation dump")
            if not Path(self.local_path).resolve().is_relative_to(Path("generated/veo").resolve()):
                raise ValueError("A VEO production asset must be under generated/veo/")
            if not Path(self.operation_dump_path).resolve().is_relative_to(Path("metadata/veo_operations").resolve()):
                raise ValueError("A VEO operation dump must be under metadata/veo_operations/")
            if not self.preview_paths:
                raise ValueError("A VEO production entry requires inspected preview paths")
            for preview in self.preview_paths:
                preview_path = Path(preview)
                if not preview_path.is_file() or not preview_path.resolve().is_relative_to(Path("generated/veo/previews").resolve()):
                    raise ValueError("VEO preview paths must be existing files under generated/veo/previews/")
        elif self.asset_type != "still":
            raise ValueError("A STILL production entry must have asset_type='still'")
        return self

    @classmethod
    def from_veo_result(cls, result: VeoResultContract, source_asset_ids: list[str]) -> "ProductionAssetEntry":
        if not result.approved:
            raise ValueError("Only an approved Veo result can become a production asset")
        return cls(
            shot_sequence=result.shot_sequence,
            shot_fingerprint=result.shot_fingerprint,
            generation_mode="VEO",
            asset_type="video",
            local_path=result.local_video_path,
            asset_sha256=result.video_sha256,
            source_asset_ids=list(source_asset_ids),
            seed_path=result.seed.local_path,
            seed_sha256=result.seed.seed_sha256,
            generation_model=result.model,
            gcs_uri=result.gcs_uri,
            operation_name=result.operation_name,
            operation_dump_path=result.operation_dump_path,
            preview_paths=list(result.preview_paths),
            qa_summary=result.qa_summary,
            cost_usd=result.cost_usd + result.seed.cost_usd,
        )


class ProductionAssetsContract(ContractModel):
    produced_by: Literal["orchestrator"] = "orchestrator"
    shots: dict[str, ProductionAssetEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def keys_match_entries(self) -> "ProductionAssetsContract":
        for key, entry in self.shots.items():
            if key != str(entry.shot_sequence):
                raise ValueError(
                    f"Production asset key {key!r} does not match shot_sequence {entry.shot_sequence}"
                )
        return self
