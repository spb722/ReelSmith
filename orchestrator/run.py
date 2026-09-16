"""Orchestrator CLI entrypoint: `python -m orchestrator.run <source_images_dir>`.

Runs preflight (AD-13) before any stage executes -- on both a fresh and a
resumed invocation -- and halts via the shared failure-report path
(AD-6/NFR5) on any preflight or budget failure. Stage execution itself
(asset understanding, story writing, etc.) starts at Story 1.2 and is
intentionally out of scope here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from orchestrator.preflight import run_preflight
from orchestrator.settings import SettingsError, load_settings
from orchestrator.state.run_manifest import (
    RUN_STATE_DIR,
    build_failure_report,
    load_run_manifest,
    write_failure_report,
)


def _halt(stage_id: str, failed_contract_name: str, reason: str) -> int:
    report = build_failure_report(
        stage_id=stage_id,
        failed_contract_name=failed_contract_name,
        reason=reason,
        attempt_count=1,
        partial_artifact_paths=[],
    )
    path = write_failure_report(report, output_dir=RUN_STATE_DIR)

    print(f"HALT [{stage_id}]: {reason}", file=sys.stderr)
    print(f"Failure report written to {path}", file=sys.stderr)

    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.run",
        description="book_reels orchestrator entrypoint",
    )
    parser.add_argument(
        "source_images_dir",
        type=Path,
        help="Directory containing the source screenshots for this run.",
    )
    args = parser.parse_args(argv)

    # A resumed invocation isn't literally "stage 1" -- but preflight
    # (AD-13) must still run before the first stage that will actually
    # execute. A corrupt manifest must never block that, so this fails
    # safe (fresh manifest) rather than raising.
    manifest = load_run_manifest(RUN_STATE_DIR)

    try:
        settings = load_settings()
    except SettingsError as exc:
        return _halt("settings", "Settings", str(exc))

    result = run_preflight(
        settings=settings,
        budget_spent_usd=manifest.budget_spent_usd,
    )

    if not result.passed:
        return _halt(
            "preflight",
            result.failed_check or "preflight",
            result.reason or "preflight failed",
        )

    print(
        f"Preflight passed for source images dir: {args.source_images_dir}. "
        "Stage execution begins in Story 1.2."
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
