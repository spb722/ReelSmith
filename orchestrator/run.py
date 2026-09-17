"""Orchestrator CLI entrypoint: `python -m orchestrator.run <source_images_dir>`.

Runs preflight, deterministic ingestion, and bounded screenshot understanding
(`run_screenshot_stage`), then -- once that stage has a validated
`AnalyzedAssetsContract` -- bounded narration writing/review/revision
(`run_narration_stage`), then -- once that stage has a validated
`FinalStoryPlanContract` -- bounded visual/shot-mode planning
(`run_visual_stage`), then -- once that stage has a validated
`VisualPlanContract` too -- bounded voice/word-timing/subtitle-cue
generation (`run_voice_stage`). Each stage's validated persisted contract is
that stage's resume checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

from claude_agent_sdk import ResultMessage

from orchestrator.agents.asset_analyst import analyze_assets
from orchestrator.agents.story_agent import write_story
from orchestrator.agents.visual_agent import plan_visuals
from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.subtitle_cues import SubtitleCuesContract
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.preflight import _check_budget, run_preflight
from orchestrator.settings import (
    GEMINI_TTS_AUDIO_TOKENS_PER_SECOND,
    GEMINI_TTS_INPUT_TOKEN_COST_USD,
    GEMINI_TTS_OUTPUT_TOKEN_COST_USD,
    STT_COST_PER_SECOND_USD,
    Settings,
    SettingsError,
    load_settings,
)
from orchestrator.state.run_manifest import (
    RUN_STATE_DIR,
    RunManifest,
    _atomic_write_json,
    build_failure_report,
    load_run_manifest,
    save_run_manifest,
    write_failure_report,
)
from orchestrator.tools.deterministic_tools import (
    ASSETS_FILE,
    ImpactPhraseNotFoundError,
    build_subtitle_cues,
    extract_word_timing,
    ingest,
)
from orchestrator.tools.gemini_tools import generate_narration_audio

ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
FINAL_STORY_PLAN_FILE = Path("metadata/final_story_plan.json")
VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")
MAX_ASSET_ANALYST_ATTEMPTS = 4
MAX_STORY_AGENT_ATTEMPTS = 4
MAX_VISUAL_AGENT_ATTEMPTS = 4
# AD-3: transient-failure-retry default (not the 4-ceiling generate-review-
# revise pattern) -- no creative self-correction loop exists in this stage.
MAX_VOICE_AGENT_ATTEMPTS = 3


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


async def run_visual_stage(settings: Settings, manifest: RunManifest) -> int:
    """Assign each shot's generation_mode/visual_treatment/visual direction
    from the already-validated `FinalStoryPlanContract` + `AnalyzedAssetsContract`
    -- gated on that story contract existing, since `visual_agent` has no
    Read tool and never re-derives it itself (AD-2: no independent timing
    source exists pre-audio, so shots map 1:1 onto scenes by sequence).
    """
    try:
        story_plan = FinalStoryPlanContract.model_validate_json(
            FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("visual_agent", "FinalStoryPlanContract", f"No validated final story plan available: {exc}")

    try:
        analyzed = AnalyzedAssetsContract.model_validate_json(ANALYZED_ASSETS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("visual_agent", "AnalyzedAssetsContract", f"No validated analyzed-assets contract available: {exc}")

    story_plan_bundle = story_plan.model_dump(mode="json")
    assets_bundle = [asset.model_dump(mode="json") for asset in analyzed.assets]

    async def call_agent(feedback: str | None, previous_output: object, remaining_budget: float):
        return await plan_visuals(
            story_plan_bundle, assets_bundle, max_budget_usd=remaining_budget,
            feedback=feedback, previous_output=previous_output,
        )

    def validate_contract(contract: VisualPlanContract) -> None:
        contract.validate_sources(story_plan)

    return await run_bounded_agent_stage(
        stage_id="visual_agent",
        failed_contract_name="VisualPlanContract",
        contract_cls=VisualPlanContract,
        persisted_file=VISUAL_PLAN_FILE,
        validate_contract=validate_contract,
        max_attempts=MAX_VISUAL_AGENT_ATTEMPTS,
        settings=settings,
        manifest=manifest,
        base_partial_paths=[str(FINAL_STORY_PLAN_FILE), str(ANALYZED_ASSETS_FILE)],
        attempt_file_prefix="visual_agent_attempt",
        call_agent=call_agent,
    )


def _voice_pipeline_result(
    *, subtype: str, is_error: bool, cost: float, message: str | None = None,
    structured_output: object = None, start: float,
) -> ResultMessage:
    """Build this pipeline's synthetic `ResultMessage`, shared by the
    success path and every failure path below so cost/session/timing
    bookkeeping can't drift between them.
    """
    duration_ms = int((time.monotonic() - start) * 1000)
    return ResultMessage(
        subtype=subtype,
        duration_ms=duration_ms,
        duration_api_ms=duration_ms,
        is_error=is_error,
        num_turns=1,
        session_id=f"voice_agent-{uuid.uuid4()}",
        total_cost_usd=cost,
        errors=[message] if message else None,
        result=message,
        structured_output=structured_output,
    )


async def run_voice_pipeline(
    story_plan: FinalStoryPlanContract, settings: Settings, budget_spent_usd: float,
) -> ResultMessage:
    """Run narration TTS -> word-timing STT -> subtitle-cue DP segmentation
    directly, with no Claude/Agent-SDK session anywhere (AD-1: mirrors
    `preflight`'s own no-judgment exception). Each tool's handler is called
    the same way `run_screenshot_stage` calls `ingest.handler` directly.
    Wraps the combined result in a synthetic `ResultMessage` so it reuses
    `run_bounded_agent_stage` unchanged.

    AD-8: remaining budget is checked immediately before each paid call
    (TTS, then STT), not only once per attempt via the outer loop. AD-9: a
    failure after an earlier paid step already succeeded still returns that
    step's real cost (via `total_cost_usd`) instead of letting the
    exception propagate past `run_bounded_agent_stage`'s cost-accounting
    code -- `subtype="error_max_budget_usd"` is the one hook
    `run_bounded_agent_stage` already has for "halt this attempt
    immediately, don't just retry", reused here for both an actual budget
    shortfall (AD-8) and AD-12's non-recoverable phrase-not-found halt.
    """
    start = time.monotonic()
    narration_script = story_plan.narration_script
    voice_direction = story_plan.voice_direction.model_dump(mode="json")

    budget_failure = _check_budget(settings, budget_spent_usd)
    if budget_failure:
        return _voice_pipeline_result(
            subtype="error_max_budget_usd", is_error=True, cost=0.0,
            message=budget_failure.reason or "Budget exhausted before the TTS call", start=start,
        )

    try:
        tts_response = await generate_narration_audio.handler({
            "narration_script": narration_script,
            "voice_direction": voice_direction,
            "project_id": settings.project_id,
            "location": settings.location,
        })
    except Exception as exc:
        return _voice_pipeline_result(
            subtype="error", is_error=True, cost=0.0,
            message=f"TTS failed: {type(exc).__name__}: {exc}", start=start,
        )

    tts_payload = json.loads(tts_response["content"][0]["text"])
    audio_duration_seconds = float(tts_payload["duration_seconds"])
    # Real Gemini TTS per-unit pricing -- settings.py's new cost-rate
    # constants (Code Map). Input-token cost is based on the actual prompt
    # text Gemini receives (build_tts_prompt's fixed instructional preamble
    # plus the narration), not just the bare narration_script.
    tts_cost = round(
        tts_payload["prompt_word_count"] * GEMINI_TTS_INPUT_TOKEN_COST_USD
        + audio_duration_seconds * GEMINI_TTS_AUDIO_TOKENS_PER_SECOND * GEMINI_TTS_OUTPUT_TOKEN_COST_USD,
        6,
    )

    budget_failure = _check_budget(settings, budget_spent_usd + tts_cost)
    if budget_failure:
        return _voice_pipeline_result(
            subtype="error_max_budget_usd", is_error=True, cost=tts_cost,
            message=budget_failure.reason or "Budget exhausted before the STT call", start=start,
        )

    stt_cost = 0.0
    try:
        stt_response = await extract_word_timing.handler({
            "audio_path": tts_payload["audio_path"],
            "narration_script": narration_script,
            "project_id": settings.project_id,
        })
        stt_payload = json.loads(stt_response["content"][0]["text"])
        stt_cost = round(audio_duration_seconds * STT_COST_PER_SECOND_USD, 6)

        dp_response = await build_subtitle_cues.handler({
            "word_timing": stt_payload,
            "narration_script": narration_script,
            "voice_direction": voice_direction,
            "scenes": [scene.model_dump(mode="json") for scene in story_plan.scenes],
            "viewer_reflection": story_plan.story_arc.viewer_reflection,
        })
        dp_payload = json.loads(dp_response["content"][0]["text"])
    except ImpactPhraseNotFoundError as exc:
        # AD-12: a text-authoring mismatch, not a transient/alignment
        # failure -- no amount of audio regeneration can ever fix it.
        # Forces an immediate halt (see subtype note above) instead of
        # burning the retry ceiling on something deterministically doomed
        # to fail identically every time.
        return _voice_pipeline_result(
            subtype="error_max_budget_usd", is_error=True, cost=tts_cost + stt_cost,
            message=f"Non-recoverable (AD-12): {exc}", start=start,
        )
    except Exception as exc:
        # AD-9: TTS (and, if it got this far, STT) spend already happened
        # and must still be recorded even though this attempt failed.
        return _voice_pipeline_result(
            subtype="error", is_error=True, cost=tts_cost + stt_cost,
            message=f"{type(exc).__name__}: {exc}", start=start,
        )

    structured_output = {
        "produced_by": "voice_agent",
        "source_narration_script": narration_script,
        "alignment_ratio": dp_payload["alignment_ratio"],
        "cues": [
            {
                "cue_id": cue["cue_id"],
                "start_seconds": cue["start_seconds"],
                "end_seconds": cue["end_seconds"],
                "word_count": cue["word_count"],
                "text": cue["text"],
                "words": cue["words"],
            }
            for cue in dp_payload["cues"]
        ],
    }

    return _voice_pipeline_result(
        subtype="success", is_error=False, cost=round(tts_cost + stt_cost, 6),
        structured_output=structured_output, start=start,
    )


async def run_voice_stage(settings: Settings, manifest: RunManifest) -> int:
    """Generate narration audio, word timing, and subtitle cues from the
    already-validated `FinalStoryPlanContract` -- gated on that contract and
    `VisualPlanContract` both existing (AC1's stated precondition;
    `VisualPlanContract` is only an existence gate here, never read for
    content).
    """
    try:
        story_plan = FinalStoryPlanContract.model_validate_json(
            FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("voice_agent", "FinalStoryPlanContract", f"No validated final story plan available: {exc}")

    try:
        VisualPlanContract.model_validate_json(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("voice_agent", "VisualPlanContract", f"No validated visual plan available: {exc}")

    async def call_agent(feedback: str | None, previous_output: object, remaining_budget: float) -> ResultMessage:
        return await run_voice_pipeline(story_plan, settings, manifest.budget_spent_usd)

    def validate_contract(contract: SubtitleCuesContract) -> None:
        contract.validate_sources(story_plan)

    return await run_bounded_agent_stage(
        stage_id="voice_agent",
        failed_contract_name="SubtitleCuesContract",
        contract_cls=SubtitleCuesContract,
        persisted_file=SUBTITLE_CUES_FILE,
        validate_contract=validate_contract,
        max_attempts=MAX_VOICE_AGENT_ATTEMPTS,
        settings=settings,
        manifest=manifest,
        base_partial_paths=[str(FINAL_STORY_PLAN_FILE), str(VISUAL_PLAN_FILE)],
        attempt_file_prefix="voice_agent_attempt",
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
    narration_result = asyncio.run(run_narration_stage(settings, manifest))
    if narration_result != 0:
        return narration_result
    visual_result = asyncio.run(run_visual_stage(settings, manifest))
    if visual_result != 0:
        return visual_result
    return asyncio.run(run_voice_stage(settings, manifest))


if __name__ == "__main__":
    sys.exit(main())
