"""Run state: budget spent, iteration counts, session id (AD-14's derived
resume pointer lives in contracts, not here), and the shared minimal
failure-report envelope every agent's halt writes through (AD-6/NFR5).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# All scripts run from the repo root (AGENTS.md convention); this is a
# repo-root-relative path, consistent with every existing script's
# metadata/generated paths.
RUN_STATE_DIR = Path("orchestrator_runs")
RUN_MANIFEST_FILENAME = "run_manifest.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    temp.replace(path)


@dataclass
class RunManifest:
    budget_spent_usd: float = 0.0
    iteration_counts: dict[str, int] = field(default_factory=dict)
    session_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunManifest":
        budget_spent_usd = data.get("budget_spent_usd", 0.0)

        try:
            budget_spent_usd = float(budget_spent_usd)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"budget_spent_usd must be numeric, got {budget_spent_usd!r}"
            ) from exc

        if math.isnan(budget_spent_usd):
            raise ValueError("budget_spent_usd must not be NaN")

        iteration_counts = data.get("iteration_counts", {})
        if not isinstance(iteration_counts, dict):
            raise ValueError("iteration_counts must be a dict")

        session_id = data.get("session_id")

        return cls(
            budget_spent_usd=budget_spent_usd,
            iteration_counts=iteration_counts,
            session_id=session_id,
        )


def save_run_manifest(manifest: RunManifest, state_dir: Path = RUN_STATE_DIR) -> Path:
    path = state_dir / RUN_MANIFEST_FILENAME
    _atomic_write_json(path, manifest.to_dict())
    return path


def load_run_manifest(state_dir: Path = RUN_STATE_DIR) -> RunManifest:
    """Load the run manifest, failing safe on any corruption.

    A corrupt/invalid `run_manifest.json` (bad JSON, non-numeric
    `budget_spent_usd`, etc.) must never raise and must never block the
    resumed-invocation preflight guarantee (AD-13) from running -- it
    falls back to a fresh `RunManifest()` instead.
    """

    path = state_dir / RUN_MANIFEST_FILENAME

    try:
        if not path.exists():
            return RunManifest()

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return RunManifest()

        return RunManifest.from_dict(data)
    except (json.JSONDecodeError, ValueError, TypeError, OSError, AttributeError):
        return RunManifest()


@dataclass
class FailureReport:
    """The shared minimal envelope every agent's halt writes (AD-6/NFR5)."""

    stage_id: str
    failed_contract_name: str
    reason: str
    attempt_count: int
    timestamp: str
    partial_artifact_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_failure_report(
    stage_id: str,
    failed_contract_name: str,
    reason: str,
    attempt_count: int,
    partial_artifact_paths: Optional[list[str]] = None,
) -> FailureReport:
    return FailureReport(
        stage_id=stage_id,
        failed_contract_name=failed_contract_name,
        reason=reason,
        attempt_count=attempt_count,
        timestamp=datetime.now(timezone.utc).isoformat(),
        partial_artifact_paths=list(partial_artifact_paths or []),
    )


def write_failure_report(report: FailureReport, output_dir: Path = RUN_STATE_DIR) -> Path:
    """Write the failure report atomically (temp file + `replace()`),
    matching `save_run_manifest`'s existing pattern -- a crash mid-write
    can never leave a truncated report.
    """

    timestamp_slug = (
        report.timestamp.replace(":", "").replace(".", "").replace("+", "")
    )
    filename = f"failure_{report.stage_id}_{timestamp_slug}.json"
    path = output_dir / filename

    _atomic_write_json(path, report.to_dict())

    return path
