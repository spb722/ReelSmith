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

from orchestrator.settings import DEFAULT_MAX_ATTEMPTS

# All scripts run from the repo root (AGENTS.md convention); this is a
# repo-root-relative path, consistent with every existing script's
# metadata/generated paths.
RUN_STATE_DIR = Path("orchestrator_runs")
# The operator-editable file: every ceiling and every counter in one place, so
# a stuck run is unstuck by opening it rather than by hand-patching state.
CONFIG_FILENAME = "config.json"
# The pre-config layout. Still read, never written, so an in-flight reel keeps
# resuming after the upgrade.
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
    max_budget_usd: Optional[float] = None
    max_attempts: dict[str, int] = field(default_factory=dict)

    def attempts_allowed(self, stage: str) -> int:
        """The ceiling for a stage, from the project's config or the default."""

        return int(self.max_attempts.get(stage, DEFAULT_MAX_ATTEMPTS.get(stage, 3)))

    def to_dict(self) -> dict[str, Any]:
        """Two clearly separated halves: what you may spend, what you have.

        `limits` is yours to edit. `usage` is written by the run, and is also
        where you reset a counter or correct a wrong charge.
        """

        limits = {"max_attempts": dict(self.max_attempts)}
        if self.max_budget_usd is not None:
            limits["max_budget_usd"] = self.max_budget_usd
        return {
            "limits": limits,
            "usage": {
                "budget_spent_usd": self.budget_spent_usd,
                "attempts_used": dict(self.iteration_counts),
                "session_id": self.session_id,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunManifest":
        # Accepts both the config.json shape and the flat run_manifest.json
        # that preceded it, so an in-flight reel upgrades without losing state.
        limits = data.get("limits") or {}
        usage = data.get("usage") or data
        if not isinstance(limits, dict) or not isinstance(usage, dict):
            raise ValueError("limits and usage must be objects")

        max_budget_usd = limits.get("max_budget_usd")
        if max_budget_usd is not None:
            max_budget_usd = float(max_budget_usd)
        max_attempts = limits.get("max_attempts") or {}
        if not isinstance(max_attempts, dict):
            raise ValueError("limits.max_attempts must be an object")
        max_attempts = {str(k): int(v) for k, v in max_attempts.items()}

        data = usage
        budget_spent_usd = data.get("budget_spent_usd", 0.0)

        try:
            budget_spent_usd = float(budget_spent_usd)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"budget_spent_usd must be numeric, got {budget_spent_usd!r}"
            ) from exc

        if math.isnan(budget_spent_usd):
            raise ValueError("budget_spent_usd must not be NaN")

        iteration_counts = data.get("attempts_used", data.get("iteration_counts", {}))
        if not isinstance(iteration_counts, dict):
            raise ValueError("attempts_used must be a dict")

        session_id = data.get("session_id")

        return cls(
            budget_spent_usd=budget_spent_usd,
            iteration_counts=iteration_counts,
            session_id=session_id,
            max_budget_usd=max_budget_usd,
            max_attempts=max_attempts,
        )


def config_file(state_dir: Path = RUN_STATE_DIR) -> Path:
    """The one file an operator edits to change a limit or reset a counter."""

    return state_dir / CONFIG_FILENAME


def save_run_manifest(manifest: RunManifest, state_dir: Path = RUN_STATE_DIR) -> Path:
    """Write limits and usage back to the project's config.

    Ceilings are always written out, defaults included, so the file shows every
    knob that exists rather than only the ones already changed.
    """

    if not manifest.max_attempts:
        manifest.max_attempts = dict(DEFAULT_MAX_ATTEMPTS)
    path = config_file(state_dir)
    _atomic_write_json(path, manifest.to_dict())
    return path


def load_run_manifest(state_dir: Path = RUN_STATE_DIR) -> RunManifest:
    """Load the run manifest, failing safe on any corruption.

    A corrupt/invalid config (bad JSON, non-numeric
    `budget_spent_usd`, etc.) must never raise and must never block the
    resumed-invocation preflight guarantee (AD-13) from running -- it
    falls back to a fresh `RunManifest()` instead.
    """

    path = config_file(state_dir)
    # The pre-config file is read once, then superseded on the next save.
    legacy = state_dir / RUN_MANIFEST_FILENAME
    if not path.exists() and legacy.exists():
        path = legacy

    try:
        if not path.exists():
            return RunManifest(max_attempts=dict(DEFAULT_MAX_ATTEMPTS))

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return RunManifest(max_attempts=dict(DEFAULT_MAX_ATTEMPTS))

        manifest = RunManifest.from_dict(data)
        # Any ceiling the file omits falls back to the built-in default, so a
        # hand-edited config that drops a key still runs.
        manifest.max_attempts = {**DEFAULT_MAX_ATTEMPTS, **manifest.max_attempts}
        return manifest
    except (json.JSONDecodeError, ValueError, TypeError, OSError, AttributeError):
        return RunManifest(max_attempts=dict(DEFAULT_MAX_ATTEMPTS))


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
