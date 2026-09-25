"""Orchestrator CLI entrypoint: `python -m orchestrator.run --project <name>`.

Runs preflight, deterministic ingestion, and bounded screenshot understanding
(`run_screenshot_stage`), then -- once that stage has a validated
`AnalyzedAssetsContract` -- bounded narration writing/review/revision
(`run_narration_stage`), then -- once that stage has a validated
`FinalStoryPlanContract` -- bounded visual/shot-mode planning
(`run_visual_stage`), then -- once that stage has a validated
`VisualPlanContract` too -- bounded voice/word-timing/subtitle-cue
generation (`run_voice_stage`). Each stage's validated persisted contract is
that stage's resume checkpoint. The production step (`run_production_stage`)
then makes the VEO-mode shots through the bounded `veo_agent` and the
STILL-mode shots through the bounded `stills_agent`, side by side (Story 4.4);
every shot records into the same partial production-asset manifest via the
orchestrator's sole keyed-upsert write path.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import math
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable, Optional, Sequence

from claude_agent_sdk import ResultMessage

from orchestrator.agents._guard import (
    STILL_TOOL,
    VEO_CLIP_TOOL,
    VEO_SEED_TOOL,
    PaidToolGuard,
)
from orchestrator.agents._skills import SkillIsolationError
from orchestrator.agents.asset_analyst import analyze_assets
from orchestrator.agents.stills_agent import generate_still_asset
from orchestrator.agents.story_agent import write_story
from orchestrator.agents.story_critic import critique_story
from orchestrator.agents.visual_reviewer import review_visual_asset
from orchestrator.agents.veo_agent import generate_veo_asset
from orchestrator.agents.visual_agent import plan_visuals
from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.review import CHARACTER_LIKENESS_CODE, StoryCritiqueContract, VisualQAVerdictContract
from orchestrator.contracts.stills import StillFailureContract, StillOutcomeContract
from orchestrator.contracts.subtitle_cues import SubtitleCuesContract
from orchestrator.contracts.veo import VeoFailureContract, VeoOutcomeContract, shot_fingerprint
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator import tracing
from orchestrator.progress import banner, format_duration, heartbeat, log, start_run
from orchestrator.workspace import (
    SOURCE_IMAGES_DIR_NAME,
    ProjectError,
    enter_project,
    resolve_project,
)
from orchestrator.preflight import _check_budget, check_remotion_delivery_toolchain, run_preflight
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
    CONFIG_FILENAME,
    RESUMABLE_REASON_CODES,
    RUN_STATE_DIR,
    RunManifest,
    _atomic_write_json,
    build_failure_report,
    load_run_manifest,
    save_run_manifest,
    write_failure_report,
)
from orchestrator.state.production_assets import (
    PRODUCTION_ASSETS_FILE,
    load_production_assets,
    upsert_production_asset,
)
from orchestrator.state.budget_ledger import BudgetLedger, Reservation, run_ledger
from orchestrator.tools.deterministic_tools import (
    ASSETS_FILE,
    DEFAULT_REMOTION_RENDER_OUTPUT,
    STT_MODEL,
    ImpactPhraseNotFoundError,
    AlignmentQualityError,
    build_subtitle_cues,
    extract_word_timing,
    ingest,
    public_relative_path_for_production_asset,
    render_remotion,
    sync_remotion_assets,
    tokenize,
)
from orchestrator.tools.gemini_tools import (
    CHARACTER_PROMPT_LEVELS,
    NARRATION_WAV,
    character_reference_paths_for_asset,
    describe_ladder_outcome,
    generate_narration_audio,
    resolve_character_references,
)
from orchestrator.state.voice_cache import (
    AUDIO_CACHE_FILE, TTS_MODEL, discard_audio_cache, load_audio_cache, save_audio_cache,
    selected_voice_name,
)
from orchestrator.tools.veo_tools import select_clip_duration_seconds
from orchestrator.tools.vendor_limits import configure_vendor_limits
from orchestrator.tools.timeline_converter import build_timeline_data

ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
FINAL_STORY_PLAN_FILE = Path("metadata/final_story_plan.json")
VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")
# The project's own copy of the finished reel, kept out of shared scratch.
FINISHED_REEL_NAME = "book_reel.mp4"
TIMELINE_FILE = Path("metadata/timeline.json")
# AD-3: transient-failure-retry default (not the 4-ceiling generate-review-
# revise pattern) -- no creative self-correction loop exists in this stage.
# Story 2.4: mirrors Settings.max_veo_attempts's default ceiling for the
# sibling single-shot bounded retry loop; a plain module constant rather than
# a new Settings field, since settings.py's existing image-cost fields are
# the only stills-specific configuration this story needs (Code Map).
# How many shots per reel may become real Veo video. `visual_agent` nominates
# more than this (MIN_VIDEO_NOMINATIONS) so there are spares once the shots too
# long for a single clip are dropped.
MAX_VEO_SHOTS = 3
# Advisory only: falling short is logged loudly but never halts a run. A reel
# whose beats are all long legitimately ends up with fewer video shots.
TARGET_MIN_VEO_SHOTS = 2


# What a resumable halt tells the operator to do next (AD-6: never wait live).
RESUME_HINTS = {
    "vendor_limit": "No attempt was counted. Re-run the same command to resume once the vendor limit resets.",
    "budget_exhausted": (
        "Resumable after raising MAX_BUDGET_USD: spend accumulates across runs in the "
        "run manifest, so re-running with the same ceiling stops here again."
    ),
}


def _halt(
    stage_id: str, failed_contract_name: str, reason: str, *,
    attempt_count: int = 1, partial_artifact_paths: list[str] | None = None,
    reason_code: str = "unclassified", failed_shots: list[dict] | None = None,
) -> int:
    hint = RESUME_HINTS.get(reason_code)
    if hint:
        reason = f"{reason} -- {hint}"
    report = build_failure_report(
        stage_id=stage_id,
        failed_contract_name=failed_contract_name,
        reason=reason,
        attempt_count=attempt_count,
        partial_artifact_paths=partial_artifact_paths,
        reason_code=reason_code,
        failed_shots=failed_shots,
    )
    path = write_failure_report(report, output_dir=RUN_STATE_DIR)
    tracing.record_halt(report)

    log(f"STOPPED — {reason}", error=True)
    log(f"Details of what went wrong: {path}", error=True)

    return 1


@dataclass
class StageReview:
    """What a stage's `review_before_persist` reviewer said about one fresh attempt.

    `result` is the reviewer query's own `ResultMessage`: the stage charges its
    cost and checks it for a vendor refusal. `feedback` goes to the next
    attempt when `approved` is false; `record` is written to the run state.
    """

    result: ResultMessage
    approved: bool
    feedback: str
    record: dict


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
    finalize_before_persist: Callable[[object], None] | None = None,
    review_before_persist: Callable[[object, float], Awaitable[StageReview]] | None = None,
    review_cap_usd: float = 0.0,
) -> int:
    """Shared bounded-retry/resume/halt control flow (Story 1.2's
    `run_screenshot_stage` shape), reused by every stage in this shape:
    skip on a validated persisted contract, else retry up to `max_attempts`
    with feedback, self-correcting each attempt, halting via the shared
    failure-report path on ceiling exhaustion or budget exhaustion.
    `validate_contract` raises `ValueError` for both the resume-staleness
    check and the post-attempt quality/source-id check -- the same method
    serves both, matching each contract's own `validate_sources`.
    `finalize_before_persist`, when given, mutates a freshly validated
    contract in place immediately before it is written to `persisted_file`
    (e.g. `run_visual_stage`'s Story 2.1 shot-timing merge) -- never called
    on the resume-skip path, since that path never rewrites the file.

    `review_before_persist`, when given, is an independent reviewer (Story
    4.5, AD-18) run on a fresh attempt that has already passed
    `validate_contract` -- never inside it, since `validate_contract` also
    runs on the resume-skip path. It is called with the contract and
    `review_cap_usd`, its query's budget, which each attempt keeps back from
    the agent's own budget. Its cost is charged to the run; only an approval
    lets the contract persist, and a rejection is the next attempt's feedback.
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
        log(feedback, error=True)
    else:
        log(f"Already done in an earlier run — skipping ({persisted_file})")
        return 0

    attempt_started = time.monotonic()
    spend_before = manifest.budget_spent_usd
    count = manifest.iteration_counts.get(stage_id, 0)
    partial_paths = list(base_partial_paths)
    if persisted_file.exists():
        partial_paths.append(str(persisted_file))
    partial_paths.extend(str(p) for p in sorted(RUN_STATE_DIR.glob(f"{attempt_file_prefix}_*.json")))

    def halt(reason: str, reason_code: str = "unclassified") -> int:
        return _halt(
            stage_id, failed_contract_name, reason,
            attempt_count=count if type(count) is int else 0,
            partial_artifact_paths=partial_paths,
            reason_code=reason_code,
        )

    def vendor_halt(refusal: VendorRefusal) -> int:
        # Refused, not spent: give the pre-incremented attempt back. The
        # Claude CLI already retried any 429, so a transient one halts too.
        nonlocal count
        count -= 1
        manifest.iteration_counts[stage_id] = count
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        return halt(f"Blocked by a vendor limit ({refusal.kind}): {refusal.detail}", "vendor_limit")

    if type(count) is not int or count < 0:
        return halt(f"Invalid persisted {stage_id} iteration count")

    review_cap = review_cap_usd if review_before_persist is not None else 0.0

    async def run_review(contract) -> int | str | None:
        """None on approval, the feedback on a rejection, or the halt's exit code."""

        nonlocal count
        remaining = settings.max_budget_usd - manifest.budget_spent_usd
        if remaining + 1e-9 < review_cap:
            return halt(
                f"Try {count} passed validation, but its independent review needs ${review_cap:.4f} "
                f"and only ${remaining:.4f} of the budget is left",
                "budget_exhausted",
            )
        review_path = RUN_STATE_DIR / f"{attempt_file_prefix}_{count}_review.json"
        try:
            with heartbeat("the reviewer is still working"):
                review = await review_before_persist(contract, review_cap)
        except SkillIsolationError as exc:
            count -= 1
            manifest.iteration_counts[stage_id] = count
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            return halt(str(exc))
        except Exception as exc:
            refusal = _vendor_refusal(exc=exc)
            if refusal is not None:
                vendor_path = RUN_STATE_DIR / f"{attempt_file_prefix}_{count}_review_vendor_limit.json"
                _atomic_write_json(vendor_path, {
                    "status": "VENDOR_LIMIT", "vendor_refusal": refusal.kind,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                partial_paths.append(str(vendor_path))
                return vendor_halt(refusal)
            # Like a crashed attempt: nothing reported, nothing charged, and
            # the attempt counts. The draft is not persisted unreviewed.
            _atomic_write_json(review_path, {"status": "EXCEPTION", "error": f"{type(exc).__name__}: {exc}"})
            partial_paths.append(str(review_path))
            return f"The independent review of try {count} failed: {type(exc).__name__}: {exc}"
        cost = review.result.total_cost_usd
        if cost is None or not math.isfinite(cost) or cost < 0:
            return halt("The reviewer returned no usable cost; cannot safely enforce the run budget")
        manifest.budget_spent_usd += cost
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        refusal = _vendor_refusal(result=review.result)
        if refusal is not None:
            vendor_path = RUN_STATE_DIR / f"{attempt_file_prefix}_{count}_review_vendor_limit.json"
            _atomic_write_json(vendor_path, {
                "status": "VENDOR_LIMIT", "vendor_refusal": refusal.kind,
                "result": review.result.result, "subtype": review.result.subtype,
                "errors": review.result.errors, "api_error_status": review.result.api_error_status,
                "claude_cost_usd": cost,
            })
            partial_paths.append(str(vendor_path))
            return vendor_halt(refusal)
        _atomic_write_json(review_path, {**review.record, "approved": review.approved, "claude_cost_usd": cost})
        partial_paths.append(str(review_path))
        budget_failure = _check_budget(settings, manifest.budget_spent_usd)
        if budget_failure:
            return halt(budget_failure.reason or "Budget exhausted", "budget_exhausted")
        return None if review.approved else review.feedback

    while count < max_attempts:
        budget_failure = _check_budget(settings, manifest.budget_spent_usd)
        if budget_failure:
            return halt(budget_failure.reason or "Budget exhausted", "budget_exhausted")
        # Story 4.5: each attempt keeps one review call's cap back from the agent.
        agent_budget = settings.max_budget_usd - manifest.budget_spent_usd - review_cap
        if agent_budget <= 0:
            return halt(
                f"${settings.max_budget_usd - manifest.budget_spent_usd:.4f} of the budget is left, which "
                f"can't cover another try plus its independent review (${review_cap:.4f})",
                "budget_exhausted",
            )

        count += 1
        manifest.iteration_counts[stage_id] = count
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        log(f"Try {count} of {max_attempts} · ${settings.max_budget_usd - manifest.budget_spent_usd:.2f} left to spend")
        try:
            with heartbeat("still working"):
                result = await call_agent(feedback, previous_output, agent_budget)
        except SkillIsolationError as exc:
            # A leak of developer tooling is not the agent's defect; retrying
            # would only repeat it. No model turn ran, so the attempt goes back.
            count -= 1
            manifest.iteration_counts[stage_id] = count
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            return halt(str(exc))
        except Exception as exc:
            refusal = _vendor_refusal(exc=exc)
            if refusal is not None:
                attempt_path = RUN_STATE_DIR / f"{attempt_file_prefix}_{count}_vendor_limit.json"
                _atomic_write_json(attempt_path, {
                    "status": "VENDOR_LIMIT", "vendor_refusal": refusal.kind,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                partial_paths.append(str(attempt_path))
                return vendor_halt(refusal)
            feedback = f"Claude attempt failed: {type(exc).__name__}: {exc}"
            previous_output = None
        else:
            cost = result.total_cost_usd
            if cost is None or not math.isfinite(cost) or cost < 0:
                return halt("Claude returned no usable cost; cannot safely enforce the run budget")
            manifest.budget_spent_usd += cost
            manifest.session_id = result.session_id
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            refusal = _vendor_refusal(result=result)
            if refusal is not None:
                # Kept apart from `_{count}.json`: the attempt is given back,
                # so a resumed run writes that name again.
                attempt_path = RUN_STATE_DIR / f"{attempt_file_prefix}_{count}_vendor_limit.json"
                _atomic_write_json(attempt_path, {
                    "status": "VENDOR_LIMIT", "vendor_refusal": refusal.kind,
                    "result": result.result, "subtype": result.subtype, "errors": result.errors,
                    "api_error_status": result.api_error_status, "claude_cost_usd": cost,
                })
                partial_paths.append(str(attempt_path))
                return vendor_halt(refusal)
            previous_output = result.structured_output
            attempt_path = RUN_STATE_DIR / f"{attempt_file_prefix}_{count}.json"
            _atomic_write_json(attempt_path, {
                "structured_output": previous_output, "result": result.result,
                "subtype": result.subtype, "errors": result.errors,
            })
            partial_paths.append(str(attempt_path))
            budget_failure = _check_budget(settings, manifest.budget_spent_usd)
            if budget_failure or result.subtype == "error_max_budget_usd":
                return halt(
                    budget_failure.reason if budget_failure else "Claude reached its remaining budget limit",
                    "budget_exhausted",
                )
            try:
                if result.is_error:
                    raise ValueError(f"Claude failed ({result.subtype}): {result.errors or result.result}")
                contract = contract_cls.model_validate(previous_output)
                validate_contract(contract)
            except ValueError as exc:
                feedback = str(exc)
            else:
                rejection = None
                if review_before_persist is not None:
                    reviewed = await run_review(contract)
                    if isinstance(reviewed, int):
                        return reviewed
                    rejection = reviewed
                if rejection is not None:
                    feedback = rejection
                else:
                    if finalize_before_persist is not None:
                        finalize_before_persist(contract)
                    _atomic_write_json(persisted_file, contract.model_dump(mode="json"))
                    log(
                        f"Done in {format_duration(time.monotonic() - attempt_started)} · "
                        f"cost ${manifest.budget_spent_usd - spend_before:.2f} · saved {persisted_file}"
                    )
                    return 0

        log(f"Try {count} of {max_attempts} didn't work: {feedback}", error=True)

    return halt(
        f"Retry ceiling exhausted: {feedback or f'{max_attempts} attempts already recorded for this run'}",
        "retry_ceiling",
    )


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
        max_attempts=manifest.attempts_allowed("asset_analyst"),
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

    async def review(contract: FinalStoryPlanContract, max_budget_usd: float) -> StageReview:
        # FR11: a fresh session that sees only the draft and the analyses --
        # not the writer's own quality_review (AD-18).
        log("Having the draft checked by an independent story critic")
        with tracing.span("story_critic") as critic_span:
            result = await critique_story(
                contract.model_dump(mode="json", exclude={"quality_review"}), assets_bundle,
                max_budget_usd=max_budget_usd,
            )
            verdict = story_critique_review(result)
            critic_span.update(output={"approved": verdict.approved, "feedback": verdict.feedback})
        return verdict

    return await run_bounded_agent_stage(
        stage_id="story_agent",
        failed_contract_name="FinalStoryPlanContract",
        contract_cls=FinalStoryPlanContract,
        persisted_file=FINAL_STORY_PLAN_FILE,
        validate_contract=validate_contract,
        max_attempts=manifest.attempts_allowed("story_agent"),
        settings=settings,
        manifest=manifest,
        base_partial_paths=[str(ANALYZED_ASSETS_FILE)],
        attempt_file_prefix="story_agent_attempt",
        call_agent=call_agent,
        review_before_persist=review,
        review_cap_usd=settings.claude_reviewer_cap_usd,
    )


def story_critique_review(result: ResultMessage) -> StageReview:
    """The story critic's verdict, as the bounded stage's review.

    A critic that failed or returned an unusable verdict is not an approval:
    the draft isn't persisted and the attempt counts, like a crashed attempt.
    """

    record = {"structured_output": result.structured_output, "result": result.result,
              "subtype": result.subtype, "errors": result.errors}
    try:
        if result.is_error:
            raise ValueError(f"the critic failed ({result.subtype}): {result.errors or result.result}")
        critique = StoryCritiqueContract.model_validate(result.structured_output)
    except ValueError as exc:
        return StageReview(result, False, f"The independent story critic gave no usable verdict: {exc}", record)
    if critique.verdict == "APPROVE":
        return StageReview(result, True, "", record)
    issues = "\n".join(
        f"- {issue.dimension}: {issue.evidence} Fix: {issue.fix}" for issue in critique.issues
    )
    return StageReview(
        result, False,
        "An independent story critic asked for a revision. Address every issue:\n" + issues,
        record,
    )


def _load_previous_shot_timing(path: Path) -> dict[int, dict]:
    """Best-effort: pull just the per-shot `start_seconds`/`end_seconds`/
    `primary_subtitle_cue_ids` (Story 2.1) out of whatever JSON currently
    sits at `path`, keyed by shot `sequence`, without requiring the rest of
    the file to satisfy `VisualPlanContract` (it may predate fields this
    contract now requires, e.g. `produced_by` -- Story 2.1 Implementation
    Notes). Returns {} on any missing/unreadable/malformed file.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    timing_by_sequence: dict[int, dict] = {}
    for shot in data.get("shots", []):
        sequence = shot.get("sequence")
        if not isinstance(sequence, int) or "start_seconds" not in shot or "end_seconds" not in shot:
            continue
        timing_by_sequence[sequence] = {
            "start_seconds": shot["start_seconds"],
            "end_seconds": shot["end_seconds"],
            "primary_subtitle_cue_ids": shot.get("primary_subtitle_cue_ids", []),
            "fade_in_frames": shot.get("fade_in_frames", 0),
            "fade_out_frames": shot.get("fade_out_frames", 0),
            "still_motion": shot.get("still_motion"),
        }
    return timing_by_sequence


def _merge_backfilled_shot_timing(contract: VisualPlanContract) -> None:
    """When `visual_agent` regenerates a plan, merge per-shot timing and
    renderer fields (`fade_*`, `still_motion`) from the previously persisted
    `metadata/visual_plan.json` by sequence. Agent-emitted fades/motion on the
    fresh draft are overwritten only where the prior file had real values for
    that sequence (Story 2.1 timing backfill + Decision B motion preservation).
    """
    timing_by_sequence = _load_previous_shot_timing(VISUAL_PLAN_FILE)
    for shot in contract.shots:
        timing = timing_by_sequence.get(shot.sequence)
        if timing is None:
            continue
        shot.start_seconds = timing["start_seconds"]
        shot.end_seconds = timing["end_seconds"]
        shot.primary_subtitle_cue_ids = list(timing["primary_subtitle_cue_ids"])
        shot.fade_in_frames = timing["fade_in_frames"]
        shot.fade_out_frames = timing["fade_out_frames"]
        if timing["still_motion"] is not None:
            from orchestrator.contracts.visual_plan import StillMotion
            shot.still_motion = StillMotion.model_validate(timing["still_motion"])


def _close_inter_shot_timing_gaps(shots: Sequence) -> None:
    """Make consecutive shots contiguous by absorbing silence into the previous shot.

    Cue-based backfill often leaves a short pause between one scene's last word
    and the next scene's first word. Remotion's timeline requires shots to butt
    together with no gap/overlap. Extending shot N's end_seconds to shot N+1's
    start_seconds holds the previous still/clip through that pause. Purely
    deterministic -- no agent.
    """
    for index in range(len(shots) - 1):
        current = shots[index]
        following = shots[index + 1]
        if following.start_seconds < current.end_seconds - 1e-6:
            raise ValueError(
                f"Shot {following.sequence} overlaps shot {current.sequence}: "
                f"previous end {current.end_seconds}, this start {following.start_seconds}."
            )
        if following.start_seconds > current.end_seconds + 1e-6:
            current.end_seconds = following.start_seconds


def _backfill_visual_plan_shot_timing_from_cues(settings: Settings) -> None:
    """After `voice_agent`, assign real per-shot timing/cue linkage from
    subtitle cues (1:1 shots/scenes). Leaves shots that already carry timing,
    then closes inter-shot gaps so the plan is Remotion-contiguous, then judges
    this plan's video nominations now that real durations exist.
    """
    visual_plan = VisualPlanContract.model_validate_json(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    needs_cue_map = any(
        shot.start_seconds == 0.0 and shot.end_seconds == 0.0 for shot in visual_plan.shots
    )
    # Tracked separately from `needs_cue_map`: a regenerated plan can inherit
    # the previous file's timing (`_merge_backfilled_shot_timing`) and so need
    # no cue mapping, while still never having had its own nominations judged.
    needs_promotion = not all(shot.video_promotion_decided for shot in visual_plan.shots)
    if needs_cue_map:
        story_plan = FinalStoryPlanContract.model_validate_json(
            FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8")
        )
        subtitle_cues = SubtitleCuesContract.model_validate_json(
            SUBTITLE_CUES_FILE.read_text(encoding="utf-8")
        )
        if len(story_plan.scenes) != len(visual_plan.shots):
            raise ValueError(
                "Cannot backfill shot timing: scene count does not match shot count."
            )

        cues = subtitle_cues.cues
        cue_cursor = 0
        for scene, shot in zip(story_plan.scenes, visual_plan.shots):
            if shot.sequence != scene.sequence:
                raise ValueError(
                    f"Shot/scene sequence mismatch at {shot.sequence} vs {scene.sequence}"
                )
            if not (shot.start_seconds == 0.0 and shot.end_seconds == 0.0):
                continue

            target_words = len(tokenize(scene.narration))
            assigned: list = []
            words_covered = 0
            while cue_cursor < len(cues) and words_covered < target_words:
                cue = cues[cue_cursor]
                assigned.append(cue)
                words_covered += cue.word_count
                cue_cursor += 1
            if not assigned:
                raise ValueError(f"Shot {shot.sequence} has no subtitle cues to map")
            shot.start_seconds = assigned[0].start_seconds
            shot.end_seconds = assigned[-1].end_seconds
            shot.primary_subtitle_cue_ids = [cue.cue_id for cue in assigned]

        if cue_cursor != len(cues):
            raise ValueError(
                "Subtitle cue coverage does not match scene-aligned shots after backfill."
            )

        # Close gaps only on freshly mapped timing so already-approved still/veo
        # fingerprints (which embedded the prior end_seconds) stay resume-valid.
        _close_inter_shot_timing_gaps(visual_plan.shots)
        _atomic_write_json(VISUAL_PLAN_FILE, visual_plan.model_dump(mode="json"))

    if needs_promotion:
        # Real durations exist now, so nominations can finally be judged. Runs
        # exactly once per plan, and always before any `shot_fingerprint` is
        # taken, so it never invalidates an already-approved asset on resume.
        visual_plan = VisualPlanContract.model_validate_json(
            VISUAL_PLAN_FILE.read_text(encoding="utf-8")
        )
        log("Deciding which shots become moving video clips")
        _promote_video_candidates(visual_plan, settings)
        _atomic_write_json(VISUAL_PLAN_FILE, visual_plan.model_dump(mode="json"))


def _promote_video_candidates(visual_plan: VisualPlanContract, settings: Settings) -> list[int]:
    """Turn `visual_agent`'s ranked nominations into real VEO-mode shots.

    Purely mechanical (AD-15): it never re-ranks and never nominates, it only
    drops nominations that cannot physically be generated and caps how many are
    taken up. Runs here, immediately after cue-derived timing lands, because
    this is the first moment a shot's real spoken duration exists -- and still
    before any `shot_fingerprint` is taken, so promotion never invalidates an
    already-approved asset.
    """
    nominated = sorted(
        (shot for shot in visual_plan.shots if shot.video_candidate_rank is not None),
        key=lambda shot: shot.video_candidate_rank,
    )
    promoted: list[int] = []
    for shot in nominated:
        if len(promoted) >= MAX_VEO_SHOTS:
            log(f"Shot {shot.sequence}: stays a still picture (already have the maximum {MAX_VEO_SHOTS} video clips)", indent=2)
            continue
        duration = shot.end_seconds - shot.start_seconds
        try:
            clip_seconds = select_clip_duration_seconds(duration, settings.veo_duration_seconds)
        except ValueError as exc:
            log(f"Shot {shot.sequence}: stays a still picture ({exc})", indent=2)
            continue
        shot.generation_mode = "VEO"
        promoted.append(shot.sequence)
        log(f"Shot {shot.sequence}: will be a moving video clip ({duration:.2f}s of narration → {clip_seconds}s clip)", indent=2)

    for shot in visual_plan.shots:
        shot.video_promotion_decided = True

    if not nominated:
        log("No shots were put forward for video — every shot will be a still picture")
    elif len(promoted) < TARGET_MIN_VEO_SHOTS:
        log(
            f"Only {len(promoted)} of {len(nominated)} candidate shot(s) fit inside a "
            f"{settings.veo_duration_seconds}s clip (hoped for {TARGET_MIN_VEO_SHOTS}) — "
            "the rest stay still pictures",
            error=True,
        )
    return promoted


def shot_attempt_reservation(settings: Settings, generation_mode: str) -> tuple[float, float]:
    """(Claude share, paid-call costs) one attempt of a shot in this mode reserves.

    A Veo attempt draws a seed and a clip, a still attempt one image. The
    Claude share is the producer's cap plus one independent visual-QA review
    at its own cap (Story 4.5); the producer's query still gets only
    `claude_shot_attempt_cap_usd`.
    """

    external = settings.image_call_cost_usd
    if generation_mode == "VEO":
        external += settings.veo_call_cost_usd
    return settings.claude_shot_attempt_cap_usd + settings.claude_reviewer_cap_usd, external


def plan_first_attempt_demotions(
    pending: Sequence, settings: Settings, unreserved_usd: float, *, started: frozenset[int] = frozenset(),
) -> tuple[list[int], float]:
    """Which pending Veo shots to demote so every first attempt fits (AD-15(c)).

    Returns the demoted sequences, lowest-ranked first, and what the first
    attempts then need in all. Deterministic: the highest
    `video_candidate_rank` number goes first, an unranked Veo shot before any
    ranked one, and a tie goes to the later shot. A Veo shot in `started`
    already has Veo attempts on record and is never demoted: that would drop
    its ladder state and step around its retry ceiling.
    """

    def needed(mode: str) -> float:
        return sum(shot_attempt_reservation(settings, mode))

    total = sum(needed(shot.generation_mode) for shot in pending)
    demotable = sorted(
        (shot for shot in pending if shot.generation_mode == "VEO" and shot.sequence not in started),
        key=lambda shot: (
            shot.video_candidate_rank is not None,
            -(shot.video_candidate_rank or 0),
            -shot.sequence,
        ),
    )
    demoted: list[int] = []
    for shot in demotable:
        if total <= unreserved_usd + 1e-9:
            break
        total -= needed("VEO") - needed("STILL")
        demoted.append(shot.sequence)
    return demoted, total


async def run_production_planning(settings: Settings, manifest: RunManifest) -> int:
    """Fix every pending shot's mode against the budget before any shot starts.

    Run by the production step (`run_production_stage`, and `run_veo_stage`
    on its own) before any shot launches.

    Story 4.3: computes each pending shot's first-attempt reservation, demotes
    the lowest-ranked Veo shots to stills until they all fit (one
    `visual_plan.json` write, here and nowhere else), and halts with
    `budget_exhausted` before any paid call when even stills can't fit. Every
    shot keeps `still_motion`, so a demotion is lossless, and a pending shot
    has no production-asset entry, so no fingerprint goes stale.
    """

    try:
        visual_plan = VisualPlanContract.model_validate_json(
            VISUAL_PLAN_FILE.read_text(encoding="utf-8")
        )
        production_assets = load_production_assets(PRODUCTION_ASSETS_FILE)
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt(
            "production_planning", "ProductionInputs",
            f"A validated visual plan and production-asset manifest are required: {exc}",
            partial_artifact_paths=[str(VISUAL_PLAN_FILE), str(PRODUCTION_ASSETS_FILE)],
        )

    pending = [shot for shot in visual_plan.shots if str(shot.sequence) not in production_assets.shots]
    if not pending:
        log("Every shot is already made — nothing to reserve")
        return 0

    unreserved = run_ledger(manifest, max_budget_usd=settings.max_budget_usd).unreserved_usd()
    started = frozenset(
        shot.sequence for shot in pending
        if manifest.iteration_counts.get(f"veo_agent_shot_{shot.sequence}", 0)
    )
    demoted, needed = plan_first_attempt_demotions(pending, settings, unreserved, started=started)
    if needed > unreserved + 1e-9:
        return _halt(
            "production_planning", "Budget",
            f"The next try of the {len(pending)} remaining shot(s) needs ${needed:.4f} reserved, even with "
            "every shot not yet started as a video made a still picture, but only "
            f"${unreserved:.4f} of the budget is left",
            partial_artifact_paths=[str(VISUAL_PLAN_FILE)],
            reason_code="budget_exhausted",
        )
    if demoted:
        for shot in visual_plan.shots:
            if shot.sequence in demoted:
                shot.generation_mode = "STILL"
        _atomic_write_json(VISUAL_PLAN_FILE, visual_plan.model_dump(mode="json"))
        for sequence in demoted:
            log(
                f"Shot {sequence}: switched from video to a still picture — the budget can't also "
                "reserve its first try as a video",
                error=True,
            )
    # Checked, not held: each attempt reserves for itself when it starts.
    log(
        f"The first try of {len(pending)} shot(s) needs ${needed:.2f} of the ${unreserved:.2f} left"
    )
    return 0


def _voice_shot_timing_backfill_or_halt(settings: Settings) -> int | None:
    """Run cue→shot timing backfill; return a halt exit code on failure."""
    try:
        _backfill_visual_plan_shot_timing_from_cues(settings)
    except (ValueError, OSError) as exc:
        return _halt(
            "voice_agent",
            "VisualPlanContract",
            f"Shot timing backfill failed: {exc}",
            partial_artifact_paths=[str(SUBTITLE_CUES_FILE), str(VISUAL_PLAN_FILE)],
        )
    return None


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
        max_attempts=manifest.attempts_allowed("visual_agent"),
        settings=settings,
        manifest=manifest,
        base_partial_paths=[str(FINAL_STORY_PLAN_FILE), str(ANALYZED_ASSETS_FILE)],
        attempt_file_prefix="visual_agent_attempt",
        call_agent=call_agent,
        finalize_before_persist=_merge_backfilled_shot_timing,
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
    """Execute paid voice steps with verified audio reuse and explicit failure kinds.

    Costs use the configured rate estimates, including attempted recognition
    requests that failed. A reused audio file contributes no new TTS charge.
    """
    start = time.monotonic()
    narration_script = story_plan.narration_script
    voice_direction = story_plan.voice_direction.model_dump(mode="json")
    tts_cost = stt_cost = 0.0

    def result(subtype: str, message: str | None = None, output=None):
        return _voice_pipeline_result(
            subtype=subtype, is_error=subtype != "success",
            cost=round(tts_cost + stt_cost, 6), message=message,
            structured_output=output, start=start,
        )

    try:
        voice_name = selected_voice_name()
    except ValueError as exc:
        return result("error_configuration", str(exc))
    cached = load_audio_cache(narration_script, voice_direction)
    if cached is not None:
        tts_payload, provenance = cached["tts"], cached["provenance"]
        log(f"Reusing the narration audio from an earlier run (voice: {voice_name})")
        with tracing.span("tts", as_type="tool", metadata={
            "provider": "gemini", "model": TTS_MODEL, "voice": voice_name, "cached": True, "cost_usd": 0.0,
        }) as tts_span:
            tts_span.update(output={"tts": tts_payload, "provenance": provenance})
    else:
        budget_failure = _check_budget(settings, budget_spent_usd)
        if budget_failure:
            return result("error_max_budget_usd", budget_failure.reason)
        try:
            log(f"Part 1 of 3: reading the script out loud (voice: {voice_name})")
            with tracing.span("tts", as_type="tool", input={
                "narration_script": narration_script, "voice_direction": voice_direction,
            }, metadata={"provider": "gemini", "model": TTS_MODEL, "voice": voice_name}) as tts_span:
                tts_response = await generate_narration_audio.handler({
                    "narration_script": narration_script, "voice_direction": voice_direction,
                    "project_id": settings.project_id, "location": settings.location,
                })
                tts_payload = json.loads(tts_response["content"][0]["text"])
                tts_span.update(output=tts_payload)
                duration = float(tts_payload["duration_seconds"])
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError("TTS returned an invalid duration")
                tts_cost = round(
                    tts_payload["prompt_word_count"] * GEMINI_TTS_INPUT_TOKEN_COST_USD
                    + duration * GEMINI_TTS_AUDIO_TOKENS_PER_SECOND * GEMINI_TTS_OUTPUT_TOKEN_COST_USD, 6,
                )
                tts_span.update(metadata={"cost_usd": tts_cost})
            provenance = save_audio_cache(tts_payload, narration_script, voice_direction)
        except Exception as exc:
            return result("error_tts", f"TTS failed: {type(exc).__name__}: {exc}")

    audio_duration_seconds = float(tts_payload["duration_seconds"])
    budget_failure = _check_budget(settings, budget_spent_usd + tts_cost)
    if budget_failure:
        return result("error_max_budget_usd", budget_failure.reason)

    try:
        log("Part 2 of 3: listening back to find when each word is spoken")
        # Failed requests can still incur charges. The tool reports how much
        # audio it actually submitted, including a failed chunk when applicable.
        stt_cost = round(audio_duration_seconds * STT_COST_PER_SECOND_USD, 6)
        with tracing.span("stt", as_type="tool", input={"audio_path": tts_payload["audio_path"]},
                          metadata={"provider": "google_speech", "model": STT_MODEL}) as stt_span:
            stt_response = await extract_word_timing.handler({
                "audio_path": tts_payload["audio_path"], "narration_script": narration_script,
                "project_id": settings.project_id,
                "diagnostics_path": str(RUN_STATE_DIR / "word_timing_diagnostics.json"),
            })
            stt_payload = json.loads(stt_response["content"][0]["text"])
            # A summary, not the word list: the diagnostics file keeps that.
            stt_span.update(output={
                "alignment_stats": stt_payload.get("alignment_stats"),
                "attempted_audio_seconds": stt_payload.get("attempted_audio_seconds"),
                "word_count": len(stt_payload.get("words") or []),
            })
            stt_cost = round(stt_payload.get("attempted_audio_seconds", audio_duration_seconds) * STT_COST_PER_SECOND_USD, 6)
            stt_span.update(metadata={"cost_usd": stt_cost})
        _atomic_write_json(RUN_STATE_DIR / "word_timing_diagnostics.json", stt_payload)
        stats = stt_payload["alignment_stats"]
        trailing = stats.get("trailing_extra_words", 0)
        if trailing:
            log(
                f"The voice added {trailing} word(s) after the script ended; they fall "
                "outside the finished reel, so they are ignored",
                indent=2,
            )
        log(
            f"Words matched: {stats['exact_match_ratio']:.0%} of the script was found, "
            f"{stats.get('normalized_match_ratio', stats['exact_match_ratio']):.0%} of what was "
            "said is in the script"
        )
    except Exception as exc:
        stt_cost = round(getattr(exc, "attempted_audio_seconds", audio_duration_seconds) * STT_COST_PER_SECOND_USD, 6)
        kind = "error_alignment" if isinstance(exc, AlignmentQualityError) else "error_stt"
        return result(kind, f"STT failed: {type(exc).__name__}: {exc}")

    try:
        log("Part 3 of 3: splitting the narration into subtitles")
        with tracing.span("subtitle_cues") as cues_span:
            dp_response = await build_subtitle_cues.handler({
                "word_timing": stt_payload, "narration_script": narration_script,
                "voice_direction": voice_direction,
                "scenes": [scene.model_dump(mode="json") for scene in story_plan.scenes],
                "viewer_reflection": story_plan.story_arc.viewer_reflection,
            })
            dp_payload = json.loads(dp_response["content"][0]["text"])
            cues_span.update(output={
                "cue_count": len(dp_payload.get("cues") or []),
                "alignment_ratio": dp_payload.get("alignment_ratio"),
            })
    except Exception as exc:
        # Segmentation and quality checks are deterministic for this evidence.
        # Retrying TTS cannot repair an invalid impact phrase or segmentation.
        return result("error_alignment" if isinstance(exc, AlignmentQualityError) else "error_subtitles",
                      f"Subtitles failed: {type(exc).__name__}: {exc}")
    return result("success", output={
        "produced_by": "voice_agent", "source_narration_script": narration_script,
        "audio_provenance": provenance, "alignment_ratio": dp_payload["alignment_ratio"],
        "cues": dp_payload["cues"],
    })


def _voice_stage_skip_is_valid(story_plan: FinalStoryPlanContract) -> bool:
    """True when a persisted `SubtitleCuesContract` may skip `voice_agent`."""
    try:
        contract = SubtitleCuesContract.model_validate_json(
            SUBTITLE_CUES_FILE.read_text(encoding="utf-8")
        )
        contract.validate_sources(story_plan)
    except (FileNotFoundError, ValueError, OSError):
        return False
    return True


def archive_voice_attempts(manifest: RunManifest) -> Path:
    """Preserve the previous bounded batch before resetting only its ceiling."""
    archive = RUN_STATE_DIR / "voice_history" / str(uuid.uuid4())
    archive.mkdir(parents=True, exist_ok=False)
    _atomic_write_json(archive / CONFIG_FILENAME, manifest.to_dict())
    evidence = list(RUN_STATE_DIR.glob("voice_agent_attempt_*.json"))
    evidence += list(RUN_STATE_DIR.glob("failure_voice_agent_*.json"))
    evidence += [RUN_STATE_DIR / "word_timing_diagnostics.json", AUDIO_CACHE_FILE, NARRATION_WAV, SUBTITLE_CUES_FILE]
    for path in evidence:
        if path.is_file():
            shutil.copy2(path, archive / path.name)
    manifest.iteration_counts["voice_agent"] = 0
    save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
    log(f"Earlier voice attempts moved to {archive} — money already spent still counts")
    return archive


async def run_voice_stage(settings: Settings, manifest: RunManifest, *, retry_voice: bool = False) -> int:
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

    if retry_voice:
        archive_voice_attempts(manifest)

    if _voice_stage_skip_is_valid(story_plan):
        log(f"Voice and subtitles already done — skipping ({SUBTITLE_CUES_FILE})")
        backfill_failure = _voice_shot_timing_backfill_or_halt(settings)
        return backfill_failure if backfill_failure is not None else 0

    count = manifest.iteration_counts.get("voice_agent", 0)
    partial_paths = [str(FINAL_STORY_PLAN_FILE), str(VISUAL_PLAN_FILE), str(NARRATION_WAV),
                     str(AUDIO_CACHE_FILE), str(RUN_STATE_DIR / "word_timing_diagnostics.json")]
    partial_paths.extend(str(p) for p in sorted(RUN_STATE_DIR.glob("voice_agent_attempt_*.json")))

    def halt(reason):
        return _halt("voice_agent", "SubtitleCuesContract", reason,
                     attempt_count=count if type(count) is int else 0, partial_artifact_paths=partial_paths)

    if type(count) is not int or count < 0:
        return halt("Invalid persisted voice_agent iteration count")
    feedback = "Attempts already recorded; use --retry-voice to archive evidence and reset only the voice ceiling"
    max_voice_attempts = manifest.attempts_allowed("voice_agent")
    while count < max_voice_attempts:
        budget_failure = _check_budget(settings, manifest.budget_spent_usd)
        if budget_failure:
            return halt(budget_failure.reason)
        count += 1
        manifest.iteration_counts["voice_agent"] = count
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        result = await run_voice_pipeline(story_plan, settings, manifest.budget_spent_usd)
        if result.total_cost_usd is None or not math.isfinite(result.total_cost_usd) or result.total_cost_usd < 0:
            return halt("Voice pipeline returned an unusable cost estimate")
        manifest.budget_spent_usd += result.total_cost_usd
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        attempt_path = RUN_STATE_DIR / f"voice_agent_attempt_{count}.json"
        _atomic_write_json(attempt_path, {
            "structured_output": result.structured_output, "result": result.result,
            "subtype": result.subtype, "errors": result.errors,
            "estimated_cost_usd": result.total_cost_usd,
        })
        partial_paths.append(str(attempt_path))
        diagnostics = RUN_STATE_DIR / "word_timing_diagnostics.json"
        if diagnostics.exists():
            evidence_path = RUN_STATE_DIR / f"voice_agent_attempt_{count}_alignment.json"
            shutil.copy2(diagnostics, evidence_path)
            partial_paths.append(str(evidence_path))
        budget_failure = _check_budget(settings, manifest.budget_spent_usd)
        if budget_failure:
            return halt(budget_failure.reason)
        if result.is_error:
            feedback = result.result or result.subtype
            if result.subtype == "error_alignment":
                # The recording itself disagrees with the script -- Gemini TTS
                # does occasionally add or drop a line. Only a new take can
                # change this evidence, and the cached one would otherwise be
                # re-transcribed to the identical failure until the ceiling is
                # spent. Trailing extra speech no longer reaches here at all;
                # `align_words` scores it as harmless.
                discarded = discard_audio_cache()
                if discarded:
                    log(
                        "The recording did not match the script — discarding it so the "
                        "next try records a fresh one",
                        indent=2,
                    )
            elif result.subtype not in {"error_tts", "error_stt"}:
                return halt(feedback)
        else:
            try:
                contract = SubtitleCuesContract.model_validate(result.structured_output)
                contract.validate_sources(story_plan)
            except ValueError as exc:
                return halt(f"Subtitle contract failed: {exc}")
            _atomic_write_json(SUBTITLE_CUES_FILE, contract.model_dump(mode="json"))
            log(f"Voice and subtitles done · saved {SUBTITLE_CUES_FILE}")
            backfill_failure = _voice_shot_timing_backfill_or_halt(settings)
            return backfill_failure if backfill_failure is not None else 0
        log(f"Try {count} of {max_voice_attempts} didn't work: {feedback}", error=True)
    return halt(f"Retry ceiling exhausted: {feedback}")


# Vendor refusals are not reel problems (Story 4.2, FR10; supersedes
# docs/vendor-limit-fix.md). A refusal means the work never ran, so it must not
# consume an attempt or draw the conservative charge. `transient` is worth a
# short backoff; `hard` will not clear before a reset or a human fix, so the run
# halts resumably. A hard marker wins over a 429, because usage and session
# limits arrive as 429s too. Keep the markers tight -- never a bare "limit".
VENDOR_HARD_MARKERS = (
    "usage limit",    # Codex / ChatGPT ("usage_limit_reached" too, once "_" -> " ")
    "session limit",  # Claude
    "spend limit",    # Claude org "monthly spend limit", "service_spend_limit_reached"
    "quota",
    "credentials",
    "unauthorized",
)
VENDOR_TRANSIENT_MARKERS = ("rate limit",)
VENDOR_HARD_HTTP_STATUSES = frozenset({401, 403})
VENDOR_TRANSIENT_HTTP_STATUSES = frozenset({429})
# google-genai errors read "<code> <STATUS>. {...}", e.g. "429 RESOURCE_EXHAUSTED".
_GENAI_STATUS = re.compile(r"\b([45]\d\d) [A-Z][A-Z_]+\b")


def classify_vendor_refusal(text: str | None, http_status: int | None = None) -> str:
    """`transient`, `hard` or `none` for one error's text and optional HTTP status."""

    lowered = (text or "").lower().replace("_", " ")
    if http_status in VENDOR_HARD_HTTP_STATUSES or any(m in lowered for m in VENDOR_HARD_MARKERS):
        return "hard"
    if http_status in VENDOR_TRANSIENT_HTTP_STATUSES or any(m in lowered for m in VENDOR_TRANSIENT_MARKERS):
        return "transient"
    return "none"


# At most 3 retries of the same attempt, about 2 minutes of waiting in all.
TRANSIENT_BACKOFF_SECONDS = (10.0, 30.0, 60.0)


@dataclass(frozen=True)
class VendorRefusal:
    kind: str  # "transient" | "hard"
    detail: str
    # The client already backed off before this reached us, so retrying again
    # would double it: the Claude CLI retries 429/5xx itself, and the Gemini
    # image path has QUOTA_RETRY_SECONDS. Veo (google-genai with no
    # retry_options) and Codex (429 retries off for ChatGPT auth) do not.
    retried_upstream: bool


def _vendor_refusal(
    *, exc: BaseException | None = None, result: ResultMessage | None = None,
    guard: PaidToolGuard | None = None,
) -> VendorRefusal | None:
    """The vendor refusal behind this attempt, from any of FR10's four routes.

    Routes: a Claude SDK exception; a `ResultMessage` with `is_error`; a paid
    tool's failure contract, which the guard's PostToolUse hook recorded; and
    Veo's SDK_ERROR, which is one such contract. A hard refusal wins.
    """

    found: list[VendorRefusal] = []
    if exc is not None:
        text = f"{type(exc).__name__}: {exc}"
        kind = classify_vendor_refusal(text)
        if kind != "none":
            found.append(VendorRefusal(kind, f"Claude: {text}", True))
    if result is not None and result.is_error:
        text = " ".join([result.result or "", *(result.errors or [])]).strip()
        kind = classify_vendor_refusal(text, result.api_error_status)
        if kind != "none":
            status = f", HTTP {result.api_error_status}" if result.api_error_status else ""
            found.append(VendorRefusal(kind, f"Claude ({result.subtype}{status}): {text}", True))
    for failure in guard.failures if guard is not None else []:
        text = f"{failure.code}: {failure.reason}"
        match = _GENAI_STATUS.search(failure.reason)
        kind = classify_vendor_refusal(text, int(match.group(1)) if match else None)
        if kind != "none":
            found.append(VendorRefusal(
                kind, f"{failure.tool_name} ({failure.provider}) {text}", failure.provider == "gemini",
            ))
    return next((r for r in found if r.kind == "hard"), found[0] if found else None)


# Veo refuses a seed whose person reads as a real, identifiable individual.
# Two of these are explicit safety codes; an OPERATION_ERROR only counts when
# the message says the input image itself broke the guidelines, since that
# code also covers ordinary backend faults that a redraw cannot fix.
LIKENESS_REJECTION_CODES = {"RAI_FILTERED", "UNSAFE_SEED"}


def _is_likeness_rejection(failure) -> bool:
    """True when Veo rejected the shot over the person depicted in the seed."""

    if failure.code in LIKENESS_REJECTION_CODES:
        return True
    if failure.code != "OPERATION_ERROR":
        return False
    error_text = json.dumps(failure.operation_error, default=str).lower()
    return "usage guidelines" in error_text or "violates" in error_text


# A seed-QA rejection is the QA agent's own words, so its code and reason are
# free text rather than a fixed enum. The ladder may only descend when the
# complaint is about the person: a seed rejected for visible UI, stray text or
# a duplicated subject is a different defect, and asking for less of the
# character would not fix it while quietly costing the character.
SEED_QA_CHARACTER_MARKERS = ("face", "character", "recognis", "recogniz", "profile", "likeness")


def _is_character_qa_rejection(failure) -> bool:
    """True when this repo's own seed QA rejected the seed over the person."""

    if getattr(failure, "stage", "") != "seed_qa":
        return False
    text = f"{getattr(failure, 'code', '')} {getattr(failure, 'reason', '')}".lower()
    return any(marker in text for marker in SEED_QA_CHARACTER_MARKERS)


def _forces_simpler_character_wording(failure) -> bool:
    """True when the next attempt should ask for less of the character.

    Two different judges can reject a seed over the person in it: Veo's
    likeness filter, and this repo's own seed-QA agent. They mean the same
    thing for the prompt -- the current wording is not working -- so both must
    move the ladder. Handling only Veo cost a live run three identical
    attempts: the source drew the man in profile, the level-0 wording demanded
    a face "clearly visible... never turned away", the image model kept the
    source pose, and QA rejected the same contradiction three times over.
    """

    return _is_likeness_rejection(failure) or _is_character_qa_rejection(failure)


def _prior_character_rejections(sequence: int) -> int:
    """How many already-recorded attempts for this shot were refused over the person.

    A resumed run must not start back at a wording already refused -- that
    redraws a known-rejected face and spends an attempt to learn nothing. The
    persisted attempt records are the state; nothing new has to be stored.
    """

    worst_refused = None
    for path in sorted(RUN_STATE_DIR.glob(f"veo_agent_shot_{sequence}_attempt_*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        failure = (record.get("structured_output") or {}).get("failure")
        if not failure:
            continue
        if not _forces_simpler_character_wording(
            SimpleNamespace(
                code=str(failure.get("code", "")),
                reason=str(failure.get("reason", "")),
                stage=str(failure.get("stage", "")),
                operation_error=failure.get("operation_error"),
            )
        ):
            continue
        # Records written before the rung was tracked all came from level 0 --
        # that is what the code did at the time.
        level = int(record.get("seed_prompt_level", 0) or 0)
        worst_refused = level if worst_refused is None else max(worst_refused, level)

    return 0 if worst_refused is None else worst_refused + 1


def _veo_settings_payload(settings: Settings) -> dict:
    return {
        "project_id": settings.project_id,
        "location": settings.location,
        "image_model": settings.image_model,
        "image_provider": settings.image_provider,
        "codex_timeout_seconds": settings.codex_image_timeout_seconds,
        "veo_model": settings.veo_model,
        "gcs_output_uri": settings.gcs_bucket_uri,
        "resolution": settings.veo_resolution,
        "duration_seconds": settings.veo_duration_seconds,
        "poll_seconds": settings.veo_poll_seconds,
        "max_poll_seconds": settings.veo_max_poll_seconds,
        "image_call_cost_usd": settings.image_call_cost_usd,
        "veo_call_cost_usd": settings.veo_call_cost_usd,
    }


def _stamp_still_outcome(
    outcome: StillOutcomeContract,
    *,
    shot_sequence: int,
    expected_fingerprint: str,
    paid_call_cost_usd: float,
) -> StillOutcomeContract:
    """Replace agent-copied provenance with the orchestrator's authoritative values.

    Sequence is the hard binding to the requested shot. Fingerprints and costs
    are tool/provenance metadata that Claude often retypes incorrectly into
    structured output; trusting them as a gate discards otherwise-valid paid
    stills. One shot in a live run reported $0.1290315 for a call the tool had
    reported as free -- roughly its own Claude session cost -- and halted the
    whole reel, while two other shots in the same run copied it correctly.

    The orchestrator already knows what an attempt costs: it reserved that
    amount before allowing the call. `cost_usd` on the outcome is a computed
    property, so the leaf fields are what get stamped.
    """

    if outcome.shot_sequence != shot_sequence:
        raise ValueError("Stills agent outcome does not match the requested source shot")
    updates: dict = {"shot_fingerprint": expected_fingerprint}
    if outcome.result is not None:
        updates["result"] = outcome.result.model_copy(
            update={"shot_fingerprint": expected_fingerprint, "cost_usd": paid_call_cost_usd}
        )
    if outcome.failure is not None:
        updates["failure"] = outcome.failure.model_copy(
            update={"shot_fingerprint": expected_fingerprint, "cost_usd": paid_call_cost_usd}
        )
    return outcome.model_copy(update=updates)


# A Veo attempt that never got past its seed did not pay for a clip. Any other
# outcome is charged for both calls, which over-charges an RAI-filtered clip
# Google does not bill -- the same conservative direction the STT accounting
# already takes.
VEO_SEED_ONLY_FAILURE_STAGES = {"seed_generation", "seed_qa"}


def _veo_attempt_cost(outcome: VeoOutcomeContract, settings: Settings) -> tuple[float, float]:
    """(seed cost, clip cost) the orchestrator knows this attempt incurred."""

    if (
        outcome.status == "FAILURE"
        and outcome.failure is not None
        and outcome.failure.stage in VEO_SEED_ONLY_FAILURE_STAGES
    ):
        return settings.image_call_cost_usd, 0.0
    return settings.image_call_cost_usd, settings.veo_call_cost_usd


def _stamp_veo_outcome(
    outcome: VeoOutcomeContract,
    *,
    shot_sequence: int,
    expected_fingerprint: str,
    seed_cost_usd: float,
    clip_cost_usd: float,
) -> VeoOutcomeContract:
    """Same authoritative stamp as stills; also rewrites nested seed fields.

    A Veo outcome's `cost_usd` property sums the seed and clip costs, so both
    leaves are stamped rather than the total.
    """

    if outcome.shot_sequence != shot_sequence:
        raise ValueError("Veo agent outcome does not match the requested source shot")
    updates: dict = {"shot_fingerprint": expected_fingerprint}
    if outcome.result is not None:
        seed = outcome.result.seed.model_copy(
            update={"shot_fingerprint": expected_fingerprint, "cost_usd": seed_cost_usd}
        )
        updates["result"] = outcome.result.model_copy(
            update={
                "shot_fingerprint": expected_fingerprint,
                "seed": seed,
                "cost_usd": clip_cost_usd,
            }
        )
    if outcome.failure is not None:
        updates["failure"] = outcome.failure.model_copy(
            update={
                "shot_fingerprint": expected_fingerprint,
                "cost_usd": seed_cost_usd + clip_cost_usd,
            }
        )
    return outcome.model_copy(update=updates)


def _charge_guarded_attempt(
    *, agent: str, sequence: int, count: int, status: str, suffix: str,
    claude_cost: float, guard: PaidToolGuard, ledger: BudgetLedger, reservation: Reservation,
    attempt_paths: list[str], agent_result: ResultMessage | None = None,
    exc: BaseException | None = None, detail: str = "",
) -> None:
    """Charge only what an interrupted attempt really cost, and record it.

    That is the Claude cost reported plus every paid call the guard saw bill;
    no conservative charge. The record gets its own suffix and no
    `structured_output`, so the ladder (`_prior_character_rejections`) and a
    resumed attempt's own `_{count}.json` never collide with it.
    """

    attempt_path = RUN_STATE_DIR / f"{agent}_shot_{sequence}_attempt_{count}_{suffix}.json"
    record: dict = {
        "status": status,
        "detail": detail,
        "claude_cost_usd": claude_cost,
        "charged_paid_tool_cost_usd": guard.billed_usd,
    }
    if agent_result is not None:
        record.update({
            "agent_output": agent_result.structured_output,
            "result": agent_result.result,
            "subtype": agent_result.subtype,
            "errors": agent_result.errors,
            "api_error_status": agent_result.api_error_status,
        })
    if exc is not None:
        record["error"] = f"{type(exc).__name__}: {exc}"
    _atomic_write_json(attempt_path, record)
    attempt_paths.append(str(attempt_path))
    # Debited last: the ledger saves the manifest, attempt counter included.
    ledger.debit(reservation, claude_cost + guard.billed_usd)


def _reports_success(agent_result: ResultMessage) -> bool:
    output = agent_result.structured_output
    return not agent_result.is_error and isinstance(output, dict) and output.get("status") == "SUCCESS"


async def _handle_shot_vendor_refusal(
    refusal: VendorRefusal, *, agent: str, contract_name: str, stage_id: str,
    sequence: int, count: int, transient_retries: int, claude_cost: float,
    guard: PaidToolGuard, manifest: RunManifest, ledger: BudgetLedger, reservation: Reservation,
    release: Callable[[Reservation], None],
    attempt_paths: list[str], agent_result: ResultMessage | None = None,
    exc: BaseException | None = None,
) -> ShotFailure | None:
    """Give the attempt back, then either back off (None: rerun it) or fail the shot.

    The caller releases the reservation; a rerun reserves afresh. A vendor
    limit is systemic (FR8), so it stops new shot launches.

    Only a transient refusal on a route with no retry of its own is rerun, at
    most `len(TRANSIENT_BACKOFF_SECONDS)` times. A rerun starts a new agent
    session, so it pays again for whatever it redraws.
    """

    retry = (
        refusal.kind == "transient"
        and not refusal.retried_upstream
        and transient_retries < len(TRANSIENT_BACKOFF_SECONDS)
    )
    manifest.iteration_counts[stage_id] = count - 1
    _charge_guarded_attempt(
        agent=agent, sequence=sequence, count=count,
        status="TRANSIENT_VENDOR_REFUSAL" if retry else "VENDOR_LIMIT",
        suffix=f"transient_{transient_retries + 1}" if retry else "vendor_limit",
        claude_cost=claude_cost, guard=guard, ledger=ledger, reservation=reservation,
        attempt_paths=attempt_paths, agent_result=agent_result, exc=exc,
        detail=f"{refusal.kind}: {refusal.detail}",
    )
    if retry:
        # Nothing of it is held while waiting; the rerun reserves afresh.
        release(reservation)
        delay = TRANSIENT_BACKOFF_SECONDS[transient_retries]
        log(
            f"Shot {sequence}: the vendor asked us to slow down ({refusal.detail}); "
            f"retrying the same attempt in {delay:.0f}s",
            error=True, indent=2,
        )
        await asyncio.sleep(delay)
        return None
    escalated = " (still failing after backoff)" if refusal.kind == "transient" else ""
    return ShotFailure(
        sequence, agent, contract_name,
        f"Blocked by a vendor limit ({refusal.kind}{escalated}) on shot {sequence}: {refusal.detail}",
        attempt_count=count - 1,
        partial_artifact_paths=attempt_paths,
        reason_code="vendor_limit",
    )


@dataclass
class ShotFailure:
    """Why one shot stopped. The production step turns these into one report."""

    sequence: int
    stage_id: str  # the producing agent: veo_agent | stills_agent
    failed_contract_name: str
    reason: str
    attempt_count: int = 1
    partial_artifact_paths: list[str] = field(default_factory=list)
    reason_code: str = "unclassified"
    # FR8: a retry-ceiling exhaustion or a non-retryable failure contract
    # (every ladder rung refused, a seed defect) is the shot's own, and its
    # siblings carry on. Anything else stops new launches: vendor limits, budget,
    # a skill leak, a crash, an unaccountable result. Unknown means stop.
    shot_specific: bool = False
    failure_stage: str | None = None

    def report_entry(self) -> dict:
        return {
            "sequence": self.sequence,
            "stage_id": self.stage_id,
            "scope": "shot" if self.shot_specific else "systemic",
            "failure_stage": self.failure_stage,
            "failed_contract_name": self.failed_contract_name,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "resumable": self.reason_code in RESUMABLE_REASON_CODES,
            "attempt_count": self.attempt_count,
            "partial_artifact_paths": list(self.partial_artifact_paths),
        }


class _ShotLaunch:
    """What the concurrent shots of one production step share (Story 4.4).

    Only touched from the event loop, so the asyncio primitives are safe.
    """

    def __init__(self, ledger: BudgetLedger):
        self.ledger = ledger
        # The first systemic failure. Once set, no shot launches and no
        # running shot starts another attempt; attempts in flight finish.
        self.stopped_by: ShotFailure | None = None
        self._changed = asyncio.Event()

    def _wake(self) -> None:
        event, self._changed = self._changed, asyncio.Event()
        event.set()

    def stop(self, failure: ShotFailure) -> None:
        if self.stopped_by is None:
            self.stopped_by = failure
        self._wake()

    def release(self, reservation: Reservation) -> None:
        self.ledger.release(reservation)
        self._wake()

    async def reserve(self, *, label: str, claude_cap_usd: float, external_usd: float) -> Reservation | None:
        """Reserve, waiting while sibling attempts hold reservations.

        Siblings hold their worst case and mostly give it back, so a refusal
        only counts once nothing else is held -- the same condition as when
        shots ran one at a time. A waiting shot holds nothing, so two can't
        wait on each other. Returns None when it can't reserve, or on a stop.
        """

        while self.stopped_by is None:
            reservation = self.ledger.reserve(
                label=label, claude_cap_usd=claude_cap_usd, external_usd=external_usd,
            )
            if reservation is not None or not self.ledger.holding():
                return reservation
            await self._changed.wait()
        return None

    async def overdrawn(self, reservation: Reservation) -> bool:
        """Whether this attempt's overshoot took the pool below zero (FR7).

        Judged like a refused reservation: while sibling attempts still hold
        unspent reservations, the pool is waited on rather than read, so a
        sibling's worst case never turns an overshoot the budget can cover
        into a halt that discards this attempt's paid asset.
        """

        while (
            self.ledger.overdrawn(besides=reservation) and self.stopped_by is None
            and self.ledger.holding(besides=reservation)
        ):
            await self._changed.wait()
        return self.ledger.overdrawn(besides=reservation)


def _halt_for_shot_failures(
    failures: list[ShotFailure], unfinished: list[int], stopped_by: ShotFailure | None,
) -> int:
    """One AD-6 report for every shot that failed in this production step.

    `stopped_by` is the systemic failure that stopped launches, if any; it
    sets the envelope's `reason_code` and resume hint.
    """

    failures = sorted(failures, key=lambda failure: failure.sequence)
    entries = [failure.report_entry() for failure in failures]
    note = (
        f" (shot(s) {', '.join(map(str, unfinished))} stopped before finishing; a re-run makes them)"
        if unfinished else ""
    )
    if len(failures) == 1:
        [failure] = failures
        return _halt(
            failure.stage_id, failure.failed_contract_name, failure.reason + note,
            attempt_count=failure.attempt_count,
            partial_artifact_paths=failure.partial_artifact_paths,
            reason_code=failure.reason_code,
            failed_shots=entries,
        )
    lead = stopped_by or failures[0]
    summary = "; ".join(
        f"shot {failure.sequence} ({failure.stage_id}, {failure.reason_code}): {failure.reason}"
        for failure in failures
    )
    own = [failure.sequence for failure in failures if failure.shot_specific]
    if stopped_by is not None and own:
        # The resume hint below is the systemic failure's; these shots won't
        # come back on a re-run by themselves.
        note += (
            f" (shot(s) {', '.join(map(str, own))} failed on their own and need their cause fixed, "
            "or a higher ceiling in config.json, before a re-run can make them)"
        )
    return _halt(
        "production", "ProductionAssetsContract", f"{len(failures)} shots failed: {summary}{note}",
        attempt_count=sum(failure.attempt_count for failure in failures),
        partial_artifact_paths=[path for failure in failures for path in failure.partial_artifact_paths],
        reason_code=lead.reason_code,
        failed_shots=entries,
    )


async def run_production_stage(settings: Settings, manifest: RunManifest) -> int:
    """Make every pending shot, Veo and still side by side (Story 4.4, AD-17).

    Production planning first fixes each shot's mode against the budget
    (Story 4.3); then every pending shot launches as its own task, under the
    shot-level cap and the per-vendor caps inside the paid tools.
    """

    return await _run_shot_production(settings, manifest, modes=("VEO", "STILL"), plan=True)


async def run_veo_stage(settings: Settings, manifest: RunManifest) -> int:
    """Only the VEO-mode shots, planned first as in Story 4.3. For running or
    testing one mode alone; `run_stages` uses `run_production_stage`."""

    return await _run_shot_production(settings, manifest, modes=("VEO",), plan=True)


async def run_stills_stage(settings: Settings, manifest: RunManifest) -> int:
    """Only the STILL-mode shots, reading the plan as it stands."""

    return await _run_shot_production(settings, manifest, modes=("STILL",), plan=False)


async def _run_shot_production(
    settings: Settings, manifest: RunManifest, *, modes: tuple[str, ...], plan: bool,
) -> int:
    if plan:
        planning_failure = await run_production_planning(settings, manifest)
        if planning_failure:
            return planning_failure

    try:
        visual_plan = VisualPlanContract.model_validate_json(
            VISUAL_PLAN_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("production", "VisualPlanContract", f"No validated visual plan available: {exc}")

    shots = [shot for shot in visual_plan.shots if shot.generation_mode in modes]
    if not shots:
        log("This reel needs no shots of this kind — skipping")
        return 0

    try:
        production_assets = load_production_assets(PRODUCTION_ASSETS_FILE)
    except (ValueError, OSError) as exc:
        return _halt(
            "production",
            "ProductionAssetsContract",
            f"Existing production asset manifest is invalid; refusing to reset it: {exc}",
            partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE)],
        )

    shots_by_sequence = {shot.sequence: shot for shot in visual_plan.shots}
    for key, entry in production_assets.shots.items():
        current_shot = shots_by_sequence.get(entry.shot_sequence)
        if current_shot is None:
            return _halt(
                "production",
                "ProductionAssetsContract",
                f"Production asset {key} references a shot absent from the current visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE)],
            )
        if entry.shot_fingerprint != shot_fingerprint(current_shot):
            return _halt(
                "production",
                "ProductionAssetsContract",
                f"Production asset for shot {entry.shot_sequence} is stale for the current visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE), entry.local_path],
            )
        if entry.generation_mode != current_shot.generation_mode:
            return _halt(
                "production",
                "ProductionAssetsContract",
                f"Production asset mode for shot {entry.shot_sequence} no longer matches the visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE), entry.local_path],
            )

    # AD-14: a persisted shot is never made again.
    pending = [shot for shot in shots if str(shot.sequence) not in production_assets.shots]
    if not pending:
        log(f"All {len(shots)} shot(s) already made — skipping")
        return 0

    try:
        analyzed = AnalyzedAssetsContract.model_validate_json(
            ANALYZED_ASSETS_FILE.read_text(encoding="utf-8")
        )
        subtitle_cues = SubtitleCuesContract.model_validate_json(
            SUBTITLE_CUES_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt(
            "production",
            "ProductionStageInputs",
            f"Validated analyzed assets and subtitle cues are required: {exc}",
        )

    assets_by_id = {asset.asset_id: asset for asset in analyzed.assets}
    cues_by_id = {cue.cue_id: cue.text for cue in subtitle_cues.cues}
    # Veo clips take longest, so they go first; each mode in sequence order.
    pending.sort(key=lambda shot: (shot.generation_mode != "VEO", shot.sequence))
    jobs = []
    for shot in pending:
        asset = next((assets_by_id[asset_id] for asset_id in shot.source_asset_ids if asset_id in assets_by_id), None)
        if asset is None:
            # Checked for every shot before any launches, so nothing is spent.
            return _halt(
                "production",
                "AnalyzedAssetsContract",
                f"Shot {shot.sequence} has no analyzed source asset",
            )
        subtitle_text = " ".join(
            cues_by_id[cue_id] for cue_id in shot.primary_subtitle_cue_ids if cue_id in cues_by_id
        ).strip()
        jobs.append((shot, asset, subtitle_text))

    launch = _ShotLaunch(run_ledger(manifest, max_budget_usd=settings.max_budget_usd))
    slots = asyncio.Semaphore(settings.shot_concurrency)
    log(f"Making {len(jobs)} shot(s), up to {settings.shot_concurrency} at a time")

    async def make(shot, asset, subtitle_text: str) -> ShotFailure | str:
        video = shot.generation_mode == "VEO"
        agent = "veo_agent" if video else "stills_agent"
        async with slots:
            if launch.stopped_by is not None:
                return "stopped"
            log(f"Shot {shot.sequence}: making a {'video clip' if video else 'still picture'}")
            producer = _produce_veo_shot if video else _produce_still_shot
            try:
                # Opened inside this shot's own task, so its queries and the
                # tool calls they make nest here even with shots side by side.
                with tracing.span(
                    f"shot_{shot.sequence:02d}",
                    metadata={"shot_sequence": shot.sequence, "generation_mode": shot.generation_mode, "agent": agent},
                ) as shot_span:
                    outcome = await producer(
                        shot, asset=asset, subtitle_text=subtitle_text,
                        settings=settings, manifest=manifest, launch=launch,
                    )
                    if isinstance(outcome, ShotFailure):
                        shot_span.update(output=outcome.report_entry(), level="ERROR", status_message=outcome.reason)
                    else:
                        shot_span.update(output={"outcome": outcome})
            except Exception as exc:
                # A bug, not a vendor or budget fact. Kept as this shot's
                # failure so its siblings are never torn down with it.
                outcome = ShotFailure(
                    shot.sequence, agent, "VeoOutcomeContract" if video else "StillOutcomeContract",
                    f"Shot production crashed: {type(exc).__name__}: {exc}",
                    attempt_count=manifest.iteration_counts.get(f"{agent}_shot_{shot.sequence}", 0),
                    partial_artifact_paths=[
                        str(path)
                        for path in sorted(RUN_STATE_DIR.glob(f"{agent}_shot_{shot.sequence}_attempt_*.json"))
                    ],
                )
            if isinstance(outcome, ShotFailure):
                if outcome.shot_specific:
                    log(f"Shot {shot.sequence} failed ({outcome.reason}) — the other shots carry on", error=True)
                else:
                    log(
                        f"Shot {shot.sequence} stopped the run ({outcome.reason_code}: {outcome.reason}) — "
                        "no new shots or tries will start; the ones running will finish",
                        error=True,
                    )
                    launch.stop(outcome)
            return outcome

    results = await asyncio.gather(*(make(*job) for job in jobs))
    failures = [result for result in results if isinstance(result, ShotFailure)]
    if not failures:
        return 0
    unfinished = sorted(shot.sequence for (shot, _, _), result in zip(jobs, results) if result == "stopped")
    return _halt_for_shot_failures(failures, unfinished, launch.stopped_by)


def _reviewer_character_context(asset, guard: PaidToolGuard, start_level: int) -> tuple[list[str], str]:
    """The character references and ladder note the reviewer judges likeness by.

    The rung is the one the image tool reports it really drew at (its note
    comes back in the tool result, which the guard kept), falling back to
    the rung the attempt started on. At the no-character rung the reviewer
    gets no reference, so it can't reject the source's own figure.
    """

    references = [str(path) for path in character_reference_paths_for_asset(asset.model_dump(mode="json"))]
    if not references:
        return [], ""
    notes = {describe_ladder_outcome({"level": level}): level for level in range(CHARACTER_PROMPT_LEVELS + 1)}
    level = next((notes[text] for text in reversed(guard.image_notes) if text in notes), start_level)
    level = min(max(level, 0), CHARACTER_PROMPT_LEVELS)
    note = describe_ladder_outcome({"level": level})
    return ([] if level >= CHARACTER_PROMPT_LEVELS else references), note


# The QA stages a reviewer may reject each kind of shot at.
REVIEW_STAGES = {"veo_agent": {"seed_qa", "clip_qa"}, "stills_agent": {"still_qa"}}


def _reviewer_failure_code(code: str) -> str:
    """The reviewer's free-text code, with any likeness rejection pinned to the
    one code the likeness ladder recognises (`SEED_QA_CHARACTER_MARKERS`)."""

    code = code.strip()
    if any(marker in code.upper() for marker in ("LIKENESS", "CHARACTER", "FACE")):
        return CHARACTER_LIKENESS_CODE
    return code


def _reviewer_rejection(outcome, verdict: VisualQAVerdictContract, *, agent: str, count: int, images: list[str]):
    """The producer's approved outcome, turned into the reviewer's QA failure.

    Written as the attempt's `structured_output`, so resume, the retry
    ceiling and the likeness ladder read it like a producer QA failure.
    """

    what = {"seed_qa": "seed", "clip_qa": "clip", "still_qa": "still"}[verdict.failure_stage]
    reasons = "; ".join(reason.strip() for reason in verdict.reasons if reason.strip())
    fields = dict(
        produced_by="orchestrator",
        shot_sequence=outcome.shot_sequence,
        shot_fingerprint=outcome.shot_fingerprint,
        stage=verdict.failure_stage,
        code=_reviewer_failure_code(verdict.failure_code),
        reason=f"Independent visual QA rejected the {what}: {reasons} ({verdict.summary})",
        retryable=True,
        attempt=count,
        partial_artifact_paths=list(images),
        cost_usd=outcome.cost_usd,
    )
    if agent == "veo_agent":
        return VeoOutcomeContract(
            produced_by=outcome.produced_by, status="FAILURE", shot_sequence=outcome.shot_sequence,
            shot_fingerprint=outcome.shot_fingerprint, failure=VeoFailureContract(**fields),
        )
    return StillOutcomeContract(
        produced_by=outcome.produced_by, status="FAILURE", shot_sequence=outcome.shot_sequence,
        shot_fingerprint=outcome.shot_fingerprint, failure=StillFailureContract(**fields),
    )


async def _review_shot_asset(
    *, agent: str, shot, asset, subtitle_text: str, outcome, settings: Settings, manifest: RunManifest,
    launch: _ShotLaunch, reservation: Reservation, stage_id: str, count: int, attempt_path: Path,
    attempt_record: dict, attempt_paths: list[str], guard: PaidToolGuard, start_level: int,
) -> ShotFailure | tuple[object, str]:
    """Independent visual QA of a producer-approved shot asset (Story 4.5, FR12).

    Returns the outcome to act on -- the producer's own on approval, or a
    FAILURE outcome carrying the reviewer's rejection -- with the reviewer's
    summary; or the shot's failure when no usable review could be had. The
    review runs inside the attempt's reservation, which holds one review.
    """

    fail = functools.partial(ShotFailure, shot.sequence, agent, "VisualQAVerdictContract")
    ledger = launch.ledger
    cap = settings.claude_reviewer_cap_usd
    video = agent == "veo_agent"
    if video:
        images = [outcome.result.seed.local_path, *outcome.result.preview_paths]
    else:
        images = [outcome.result.local_image_path]
    references, ladder_note = _reviewer_character_context(asset, guard, start_level)

    def save_review(review: dict) -> None:
        attempt_record["review"] = review
        _atomic_write_json(attempt_path, attempt_record)

    log(f"Shot {shot.sequence}: having the result checked by an independent reviewer", indent=2)
    try:
        result = await review_visual_asset(
            generation_mode="VEO" if video else "STILL",
            shot=shot.model_dump(mode="json"),
            subtitle_text=subtitle_text,
            images_under_review=images,
            character_reference_paths=references,
            ladder_note=ladder_note,
            max_budget_usd=cap,
        )
    except SkillIsolationError as exc:
        # Before any review turn: nothing to charge. Systemic, like a producer
        # leak, and the attempt goes back as the story critic's does.
        save_review({"status": "SKILL_ISOLATION", "error": str(exc)})
        manifest.iteration_counts[stage_id] = count - 1
        save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
        return fail(str(exc), attempt_count=count - 1, partial_artifact_paths=attempt_paths)
    except Exception as exc:
        refusal = _vendor_refusal(exc=exc)
        if refusal is not None:
            return _reviewer_vendor_limit(
                refusal, agent=agent, shot=shot, manifest=manifest, stage_id=stage_id, count=count,
                claude_cost=0.0, save_review=save_review, attempt_paths=attempt_paths,
            )
        # No cost was ever reported, so charge the review's whole cap.
        ledger.debit(reservation, cap)
        save_review({"status": "EXCEPTION", "error": f"{type(exc).__name__}: {exc}", "charged_cost_usd": cap})
        return fail(
            f"Shot {shot.sequence}: the independent visual review crashed: {type(exc).__name__}: {exc}",
            attempt_count=count, partial_artifact_paths=attempt_paths,
        )

    cost = result.total_cost_usd
    if cost is None or not math.isfinite(cost) or cost < 0:
        ledger.debit(reservation, cap)
        save_review({"status": "NO_COST", "charged_cost_usd": cap})
        return fail(
            f"Shot {shot.sequence}: the visual reviewer returned no usable Claude cost",
            attempt_count=count, partial_artifact_paths=attempt_paths,
        )
    ledger.debit(reservation, cost)
    refusal = _vendor_refusal(result=result)
    if refusal is not None:
        return _reviewer_vendor_limit(
            refusal, agent=agent, shot=shot, manifest=manifest, stage_id=stage_id, count=count,
            claude_cost=cost, save_review=save_review, attempt_paths=attempt_paths,
        )
    try:
        if result.is_error:
            raise ValueError(f"Claude failed ({result.subtype}): {result.errors or result.result}")
        verdict = VisualQAVerdictContract.model_validate(result.structured_output)
        if not verdict.approved and verdict.failure_stage not in REVIEW_STAGES[agent]:
            raise ValueError(f"failure_stage {verdict.failure_stage!r} does not apply to this kind of shot")
    except ValueError as exc:
        save_review({
            "status": "INVALID_VERDICT", "error": str(exc), "agent_output": result.structured_output,
            "result": result.result, "subtype": result.subtype, "claude_cost_usd": cost,
        })
        return fail(
            f"Shot {shot.sequence}: invalid visual reviewer verdict: {exc}",
            attempt_count=count, partial_artifact_paths=attempt_paths,
        )

    review = {"verdict": verdict.model_dump(mode="json"), "claude_cost_usd": cost}
    if verdict.approved:
        save_review(review)
        return outcome, verdict.summary
    rejected = _reviewer_rejection(outcome, verdict, agent=agent, count=count, images=images)
    attempt_record["producer_outcome"] = attempt_record["structured_output"]
    attempt_record["structured_output"] = rejected.model_dump(mode="json")
    save_review(review)
    return rejected, verdict.summary


def _reviewer_vendor_limit(
    refusal: VendorRefusal, *, agent: str, shot, manifest: RunManifest, stage_id: str, count: int,
    claude_cost: float, save_review: Callable[[dict], None], attempt_paths: list[str],
) -> ShotFailure:
    """A vendor refusal of the review: the attempt goes back and the run stops (FR10).

    The Claude CLI already retried any 429, so a transient one stops too.
    """

    save_review({
        "status": "VENDOR_LIMIT", "vendor_refusal": refusal.kind, "detail": refusal.detail,
        "claude_cost_usd": claude_cost,
    })
    manifest.iteration_counts[stage_id] = count - 1
    save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
    return ShotFailure(
        shot.sequence, agent, "VisualQAVerdictContract",
        f"Blocked by a vendor limit ({refusal.kind}) reviewing shot {shot.sequence}: {refusal.detail}",
        attempt_count=count - 1, partial_artifact_paths=attempt_paths, reason_code="vendor_limit",
    )


async def _review_overshoot(
    launch: _ShotLaunch, reservation: Reservation, agent: str, sequence: int, count: int,
    attempt_paths: list[str],
) -> ShotFailure | None:
    """The overdraw check again, once the review has been charged too (FR7)."""

    if not await launch.overdrawn(reservation):
        return None
    return ShotFailure(
        sequence, agent, "Budget",
        f"Shot {sequence}: try {count} and its review cost ${reservation.debited_usd:.4f}, "
        f"${reservation.debited_usd - reservation.amount_usd:.4f} more than it reserved, "
        "and the run budget can't cover the overshoot",
        attempt_count=count, partial_artifact_paths=attempt_paths, reason_code="budget_exhausted",
    )


async def _produce_veo_shot(
    shot, *, asset, subtitle_text: str, settings: Settings, manifest: RunManifest, launch: _ShotLaunch,
) -> ShotFailure | str:
    """Run one VEO-mode shot's bounded attempts; "done", "stopped" or its failure.

    It never halts the run itself: the production step gathers every shot's
    result into one report (Story 4.4).
    """

    fail = functools.partial(ShotFailure, shot.sequence)
    ledger = launch.ledger
    settings_payload = _veo_settings_payload(settings)
    claude_cap, reserved_external_cost = shot_attempt_reservation(settings, "VEO")
    # Ceilings come from the project's config.json and nowhere else, so there
    # is one file to edit when a stage needs more room.
    max_veo_attempts = manifest.attempts_allowed("veo_agent")
    stage_id = f"veo_agent_shot_{shot.sequence}"
    count = manifest.iteration_counts.get(stage_id, 0)
    if type(count) is not int or count < 0:
        return fail("veo_agent", "RunManifest", f"Invalid iteration count for {stage_id}")
    correction = ""
    previous_outcome: object = None
    transient_retries = 0
    # Which rung of the seed prompt ladder this shot's next attempt starts
    # on. Only a Veo likeness rejection moves it, and it never moves back.
    # Seeded from the attempts already on disk so a resumed run does not
    # repeat a wording Veo has refused before.
    seed_prompt_level = _prior_character_rejections(shot.sequence)
    if seed_prompt_level:
        log(
            f"Shot {shot.sequence}: this shot's face was refused {seed_prompt_level} "
            "time(s) in an earlier run, so the seed starts with a less detailed one",
            indent=2,
        )
    attempt_paths: list[str] = [
        str(path)
        for path in sorted(RUN_STATE_DIR.glob(f"veo_agent_shot_{shot.sequence}_attempt_*.json"))
    ]

    while count < max_veo_attempts:
        # FR7: this attempt's worst case is held before its query starts.
        # Every retry reserves afresh; one that can't is a budget stop, not
        # a demotion -- modes were fixed before launch (AD-15(c)).
        if launch.stopped_by is not None:
            # FR8: a systemic failure elsewhere stops new attempts too; the
            # shot is unfinished, not failed, and a resume picks it up.
            return "stopped"
        reservation = await launch.reserve(
            label=f"shot {shot.sequence} try {count + 1}",
            claude_cap_usd=claude_cap, external_usd=reserved_external_cost,
        )
        if reservation is None:
            if launch.stopped_by is not None:
                return "stopped"
            return fail(
                "veo_agent",
                "Budget",
                f"Shot {shot.sequence}: try {count + 1} needs ${claude_cap + reserved_external_cost:.4f} "
                f"reserved (Claude ${claude_cap:.4f} + paid calls ${reserved_external_cost:.4f}), but only "
                f"${ledger.unreserved_usd():.4f} of the run budget is unreserved",
                attempt_count=count,
                partial_artifact_paths=attempt_paths,
                reason_code="budget_exhausted",
            )
        try:
            count += 1
            manifest.iteration_counts[stage_id] = count
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            # FR9: the hook denies a paid call this attempt's reservation can't
            # cover, counting every paid call already allowed in this session.
            guard = PaidToolGuard(
                limit_usd=reservation.paid_tool_allowance_usd,
                costs={VEO_SEED_TOOL: settings.image_call_cost_usd, VEO_CLIP_TOOL: settings.veo_call_cost_usd},
            )
            try:
                agent_result = await generate_veo_asset(
                    shot=shot.model_dump(mode="json"),
                    asset=asset.model_dump(mode="json"),
                    subtitle_text=subtitle_text,
                    settings={**settings_payload, "seed_prompt_level": seed_prompt_level},
                    attempt=count,
                    max_budget_usd=settings.claude_shot_attempt_cap_usd,
                    correction=correction,
                    previous_outcome=previous_outcome,
                    guard=guard,
                )
            except SkillIsolationError as exc:
                # Raised on the init message, before any model turn or paid
                # tool call, so nothing is charged and the attempt goes back.
                manifest.iteration_counts[stage_id] = count - 1
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return fail(
                    "veo_agent", "VeoOutcomeContract", str(exc),
                    attempt_count=count - 1, partial_artifact_paths=attempt_paths,
                )
            except Exception as exc:
                refusal = _vendor_refusal(exc=exc, guard=guard)
                if refusal is not None:
                    halted = await _handle_shot_vendor_refusal(
                        refusal, agent="veo_agent", contract_name="VeoOutcomeContract",
                        stage_id=stage_id, sequence=shot.sequence, count=count,
                        transient_retries=transient_retries, claude_cost=0.0, guard=guard,
                        manifest=manifest, ledger=ledger, reservation=reservation, release=launch.release,
                        attempt_paths=attempt_paths, exc=exc,
                    )
                    if halted is not None:
                        return halted
                    count -= 1
                    transient_retries += 1
                    continue
                # Tools may have billed and Claude may have spent up to its cap,
                # with no cost ever reported, so charge the attempt's whole
                # reservation -- its worst case, and never more.
                charged_cost = reservation.amount_usd
                ledger.debit(reservation, charged_cost)
                attempt_path = RUN_STATE_DIR / f"veo_agent_shot_{shot.sequence}_attempt_{count}.json"
                _atomic_write_json(attempt_path, {
                    "status": "EXCEPTION",
                    "error": f"{type(exc).__name__}: {exc}",
                    "charged_cost_usd": charged_cost,
                })
                attempt_paths.append(str(attempt_path))
                return fail(
                    "veo_agent",
                    "VeoOutcomeContract",
                    "Veo agent ended without an accountable result after tools may have run: "
                    f"{type(exc).__name__}: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                    reason_code="budget_exhausted" if guard.denial else "unclassified",
                )

            claude_cost = agent_result.total_cost_usd
            if claude_cost is None or not math.isfinite(claude_cost) or claude_cost < 0:
                # As unaccountable as a crash, so charged the same worst case.
                ledger.debit(reservation, reservation.amount_usd)
                return fail(
                    "veo_agent", "Budget", "Veo agent returned no usable Claude cost",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                )
            if guard.denial is not None:
                # Checked before the outcome, so the agent's own output can't
                # mask a denied paid call (AD-7).
                _charge_guarded_attempt(
                    agent="veo_agent", sequence=shot.sequence, count=count,
                    status="BUDGET_DENIED", suffix="budget_denied", claude_cost=claude_cost,
                    guard=guard, ledger=ledger, reservation=reservation, attempt_paths=attempt_paths,
                    agent_result=agent_result, detail=guard.denial,
                )
                return fail(
                    "veo_agent", "Budget", guard.denial,
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                    reason_code="budget_exhausted",
                )
            refusal = _vendor_refusal(result=agent_result, guard=guard)
            if refusal is not None and refusal.kind == "transient" and _reports_success(agent_result):
                # The agent retried the tool itself and got its asset; a
                # rerun would throw that paid work away.
                refusal = None
            if refusal is not None:
                halted = await _handle_shot_vendor_refusal(
                    refusal, agent="veo_agent", contract_name="VeoOutcomeContract",
                    stage_id=stage_id, sequence=shot.sequence, count=count,
                    transient_retries=transient_retries, claude_cost=claude_cost, guard=guard,
                    manifest=manifest, ledger=ledger, reservation=reservation, release=launch.release,
                    attempt_paths=attempt_paths, agent_result=agent_result,
                )
                if halted is not None:
                    return halted
                count -= 1
                transient_retries += 1
                continue
            transient_retries = 0
            try:
                if agent_result.is_error:
                    raise ValueError(
                        f"Claude failed ({agent_result.subtype}): {agent_result.errors or agent_result.result}"
                    )
                outcome = VeoOutcomeContract.model_validate(agent_result.structured_output)
                expected_fingerprint = shot_fingerprint(shot)
                seed_cost, clip_cost = _veo_attempt_cost(outcome, settings)
                outcome = _stamp_veo_outcome(
                    outcome,
                    shot_sequence=shot.sequence,
                    expected_fingerprint=expected_fingerprint,
                    seed_cost_usd=seed_cost,
                    clip_cost_usd=clip_cost,
                )
                if outcome.status == "SUCCESS" and outcome.result.seed.source_asset_id not in shot.source_asset_ids:
                    raise ValueError("Veo agent outcome seed source asset is not cited by the requested shot")
            except ValueError as exc:
                # The agent cost is known, but a malformed response cannot safely
                # prove which external paid calls occurred. Charge the reserved
                # ceiling conservatively and halt instead of retrying blindly.
                ledger.debit(reservation, claude_cost + reserved_external_cost)
                attempt_path = RUN_STATE_DIR / f"veo_agent_shot_{shot.sequence}_attempt_{count}.json"
                _atomic_write_json(attempt_path, {
                    "status": "INVALID_OUTCOME",
                    "error": str(exc),
                    "structured_output": agent_result.structured_output,
                    "result": agent_result.result,
                    "subtype": agent_result.subtype,
                    "errors": agent_result.errors,
                    "claude_cost_usd": claude_cost,
                    "charged_paid_tool_cost_usd": reserved_external_cost,
                })
                attempt_paths.append(str(attempt_path))
                return fail(
                    "veo_agent",
                    "VeoOutcomeContract",
                    f"Invalid Veo agent outcome: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                )

            manifest.session_id = agent_result.session_id
            ledger.debit(reservation, claude_cost + outcome.cost_usd)
            attempt_path = RUN_STATE_DIR / f"veo_agent_shot_{shot.sequence}_attempt_{count}.json"
            attempt_record = {
                "structured_output": outcome.model_dump(mode="json"),
                "result": agent_result.result,
                "subtype": agent_result.subtype,
                "errors": agent_result.errors,
                "claude_cost_usd": claude_cost,
                "paid_tool_cost_usd": outcome.cost_usd,
                # Which rung drew this seed. A resume needs the rung that was
                # refused, not how many refusals there were: three rejections
                # of the same wording mean "try the next rung", not "skip three".
                "seed_prompt_level": seed_prompt_level,
            }
            _atomic_write_json(attempt_path, attempt_record)
            attempt_paths.append(str(attempt_path))
            previous_outcome = outcome.model_dump(mode="json")

            if await launch.overdrawn(reservation):
                # The attempt cost more than it reserved, and the excess took the
                # unreserved pool below zero (FR7): systemic, like any budget stop.
                return fail(
                    "veo_agent", "Budget",
                    f"Shot {shot.sequence}: try {count} cost ${reservation.debited_usd:.4f}, "
                    f"${reservation.debited_usd - reservation.amount_usd:.4f} more than it reserved, "
                    "and the run budget can't cover the overshoot",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                    reason_code="budget_exhausted",
                )

            reviewer_summary = ""
            if outcome.status == "SUCCESS":
                # FR12: approved only once an independent reviewer approves
                # too, and before the upsert. The clip is already paid for.
                reviewed = await _review_shot_asset(
                    agent="veo_agent", shot=shot, asset=asset, subtitle_text=subtitle_text,
                    outcome=outcome, settings=settings, manifest=manifest, launch=launch,
                    reservation=reservation, stage_id=stage_id, count=count,
                    attempt_path=attempt_path, attempt_record=attempt_record,
                    attempt_paths=attempt_paths, guard=guard, start_level=seed_prompt_level,
                )
                if isinstance(reviewed, ShotFailure):
                    return reviewed
                outcome, reviewer_summary = reviewed
                previous_outcome = outcome.model_dump(mode="json")
                overshoot = await _review_overshoot(launch, reservation, "veo_agent", shot.sequence, count, attempt_paths)
                if overshoot is not None:
                    return overshoot

            if outcome.status == "SUCCESS":
                try:
                    entry = ProductionAssetEntry.from_veo_result(
                        outcome.result,
                        source_asset_ids=shot.source_asset_ids,
                        reviewer_summary=reviewer_summary,
                    )
                    upsert_production_asset(
                        entry,
                        path=PRODUCTION_ASSETS_FILE,
                        writer="orchestrator",
                    )
                except Exception as exc:
                    return fail(
                        "veo_agent",
                        "ProductionAssetsContract",
                        f"Approved Veo result could not be persisted: {type(exc).__name__}: {exc}",
                        attempt_count=count,
                        partial_artifact_paths=attempt_paths,
                    )
                log(f"Shot {shot.sequence}: video clip approved → {entry.local_path}", indent=2)
                return "done"

            failure = outcome.failure
            correction = failure.reason
            if not failure.retryable:
                return fail(
                    "veo_agent",
                    "VeoFailureContract",
                    failure.reason,
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths + failure.partial_artifact_paths,
                    shot_specific=True,
                    failure_stage=failure.stage,
                )
            log(
                f"Shot {shot.sequence}: try {count} of {max_veo_attempts} "
                f"didn't work — {failure.reason}",
                error=True,
            )
            if _forces_simpler_character_wording(failure):
                # Redrawing the same wording would produce the same face and
                # be rejected again -- which is exactly what burned all three
                # attempts before this existed.
                seed_prompt_level += 1
                correction = (
                    f"{failure.reason} Veo rejected the person drawn into this seed as too "
                    "realistic a depiction of a real individual. The next attempt redraws the "
                    "seed with a less specific face."
                )
                log(
                    f"Shot {shot.sequence}: redrawing the seed with a less detailed face "
                    f"(character wording level {seed_prompt_level})",
                    indent=2,
                )
        finally:
            # Whatever this attempt didn't spend goes back, on every path.
            launch.release(reservation)
    else:
        reason = "Veo retry ceiling exhausted"
        last_failure_paths: list[str] = []
        last_failure: dict = {}
        if isinstance(previous_outcome, dict):
            last_failure = previous_outcome.get("failure") or {}
            reason = last_failure.get("reason", reason)
            last_failure_paths = last_failure.get("partial_artifact_paths", [])
        return fail(
            "veo_agent",
            "VeoFailureContract",
            reason,
            attempt_count=count,
            partial_artifact_paths=attempt_paths + last_failure_paths,
            reason_code="retry_ceiling",
            shot_specific=True,
            failure_stage=last_failure.get("stage"),
        )


def _stills_settings_payload(settings: Settings) -> dict:
    return {
        "project_id": settings.project_id,
        "location": settings.location,
        "image_model": settings.image_model,
        "image_provider": settings.image_provider,
        "codex_timeout_seconds": settings.codex_image_timeout_seconds,
        "image_call_cost_usd": settings.image_call_cost_usd,
    }


async def _produce_still_shot(
    shot, *, asset, subtitle_text: str, settings: Settings, manifest: RunManifest, launch: _ShotLaunch,
) -> ShotFailure | str:
    """Run one STILL-mode shot's bounded attempts; "done", "stopped" or its failure.

    Mirrors `_produce_veo_shot` minus the seed stage: one image-edit call per
    attempt, no GCS/video concept.
    """

    fail = functools.partial(ShotFailure, shot.sequence)
    ledger = launch.ledger
    settings_payload = _stills_settings_payload(settings)
    claude_cap, reserved_external_cost = shot_attempt_reservation(settings, "STILL")
    max_stills_attempts = manifest.attempts_allowed("stills_agent")
    stage_id = f"stills_agent_shot_{shot.sequence}"
    count = manifest.iteration_counts.get(stage_id, 0)
    if type(count) is not int or count < 0:
        return fail("stills_agent", "RunManifest", f"Invalid iteration count for {stage_id}")
    correction = ""
    previous_outcome: object = None
    transient_retries = 0
    attempt_paths: list[str] = [
        str(path)
        for path in sorted(RUN_STATE_DIR.glob(f"stills_agent_shot_{shot.sequence}_attempt_*.json"))
    ]

    while count < max_stills_attempts:
        # FR7: this attempt's worst case is held before its query starts.
        # Every retry reserves afresh; one that can't is a budget stop, not
        # a demotion -- modes were fixed before launch (AD-15(c)).
        if launch.stopped_by is not None:
            # FR8: a systemic failure elsewhere stops new attempts too; the
            # shot is unfinished, not failed, and a resume picks it up.
            return "stopped"
        reservation = await launch.reserve(
            label=f"shot {shot.sequence} try {count + 1}",
            claude_cap_usd=claude_cap, external_usd=reserved_external_cost,
        )
        if reservation is None:
            if launch.stopped_by is not None:
                return "stopped"
            return fail(
                "stills_agent",
                "Budget",
                f"Shot {shot.sequence}: try {count + 1} needs ${claude_cap + reserved_external_cost:.4f} "
                f"reserved (Claude ${claude_cap:.4f} + paid calls ${reserved_external_cost:.4f}), but only "
                f"${ledger.unreserved_usd():.4f} of the run budget is unreserved",
                attempt_count=count,
                partial_artifact_paths=attempt_paths,
                reason_code="budget_exhausted",
            )
        try:
            count += 1
            manifest.iteration_counts[stage_id] = count
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            # FR9: the hook denies a paid call this attempt's reservation can't
            # cover, counting every paid call already allowed in this session.
            guard = PaidToolGuard(
                limit_usd=reservation.paid_tool_allowance_usd, costs={STILL_TOOL: settings.image_call_cost_usd},
            )
            try:
                agent_result = await generate_still_asset(
                    shot=shot.model_dump(mode="json"),
                    asset=asset.model_dump(mode="json"),
                    subtitle_text=subtitle_text,
                    settings=settings_payload,
                    attempt=count,
                    max_budget_usd=settings.claude_shot_attempt_cap_usd,
                    correction=correction,
                    previous_outcome=previous_outcome,
                    guard=guard,
                )
            except SkillIsolationError as exc:
                # Raised on the init message, before any model turn or paid
                # tool call, so nothing is charged and the attempt goes back.
                manifest.iteration_counts[stage_id] = count - 1
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return fail(
                    "stills_agent", "StillOutcomeContract", str(exc),
                    attempt_count=count - 1, partial_artifact_paths=attempt_paths,
                )
            except Exception as exc:
                refusal = _vendor_refusal(exc=exc, guard=guard)
                if refusal is not None:
                    halted = await _handle_shot_vendor_refusal(
                        refusal, agent="stills_agent", contract_name="StillOutcomeContract",
                        stage_id=stage_id, sequence=shot.sequence, count=count,
                        transient_retries=transient_retries, claude_cost=0.0, guard=guard,
                        manifest=manifest, ledger=ledger, reservation=reservation, release=launch.release,
                        attempt_paths=attempt_paths, exc=exc,
                    )
                    if halted is not None:
                        return halted
                    count -= 1
                    transient_retries += 1
                    continue
                # Tools may have billed and Claude may have spent up to its cap,
                # with no cost ever reported, so charge the attempt's whole
                # reservation -- its worst case, and never more.
                charged_cost = reservation.amount_usd
                ledger.debit(reservation, charged_cost)
                attempt_path = RUN_STATE_DIR / f"stills_agent_shot_{shot.sequence}_attempt_{count}.json"
                _atomic_write_json(attempt_path, {
                    "status": "EXCEPTION",
                    "error": f"{type(exc).__name__}: {exc}",
                    "charged_cost_usd": charged_cost,
                })
                attempt_paths.append(str(attempt_path))
                return fail(
                    "stills_agent",
                    "StillOutcomeContract",
                    "Stills agent ended without an accountable result after tools may have run: "
                    f"{type(exc).__name__}: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                    reason_code="budget_exhausted" if guard.denial else "unclassified",
                )

            claude_cost = agent_result.total_cost_usd
            if claude_cost is None or not math.isfinite(claude_cost) or claude_cost < 0:
                # As unaccountable as a crash, so charged the same worst case.
                ledger.debit(reservation, reservation.amount_usd)
                return fail(
                    "stills_agent", "Budget", "Stills agent returned no usable Claude cost",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                )
            if guard.denial is not None:
                # Checked before the outcome, so the agent's own output can't
                # mask a denied paid call (AD-7).
                _charge_guarded_attempt(
                    agent="stills_agent", sequence=shot.sequence, count=count,
                    status="BUDGET_DENIED", suffix="budget_denied", claude_cost=claude_cost,
                    guard=guard, ledger=ledger, reservation=reservation, attempt_paths=attempt_paths,
                    agent_result=agent_result, detail=guard.denial,
                )
                return fail(
                    "stills_agent", "Budget", guard.denial,
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                    reason_code="budget_exhausted",
                )
            refusal = _vendor_refusal(result=agent_result, guard=guard)
            if refusal is not None and refusal.kind == "transient" and _reports_success(agent_result):
                # The agent retried the tool itself and got its asset; a
                # rerun would throw that paid work away.
                refusal = None
            if refusal is not None:
                halted = await _handle_shot_vendor_refusal(
                    refusal, agent="stills_agent", contract_name="StillOutcomeContract",
                    stage_id=stage_id, sequence=shot.sequence, count=count,
                    transient_retries=transient_retries, claude_cost=claude_cost, guard=guard,
                    manifest=manifest, ledger=ledger, reservation=reservation, release=launch.release,
                    attempt_paths=attempt_paths, agent_result=agent_result,
                )
                if halted is not None:
                    return halted
                count -= 1
                transient_retries += 1
                continue
            transient_retries = 0
            try:
                if agent_result.is_error:
                    raise ValueError(
                        f"Claude failed ({agent_result.subtype}): {agent_result.errors or agent_result.result}"
                    )
                outcome = StillOutcomeContract.model_validate(agent_result.structured_output)
                expected_fingerprint = shot_fingerprint(shot)
                # One paid image call per attempt, at the rate the orchestrator
                # already reserved -- so the cost is stamped, never read back
                # from the agent and re-checked against a ceiling.
                outcome = _stamp_still_outcome(
                    outcome,
                    shot_sequence=shot.sequence,
                    expected_fingerprint=expected_fingerprint,
                    paid_call_cost_usd=settings.image_call_cost_usd,
                )
                if outcome.status == "SUCCESS" and outcome.result.source_asset_id not in shot.source_asset_ids:
                    raise ValueError("Stills agent outcome source asset is not cited by the requested shot")
            except ValueError as exc:
                # The agent cost is known, but a malformed response cannot safely
                # prove which external paid calls occurred. Charge the reserved
                # ceiling conservatively and halt instead of retrying blindly.
                ledger.debit(reservation, claude_cost + reserved_external_cost)
                attempt_path = RUN_STATE_DIR / f"stills_agent_shot_{shot.sequence}_attempt_{count}.json"
                _atomic_write_json(attempt_path, {
                    "status": "INVALID_OUTCOME",
                    "error": str(exc),
                    "structured_output": agent_result.structured_output,
                    "result": agent_result.result,
                    "subtype": agent_result.subtype,
                    "errors": agent_result.errors,
                    "claude_cost_usd": claude_cost,
                    "charged_paid_tool_cost_usd": reserved_external_cost,
                })
                attempt_paths.append(str(attempt_path))
                return fail(
                    "stills_agent",
                    "StillOutcomeContract",
                    f"Invalid stills agent outcome: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                )

            manifest.session_id = agent_result.session_id
            ledger.debit(reservation, claude_cost + outcome.cost_usd)
            attempt_path = RUN_STATE_DIR / f"stills_agent_shot_{shot.sequence}_attempt_{count}.json"
            attempt_record = {
                "structured_output": outcome.model_dump(mode="json"),
                "result": agent_result.result,
                "subtype": agent_result.subtype,
                "errors": agent_result.errors,
                "claude_cost_usd": claude_cost,
                "paid_tool_cost_usd": outcome.cost_usd,
            }
            _atomic_write_json(attempt_path, attempt_record)
            attempt_paths.append(str(attempt_path))
            previous_outcome = outcome.model_dump(mode="json")

            if await launch.overdrawn(reservation):
                # The attempt cost more than it reserved, and the excess took the
                # unreserved pool below zero (FR7): systemic, like any budget stop.
                return fail(
                    "stills_agent", "Budget",
                    f"Shot {shot.sequence}: try {count} cost ${reservation.debited_usd:.4f}, "
                    f"${reservation.debited_usd - reservation.amount_usd:.4f} more than it reserved, "
                    "and the run budget can't cover the overshoot",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                    reason_code="budget_exhausted",
                )

            reviewer_summary = ""
            if outcome.status == "SUCCESS":
                # FR12: approved only once an independent reviewer approves too.
                reviewed = await _review_shot_asset(
                    agent="stills_agent", shot=shot, asset=asset, subtitle_text=subtitle_text,
                    outcome=outcome, settings=settings, manifest=manifest, launch=launch,
                    reservation=reservation, stage_id=stage_id, count=count,
                    attempt_path=attempt_path, attempt_record=attempt_record,
                    attempt_paths=attempt_paths, guard=guard, start_level=0,
                )
                if isinstance(reviewed, ShotFailure):
                    return reviewed
                outcome, reviewer_summary = reviewed
                previous_outcome = outcome.model_dump(mode="json")
                overshoot = await _review_overshoot(launch, reservation, "stills_agent", shot.sequence, count, attempt_paths)
                if overshoot is not None:
                    return overshoot

            if outcome.status == "SUCCESS":
                try:
                    entry = ProductionAssetEntry.from_still_result(
                        outcome.result,
                        source_asset_ids=shot.source_asset_ids,
                        reviewer_summary=reviewer_summary,
                    )
                    upsert_production_asset(
                        entry,
                        path=PRODUCTION_ASSETS_FILE,
                        writer="orchestrator",
                    )
                except Exception as exc:
                    return fail(
                        "stills_agent",
                        "ProductionAssetsContract",
                        f"Approved still result could not be persisted: {type(exc).__name__}: {exc}",
                        attempt_count=count,
                        partial_artifact_paths=attempt_paths,
                    )
                log(f"Shot {shot.sequence}: picture approved → {entry.local_path}", indent=2)
                return "done"

            failure = outcome.failure
            correction = failure.reason
            if not failure.retryable:
                return fail(
                    "stills_agent",
                    "StillFailureContract",
                    failure.reason,
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths + failure.partial_artifact_paths,
                    shot_specific=True,
                    failure_stage=failure.stage,
                )
            log(
                f"Shot {shot.sequence}: try {count} of {max_stills_attempts} "
                f"didn't work — {failure.reason}",
                error=True,
            )
        finally:
            # Whatever this attempt didn't spend goes back, on every path.
            launch.release(reservation)
    else:
        reason = "Stills retry ceiling exhausted"
        last_failure_paths: list[str] = []
        last_failure: dict = {}
        if isinstance(previous_outcome, dict):
            last_failure = previous_outcome.get("failure") or {}
            reason = last_failure.get("reason", reason)
            last_failure_paths = last_failure.get("partial_artifact_paths", [])
        return fail(
            "stills_agent",
            "StillFailureContract",
            reason,
            attempt_count=count,
            partial_artifact_paths=attempt_paths + last_failure_paths,
            reason_code="retry_ceiling",
            shot_specific=True,
            failure_stage=last_failure.get("stage"),
        )


async def run_delivery_stage(settings: Settings, manifest: RunManifest) -> int:
    """Sync validated production assets into Remotion and render the final MP4."""

    remotion_failure = check_remotion_delivery_toolchain()
    if remotion_failure is not None:
        return _halt(
            "delivery",
            remotion_failure.failed_check or "remotion",
            remotion_failure.reason or "Remotion delivery preflight failed",
        )

    try:
        visual_plan = VisualPlanContract.model_validate_json(
            VISUAL_PLAN_FILE.read_text(encoding="utf-8")
        )
        subtitle_cues = SubtitleCuesContract.model_validate_json(
            SUBTITLE_CUES_FILE.read_text(encoding="utf-8")
        )
        production_assets = load_production_assets(PRODUCTION_ASSETS_FILE)
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("delivery", "DeliveryInputs", f"Validated delivery inputs required: {exc}")

    missing_sequences = [
        shot.sequence for shot in visual_plan.shots
        if str(shot.sequence) not in production_assets.shots
    ]
    if missing_sequences:
        return _halt(
            "delivery",
            "ProductionAssetsContract",
            f"Incomplete production assets: missing shot(s) {missing_sequences}",
            partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE)],
        )

    shot_asset_sources: dict[str, str] = {}
    shot_assets_public: dict[int, str] = {}
    for shot in visual_plan.shots:
        entry = production_assets.shots[str(shot.sequence)]
        public_relative = public_relative_path_for_production_asset(
            entry.local_path, entry.shot_sequence, entry.asset_type,
        )
        shot_asset_sources[str(shot.sequence)] = entry.local_path
        shot_assets_public[shot.sequence] = public_relative

    if not NARRATION_WAV.is_file():
        return _halt(
            "delivery",
            "NarrationAudio",
            f"Final narration audio not found at {NARRATION_WAV}",
        )

    try:
        # Cue pauses may still sit on disk from older backfills; absorb them in
        # memory for timeline.json without rewriting visual_plan fingerprints.
        _close_inter_shot_timing_gaps(visual_plan.shots)
        timeline = build_timeline_data(visual_plan, subtitle_cues, shot_assets_public)
    except ValueError as exc:
        return _halt("delivery", "TimelineConverter", str(exc))

    timeline_path = TIMELINE_FILE
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(timeline_path, timeline)

    try:
        await sync_remotion_assets.handler({
            "narration_path": str(NARRATION_WAV),
            "subtitle_cues_path": str(SUBTITLE_CUES_FILE),
            "timeline_path": str(timeline_path),
            "visual_plan_path": str(VISUAL_PLAN_FILE),
            "shot_asset_sources": shot_asset_sources,
        })
    except Exception as exc:
        return _halt("delivery", "sync_remotion_assets", f"{type(exc).__name__}: {exc}")

    # The tool reports the command and cwd it actually ran; the span records those.
    with tracing.span("render_remotion", as_type="tool", input={
        "timeline_path": str(timeline_path), "output_path": str(DEFAULT_REMOTION_RENDER_OUTPUT),
    }) as render_span:
        try:
            render_response = await render_remotion.handler({
                "output_path": str(DEFAULT_REMOTION_RENDER_OUTPUT),
            })
        except Exception as exc:
            return _halt("delivery", "render_remotion", f"{type(exc).__name__}: {exc}")
        render_span.update(output=tracing.tool_response_for_trace(render_response))

    try:
        render_payload = json.loads(render_response["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return _halt(
            "delivery",
            "render_remotion",
            f"Remotion render returned an unreadable tool response: {exc}",
        )
    # remotion/out/ is shared scratch that the next reel's render overwrites,
    # so the keeping copy goes in the project alongside its own metadata.
    rendered = Path(render_payload["output_path"])
    kept = Path(FINISHED_REEL_NAME)
    try:
        shutil.copy2(rendered, kept)
    except OSError as exc:
        return _halt("delivery", "render_remotion", f"Could not keep the rendered reel: {exc}")
    log(f"Finished! Your video is at {kept.resolve()}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.run",
        description="book_reels orchestrator entrypoint",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Name of the folder under projects/ holding this reel. Its "
             "source_images/ supplies the screenshots, and every artifact this "
             "run writes stays inside it.",
    )
    parser.add_argument("--retry-voice", action="store_true",
                        help="Archive previous voice evidence and reset only its bounded retry ceiling; keep cumulative spend.")
    args = parser.parse_args(argv)

    # Before anything else: every per-reel path in this repo is relative, so
    # moving into the project is what keeps one reel's files out of another's.
    try:
        project = enter_project(resolve_project(args.project))
    except ProjectError as exc:
        print(f"STOPPED — {exc}", file=sys.stderr)
        return 1

    log_path = start_run(RUN_STATE_DIR)
    # FR13: one trace per run, wrapping settings, preflight and every stage.
    # Off, and never imported, without the Langfuse keys (FR16).
    tracing.init_run_trace(args.project, log_path.stem if log_path else "run")
    exit_code = None
    try:
        exit_code = _run_after_start(args, project, log_path)
        return exit_code
    finally:
        tracing.end_run_trace(exit_code)


def _run_after_start(args: argparse.Namespace, project: Path, log_path: Path | None) -> int:
    banner(f"Starting a reel in project '{args.project}'")
    log(f"Everything this run writes stays in {project}")
    log(f"A copy of everything printed here is being saved to {log_path}")

    # A resumed invocation isn't literally "stage 1" -- but preflight
    # (AD-13) must still run before the first stage that will actually
    # execute. A corrupt manifest must never block that, so this fails
    # safe (fresh manifest) rather than raising.
    manifest = load_run_manifest(RUN_STATE_DIR)

    try:
        settings = load_settings()
    except SettingsError as exc:
        return _halt("settings", "Settings", str(exc))

    with tracing.span("preflight") as preflight_span:
        result = run_preflight(
            settings=settings,
            budget_spent_usd=manifest.budget_spent_usd,
        )
        preflight_span.update(output={
            "passed": result.passed, "failed_check": result.failed_check, "reason": result.reason,
        })

    if not result.passed:
        return _halt(
            "preflight",
            result.failed_check or "preflight",
            result.reason or "preflight failed",
        )

    log(f"Startup checks passed · reading screenshots from {SOURCE_IMAGES_DIR_NAME}/")
    character_references = resolve_character_references()
    if character_references:
        log(
            "Recurring character: "
            + ", ".join(str(path) for path in character_references)
            + " — drawn only into shots whose artwork already shows a person"
        )
    else:
        log("No recurring character set — people stay as the source drew them")
    source_images = Path(SOURCE_IMAGES_DIR_NAME)
    return asyncio.run(run_stages(source_images, settings, manifest, retry_voice=args.retry_voice))


async def run_stages(source_images_dir: Path, settings: Settings, manifest: RunManifest, *, retry_voice=False) -> int:
    """All SDK stages and their stream cleanup share one live event loop."""
    # Built here rather than at module scope on purpose: the tests replace
    # these stage functions as attributes of this module, and a list captured
    # at import time would keep calling the originals.
    # The middle name is each stage's trace span (Story 4.6).
    steps = [
        ("Writing the narration", "story_agent", run_narration_stage),
        ("Planning the shots", "visual_agent", run_visual_stage),
        ("Recording the voice and building subtitles", "voice", run_voice_stage),
        # Story 4.4: one step, Veo and still shots side by side.
        ("Making the video clips and still pictures", "production", run_production_stage),
        ("Putting the final video together", "delivery", run_delivery_stage),
    ]
    total = len(steps) + 1
    # Set once, before any shot, while no vendor call is in flight.
    configure_vendor_limits(
        veo=settings.veo_concurrency,
        gemini_image=settings.gemini_image_concurrency,
        codex_image=settings.codex_image_concurrency,
    )
    banner(f"Step 1 of {total} — Looking at your screenshots")
    started = time.monotonic()
    with tracing.span("asset_analyst") as stage_span:
        result = await run_screenshot_stage(source_images_dir, settings, manifest)
        tracing.mark_stage_result(stage_span, result, budget_spent_usd=manifest.budget_spent_usd)
    if result:
        return result
    log(f"Step 1 took {format_duration(time.monotonic() - started)}")
    for index, (name, span_name, stage) in enumerate(steps, start=2):
        banner(f"Step {index} of {total} — {name}")
        started = time.monotonic()
        with tracing.span(span_name) as stage_span:
            result = await stage(settings, manifest, retry_voice=True) if stage is run_voice_stage and retry_voice else await stage(settings, manifest)
            tracing.mark_stage_result(stage_span, result, budget_spent_usd=manifest.budget_spent_usd)
        if result:
            return result
        log(f"Step {index} took {format_duration(time.monotonic() - started)} · ${manifest.budget_spent_usd:.2f} spent so far")
    return 0


if __name__ == "__main__":
    sys.exit(main())
