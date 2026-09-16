"""Orchestrator CLI entrypoint: `python -m orchestrator.run <source_images_dir>`.

Runs preflight, deterministic ingestion, and bounded screenshot understanding
(`run_screenshot_stage`), then -- once that stage has a validated
`AnalyzedAssetsContract` -- bounded narration writing/review/revision
(`run_narration_stage`). Each stage's validated persisted contract is that
stage's resume checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

from claude_agent_sdk import ResultMessage

from orchestrator.agents.asset_analyst import analyze_assets
from orchestrator.agents.story_agent import write_story
from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
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
FINAL_STORY_PLAN_FILE = Path("metadata/final_story_plan.json")
MAX_ASSET_ANALYST_ATTEMPTS = 4
MAX_STORY_AGENT_ATTEMPTS = 4


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


async def run_bounded_agent_stage(
    *,
    stage_id: str,
    failed_contract_name: str,
    contract_cls: type,
    persisted_file: Path,
    validate_contract: Callable[[object], None],
    max_attempts: int,
    settings: Settings,
    manifest: RunManifest,
    base_partial_paths: list[str],
    attempt_file_prefix: str,
    call_agent: Callable[[str | None, object, float], Awaitable[ResultMessage]],
) -> int:
    """Shared bounded-retry/resume/halt control flow (Story 1.2's
    `run_screenshot_stage` shape), reused by every stage in this shape:
    skip on a validated persisted contract, else retry up to `max_attempts`
    with feedback, self-correcting each attempt, halting via the shared
    failure-report path on ceiling exhaustion or budget exhaustion.
    `validate_contract` raises `ValueError` for both the resume-staleness
    check and the post-attempt quality/source-id check -- the same method
    serves both, matching each contract's own `validate_sources`.
    """
    feedback = None
    previous_output = None
    try:
        persisted = contract_cls.model_validate_json(persisted_file.read_text(encoding="utf-8"))
        validate_contract(persisted)
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        feedback = f"Persisted {failed_contract_name} is invalid for this run: {exc}"
        print(feedback, file=sys.stderr)
    else:
        print(f"{stage_id} skipped: validated {persisted_file}")
        return 0

    count = manifest.iteration_counts.get(stage_id, 0)
    partial_paths = list(base_partial_paths)
    if persisted_file.exists():
        partial_paths.append(str(persisted_file))
    partial_paths.extend(str(p) for p in sorted(RUN_STATE_DIR.glob(f"{attempt_file_prefix}_*.json")))

    def halt(reason: str) -> int:
        return _halt(
            stage_id, failed_contract_name, reason,
            attempt_count=count if type(count) is int else 0,
            partial_artifact_paths=partial_paths,
        )

    if type(count) is not int or count < 0:
        return halt(f"Invalid persisted {stage_id} iteration count")

    while count < max_attempts:
        budget_failure = _check_budget(settings, manifest.budget_spent_usd)
        if budget_failure:
            return halt(budget_failure.reason or "Budget exhausted")

        count += 1
        manifest.iteration_counts[stage_id] = count
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        try:
            result = await call_agent(
                feedback, previous_output, settings.max_budget_usd - manifest.budget_spent_usd
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
            attempt_path = RUN_STATE_DIR / f"{attempt_file_prefix}_{count}.json"
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
                contract = contract_cls.model_validate(previous_output)
                validate_contract(contract)
            except ValueError as exc:
                feedback = str(exc)
            else:
                _atomic_write_json(persisted_file, contract.model_dump(mode="json"))
                print(f"{stage_id} validated: {persisted_file}")
                return 0

        print(f"{stage_id} attempt {count}/{max_attempts} failed: {feedback}", file=sys.stderr)

    return halt(f"Retry ceiling exhausted: {feedback or f'{max_attempts} attempts already recorded for this run'}")


async def run_screenshot_stage(source_images_dir: Path, settings: Settings, manifest: RunManifest) -> int:
    """Ingest on every invocation; only a valid matching artifact skips Claude."""
    try:
        # Calling the SDK tool's handler keeps ordering deterministic and costs
        # nothing: the same in-process function is also exposed on its MCP server.
        ingestion = await ingest.handler({"source_images_dir": str(source_images_dir)})
        assets = json.loads(ingestion["content"][0]["text"])["assets"]
    except Exception as exc:
        return _halt("ingest", "AssetManifest", str(exc))

    async def call_agent(feedback: str | None, previous_output: object, remaining_budget: float):
        return await analyze_assets(
            assets, max_budget_usd=remaining_budget, feedback=feedback, previous_output=previous_output,
        )

    def validate_contract(contract: AnalyzedAssetsContract) -> None:
        contract.validate_sources(assets)

    return await run_bounded_agent_stage(
        stage_id="asset_analyst",
        failed_contract_name="AnalyzedAssetsContract",
        contract_cls=AnalyzedAssetsContract,
        persisted_file=ANALYZED_ASSETS_FILE,
        validate_contract=validate_contract,
        max_attempts=MAX_ASSET_ANALYST_ATTEMPTS,
        settings=settings,
        manifest=manifest,
        base_partial_paths=[str(ASSETS_FILE)],
        attempt_file_prefix="asset_analyst_attempt",
        call_agent=call_agent,
    )


async def run_narration_stage(settings: Settings, manifest: RunManifest) -> int:
    """Write, self-review, and revise narration from the already-validated
    `AnalyzedAssetsContract` -- gated on that contract existing, since
    `story_agent` has no Read tool and never re-derives it itself.
    """
    try:
        analyzed = AnalyzedAssetsContract.model_validate_json(ANALYZED_ASSETS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("story_agent", "AnalyzedAssetsContract", f"No validated analyzed-assets contract available: {exc}")

    valid_asset_ids = {asset.asset_id for asset in analyzed.assets}
    assets_bundle = [asset.model_dump(mode="json") for asset in analyzed.assets]

    async def call_agent(feedback: str | None, previous_output: object, remaining_budget: float):
        return await write_story(
            assets_bundle, max_budget_usd=remaining_budget, feedback=feedback, previous_output=previous_output,
        )

    def validate_contract(contract: FinalStoryPlanContract) -> None:
        contract.validate_sources(valid_asset_ids)

    return await run_bounded_agent_stage(
        stage_id="story_agent",
        failed_contract_name="FinalStoryPlanContract",
        contract_cls=FinalStoryPlanContract,
        persisted_file=FINAL_STORY_PLAN_FILE,
        validate_contract=validate_contract,
        max_attempts=MAX_STORY_AGENT_ATTEMPTS,
        settings=settings,
        manifest=manifest,
        base_partial_paths=[str(ANALYZED_ASSETS_FILE)],
        attempt_file_prefix="story_agent_attempt",
        call_agent=call_agent,
    )


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
    screenshot_result = asyncio.run(run_screenshot_stage(args.source_images_dir, settings, manifest))
    if screenshot_result != 0:
        return screenshot_result
    return asyncio.run(run_narration_stage(settings, manifest))


if __name__ == "__main__":
    sys.exit(main())
