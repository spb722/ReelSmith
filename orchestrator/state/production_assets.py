"""Single-writer, atomic persistence for production_assets.json."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from orchestrator.contracts.production_assets import ProductionAssetEntry, ProductionAssetsContract
from orchestrator.state.run_manifest import _atomic_write_json


PRODUCTION_ASSETS_FILE = Path("metadata/production_assets.json")


def load_production_assets(path: Path = PRODUCTION_ASSETS_FILE) -> ProductionAssetsContract:
    if not path.exists():
        return ProductionAssetsContract()
    return ProductionAssetsContract.model_validate_json(path.read_text(encoding="utf-8"))


def upsert_production_asset(
    entry: ProductionAssetEntry,
    *,
    path: Path = PRODUCTION_ASSETS_FILE,
    writer: Literal["orchestrator"] = "orchestrator",
) -> ProductionAssetsContract:
    """Merge one approved entry by shot id and atomically replace the file."""

    if writer != "orchestrator":
        raise ValueError("Only the orchestrator may write ProductionAssetsContract")
    manifest = load_production_assets(path)
    merged = dict(manifest.shots)
    merged[str(entry.shot_sequence)] = entry
    updated = ProductionAssetsContract(shots=merged)
    _atomic_write_json(path, updated.model_dump(mode="json"))
    return updated
