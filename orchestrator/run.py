"""Orchestrator CLI entrypoint: `python -m orchestrator.run <source_images_dir>`.

Runs preflight, deterministic ingestion, and bounded screenshot understanding.
The validated analyzed-assets file is the single-stage resume checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

from orchestrator.agents.asset_analyst import analyze_assets
from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.preflight import _check_budget, run_preflight
from orchestrator.settings import Settings, SettingsError, load_settings
from orchestrator.state.run_manifest import (
    RUN_STATE_DIR,
    RunManifest,
    _atomic_write_json,
    build_failure_report,
    load_run_manifest,
    save_run_manifest,
    write_failure_report,
)
from orchestrator.tools.deterministic_tools import ASSETS_FILE, ingest

ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
MAX_ASSET_ANALYST_ATTEMPTS = 4


def _halt(
    stage_id: str, failed_contract_name: str, reason: str, *,
    attempt_count: int = 1, partial_artifact_paths: list[str] | None = None,
) -> int:
    report = build_failure_report(
        stage_id=stage_id,
        failed_contract_name=failed_contract_name,
        reason=reason,
        attempt_count=attempt_count,
        partial_artifact_paths=partial_artifact_paths,
    )
    path = write_failure_report(report, output_dir=RUN_STATE_DIR)

    print(f"HALT [{stage_id}]: {reason}", file=sys.stderr)
    print(f"Failure report written to {path}", file=sys.stderr)

    return 1


async def run_screenshot_stage(source_images_dir: Path, settings: Settings, manifest: RunManifest) -> int:
    """Ingest on every invocation; only a valid matching artifact skips Claude."""
    try:
        # Calling the SDK tool's handler keeps ordering deterministic and costs
        # nothing: the same in-process function is also exposed on its MCP server.
        ingestion = await ingest.handler({"source_images_dir": str(source_images_dir)})
        assets = json.loads(ingestion["content"][0]["text"])["assets"]
    except Exception as exc:
        return _halt("ingest", "AssetManifest", str(exc))

    feedback = None
    previous_output = None
    try:
        persisted = AnalyzedAssetsContract.model_validate_json(ANALYZED_ASSETS_FILE.read_text(encoding="utf-8"))
        persisted.validate_sources(assets)
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        feedback = f"Persisted analyzed-assets contract is invalid for this run: {exc}"
        print(feedback, file=sys.stderr)
    else:
        print(f"asset_analyst skipped: validated {ANALYZED_ASSETS_FILE}")
        return 0

    count = manifest.iteration_counts.get("asset_analyst", 0)
    partial_paths = [str(ASSETS_FILE)]
    if ANALYZED_ASSETS_FILE.exists():
        partial_paths.append(str(ANALYZED_ASSETS_FILE))
    partial_paths.extend(str(p) for p in sorted(RUN_STATE_DIR.glob("asset_analyst_attempt_*.json")))

    def halt(reason: str) -> int:
        return _halt(
            "asset_analyst", "AnalyzedAssetsContract", reason,
            attempt_count=count if type(count) is int else 0,
            partial_artifact_paths=partial_paths,
        )

    if type(count) is not int or count < 0:
        return halt("Invalid persisted asset_analyst iteration count")

    while count < MAX_ASSET_ANALYST_ATTEMPTS:
        budget_failure = _check_budget(settings, manifest.budget_spent_usd)
        if budget_failure:
            return halt(budget_failure.reason or "Budget exhausted")

        count += 1
        manifest.iteration_counts["asset_analyst"] = count
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        try:
            result = await analyze_assets(
                assets,
                max_budget_usd=settings.max_budget_usd - manifest.budget_spent_usd,
                feedback=feedback,
                previous_output=previous_output,
            )
        except Exception as exc:
            feedback = f"Claude attempt failed: {type(exc).__name__}: {exc}"
            previous_output = None
        else:
            cost = result.total_cost_usd
            if cost is None or not math.isfinite(cost) or cost < 0:
                return halt("Claude returned no usable cost; cannot safely enforce the run budget")
            manifest.budget_spent_usd += cost
            manifest.session_id = result.session_id
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            previous_output = result.structured_output
            attempt_path = RUN_STATE_DIR / f"asset_analyst_attempt_{count}.json"
            _atomic_write_json(attempt_path, {
                "structured_output": previous_output, "result": result.result,
                "subtype": result.subtype, "errors": result.errors,
            })
            partial_paths.append(str(attempt_path))
            budget_failure = _check_budget(settings, manifest.budget_spent_usd)
            if budget_failure or result.subtype == "error_max_budget_usd":
                return halt(budget_failure.reason if budget_failure else "Claude reached its remaining budget limit")
            try:
                if result.is_error:
                    raise ValueError(f"Claude failed ({result.subtype}): {result.errors or result.result}")
                contract = AnalyzedAssetsContract.model_validate(previous_output)
                contract.validate_sources(assets)
            except ValueError as exc:
                feedback = str(exc)
            else:
                _atomic_write_json(ANALYZED_ASSETS_FILE, contract.model_dump(mode="json"))
                print(f"asset_analyst validated: {ANALYZED_ASSETS_FILE}")
                return 0

        print(f"asset_analyst attempt {count}/{MAX_ASSET_ANALYST_ATTEMPTS} failed: {feedback}", file=sys.stderr)

    return halt(f"Retry ceiling exhausted: {feedback or 'four attempts already recorded for this run'}")


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

    print(f"Preflight passed for source images dir: {args.source_images_dir}.")
    return asyncio.run(run_screenshot_stage(args.source_images_dir, settings, manifest))


if __name__ == "__main__":
    sys.exit(main())
