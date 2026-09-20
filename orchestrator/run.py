"""Orchestrator CLI entrypoint: `python -m orchestrator.run <source_images_dir>`.

Runs preflight, deterministic ingestion, and bounded screenshot understanding
(`run_screenshot_stage`), then -- once that stage has a validated
`AnalyzedAssetsContract` -- bounded narration writing/review/revision
(`run_narration_stage`), then -- once that stage has a validated
`FinalStoryPlanContract` -- bounded visual/shot-mode planning
(`run_visual_stage`), then -- once that stage has a validated
`VisualPlanContract` too -- bounded voice/word-timing/subtitle-cue
generation (`run_voice_stage`). Each stage's validated persisted contract is
that stage's resume checkpoint. Approved VEO-mode shots then run through the
bounded `veo_agent` stage (`run_veo_stage`), and approved STILL-mode shots
through the bounded `stills_agent` stage (`run_stills_stage`); both record
into the same partial production-asset manifest via the orchestrator's sole
keyed-upsert write path.
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
from orchestrator.agents.stills_agent import generate_still_asset
from orchestrator.agents.story_agent import write_story
from orchestrator.agents.veo_agent import generate_veo_asset
from orchestrator.agents.visual_agent import plan_visuals
from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.stills import StillOutcomeContract
from orchestrator.contracts.subtitle_cues import SubtitleCuesContract
from orchestrator.contracts.veo import VeoOutcomeContract, shot_fingerprint
from orchestrator.contracts.visual_plan import VisualPlanContract
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
from orchestrator.tools.deterministic_tools import (
    ASSETS_FILE,
    DEFAULT_REMOTION_RENDER_OUTPUT,
    ImpactPhraseNotFoundError,
    build_subtitle_cues,
    extract_word_timing,
    ingest,
    public_relative_path_for_production_asset,
    render_remotion,
    sync_remotion_assets,
    tokenize,
)
from orchestrator.tools.gemini_tools import (
    NARRATION_WAV,
    generate_narration_audio,
    resolve_character_references,
)
from orchestrator.tools.veo_tools import select_clip_duration_seconds
from orchestrator.tools.timeline_converter import build_timeline_data

ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
FINAL_STORY_PLAN_FILE = Path("metadata/final_story_plan.json")
VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")
TIMELINE_FILE = Path("metadata/timeline.json")
MAX_ASSET_ANALYST_ATTEMPTS = 4
MAX_STORY_AGENT_ATTEMPTS = 4
MAX_VISUAL_AGENT_ATTEMPTS = 4
# AD-3: transient-failure-retry default (not the 4-ceiling generate-review-
# revise pattern) -- no creative self-correction loop exists in this stage.
MAX_VOICE_AGENT_ATTEMPTS = 3
# Story 2.4: mirrors Settings.max_veo_attempts's default ceiling for the
# sibling single-shot bounded retry loop; a plain module constant rather than
# a new Settings field, since settings.py's existing image-cost fields are
# the only stills-specific configuration this story needs (Code Map).
MAX_STILLS_ATTEMPTS = 3
# How many shots per reel may become real Veo video. `visual_agent` nominates
# more than this (MIN_VIDEO_NOMINATIONS) so there are spares once the shots too
# long for a single clip are dropped.
MAX_VEO_SHOTS = 3
# Advisory only: falling short is logged loudly but never halts a run. A reel
# whose beats are all long legitimately ends up with fewer video shots.
TARGET_MIN_VEO_SHOTS = 2


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
    finalize_before_persist: Callable[[object], None] | None = None,
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
        print(f"{stage_id}: starting attempt {count}/{max_attempts} (budget remaining ${settings.max_budget_usd - manifest.budget_spent_usd:.4f})")
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
                if finalize_before_persist is not None:
                    finalize_before_persist(contract)
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
        print("=== video promotion (nominations -> VEO) ===")
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
            print(f"  shot {shot.sequence}: not promoted (already at the {MAX_VEO_SHOTS}-video cap)")
            continue
        duration = shot.end_seconds - shot.start_seconds
        try:
            clip_seconds = select_clip_duration_seconds(duration, settings.veo_duration_seconds)
        except ValueError as exc:
            print(f"  shot {shot.sequence}: not promoted ({exc})")
            continue
        shot.generation_mode = "VEO"
        promoted.append(shot.sequence)
        print(f"  shot {shot.sequence}: promoted to VEO ({duration:.2f}s -> {clip_seconds}s clip)")

    for shot in visual_plan.shots:
        shot.video_promotion_decided = True

    if not nominated:
        print("video promotion: visual plan carries no video nominations")
    elif len(promoted) < TARGET_MIN_VEO_SHOTS:
        print(
            f"video promotion: only {len(promoted)} of {len(nominated)} nominated shot(s) fit inside "
            f"a {settings.veo_duration_seconds}s clip (wanted {TARGET_MIN_VEO_SHOTS}); "
            "continuing with the rest as stills",
            file=sys.stderr,
        )
    return promoted


def _demote_veo_shot_to_still(sequence: int, reason: str) -> None:
    """Send one not-yet-generated VEO shot back to STILL, in the plan on disk.

    Safe because the shot has no production-asset entry yet, so no fingerprint
    stale-check can trip, and every shot already carries `still_motion`.
    """
    visual_plan = VisualPlanContract.model_validate_json(
        VISUAL_PLAN_FILE.read_text(encoding="utf-8")
    )
    for shot in visual_plan.shots:
        if shot.sequence == sequence:
            shot.generation_mode = "STILL"
            break
    _atomic_write_json(VISUAL_PLAN_FILE, visual_plan.model_dump(mode="json"))
    print(f"veo_agent: shot {sequence} demoted to STILL — {reason}", file=sys.stderr)


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
        max_attempts=MAX_VISUAL_AGENT_ATTEMPTS,
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

    print("voice_agent: step 1/3 TTS — generating narration audio…")
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
    print(
        f"voice_agent: step 1/3 TTS done — {tts_payload['audio_path']} "
        f"({audio_duration_seconds:.2f}s)"
    )
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
        print("voice_agent: step 2/3 STT — aligning word timings (splits audio >55s)…")
        stt_response = await extract_word_timing.handler({
            "audio_path": tts_payload["audio_path"],
            "narration_script": narration_script,
            "project_id": settings.project_id,
        })
        stt_payload = json.loads(stt_response["content"][0]["text"])
        stt_cost = round(audio_duration_seconds * STT_COST_PER_SECOND_USD, 6)
        print(
            f"voice_agent: step 2/3 STT done — "
            f"exact_match_ratio={stt_payload['alignment_stats']['exact_match_ratio']:.3f}, "
            f"words={len(stt_payload['words'])}"
        )

        print("voice_agent: step 3/3 subtitles — building cue segments…")
        dp_response = await build_subtitle_cues.handler({
            "word_timing": stt_payload,
            "narration_script": narration_script,
            "voice_direction": voice_direction,
            "scenes": [scene.model_dump(mode="json") for scene in story_plan.scenes],
            "viewer_reflection": story_plan.story_arc.viewer_reflection,
        })
        dp_payload = json.loads(dp_response["content"][0]["text"])
        print(f"voice_agent: step 3/3 subtitles done — {dp_payload['cue_count']} cues")
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
        # Pass each cue through whole rather than re-projecting named keys.
        # `SubtitleCuesContract.Cue` sets `extra="ignore"`, so the fields it
        # doesn't model (duration_seconds, start/end_word_index,
        # duration_warning) are still dropped at validation -- but a field it
        # *does* model can no longer be lost by being forgotten here, which is
        # exactly how every cue's `style_hint` silently became NORMAL and made
        # the IMPACT/EMPHASIS/REFLECTION render paths unreachable.
        "cues": dp_payload["cues"],
    }

    return _voice_pipeline_result(
        subtype="success", is_error=False, cost=round(tts_cost + stt_cost, 6),
        structured_output=structured_output, start=start,
    )


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

    if _voice_stage_skip_is_valid(story_plan):
        print(f"voice_agent skipped: validated {SUBTITLE_CUES_FILE}")
        backfill_failure = _voice_shot_timing_backfill_or_halt(settings)
        return backfill_failure if backfill_failure is not None else 0

    async def call_agent(feedback: str | None, previous_output: object, remaining_budget: float) -> ResultMessage:
        return await run_voice_pipeline(story_plan, settings, manifest.budget_spent_usd)

    def validate_contract(contract: SubtitleCuesContract) -> None:
        contract.validate_sources(story_plan)

    result = await run_bounded_agent_stage(
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
    if result != 0:
        return result
    backfill_failure = _voice_shot_timing_backfill_or_halt(settings)
    return backfill_failure if backfill_failure is not None else 0


def _veo_settings_payload(settings: Settings) -> dict:
    return {
        "project_id": settings.project_id,
        "location": settings.location,
        "image_model": settings.image_model,
        "veo_model": settings.veo_model,
        "gcs_output_uri": settings.gcs_bucket_uri,
        "resolution": settings.veo_resolution,
        "duration_seconds": settings.veo_duration_seconds,
        "poll_seconds": settings.veo_poll_seconds,
        "max_poll_seconds": settings.veo_max_poll_seconds,
        "image_call_cost_usd": settings.image_call_cost_usd,
        "veo_call_cost_usd": settings.veo_call_cost_usd,
    }


def _stamp_still_outcome_fingerprint(
    outcome: StillOutcomeContract,
    *,
    shot_sequence: int,
    expected_fingerprint: str,
) -> StillOutcomeContract:
    """Replace agent-copied fingerprints with the orchestrator's authoritative hash.

    Sequence is the hard binding to the requested shot. Fingerprints are tool/
    provenance metadata that Claude often retypes incorrectly into structured
    output; trusting them as a gate discards otherwise-valid paid stills.
    """

    if outcome.shot_sequence != shot_sequence:
        raise ValueError("Stills agent outcome does not match the requested source shot")
    updates: dict = {"shot_fingerprint": expected_fingerprint}
    if outcome.result is not None:
        updates["result"] = outcome.result.model_copy(
            update={"shot_fingerprint": expected_fingerprint}
        )
    if outcome.failure is not None:
        updates["failure"] = outcome.failure.model_copy(
            update={"shot_fingerprint": expected_fingerprint}
        )
    return outcome.model_copy(update=updates)


def _stamp_veo_outcome_fingerprint(
    outcome: VeoOutcomeContract,
    *,
    shot_sequence: int,
    expected_fingerprint: str,
) -> VeoOutcomeContract:
    """Same authoritative stamp as stills; also rewrites nested seed fingerprints."""

    if outcome.shot_sequence != shot_sequence:
        raise ValueError("Veo agent outcome does not match the requested source shot")
    updates: dict = {"shot_fingerprint": expected_fingerprint}
    if outcome.result is not None:
        seed = outcome.result.seed.model_copy(update={"shot_fingerprint": expected_fingerprint})
        updates["result"] = outcome.result.model_copy(
            update={"shot_fingerprint": expected_fingerprint, "seed": seed}
        )
    if outcome.failure is not None:
        updates["failure"] = outcome.failure.model_copy(
            update={"shot_fingerprint": expected_fingerprint}
        )
    return outcome.model_copy(update=updates)


async def run_veo_stage(settings: Settings, manifest: RunManifest) -> int:
    """Generate only VEO-mode shots and atomically record approved clips."""

    try:
        visual_plan = VisualPlanContract.model_validate_json(
            VISUAL_PLAN_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("veo_agent", "VisualPlanContract", f"No validated visual plan available: {exc}")

    veo_shots = [shot for shot in visual_plan.shots if shot.generation_mode == "VEO"]
    if not veo_shots:
        print("veo_agent skipped: visual plan contains no VEO-mode shots")
        return 0

    try:
        production_assets = load_production_assets(PRODUCTION_ASSETS_FILE)
    except (ValueError, OSError) as exc:
        return _halt(
            "veo_agent",
            "ProductionAssetsContract",
            f"Existing production asset manifest is invalid; refusing to reset it: {exc}",
            partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE)],
        )

    shots_by_sequence = {shot.sequence: shot for shot in visual_plan.shots}
    for key, entry in production_assets.shots.items():
        current_shot = shots_by_sequence.get(entry.shot_sequence)
        if current_shot is None:
            return _halt(
                "veo_agent",
                "ProductionAssetsContract",
                f"Production asset {key} references a shot absent from the current visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE)],
            )
        if entry.shot_fingerprint != shot_fingerprint(current_shot):
            return _halt(
                "veo_agent",
                "ProductionAssetsContract",
                f"Production asset for shot {entry.shot_sequence} is stale for the current visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE), entry.local_path],
            )
        if entry.generation_mode != current_shot.generation_mode:
            return _halt(
                "veo_agent",
                "ProductionAssetsContract",
                f"Production asset mode for shot {entry.shot_sequence} no longer matches the visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE), entry.local_path],
            )

    pending = [
        shot for shot in veo_shots
        if str(shot.sequence) not in production_assets.shots
    ]
    if not pending:
        print(f"veo_agent skipped: all {len(veo_shots)} VEO shots have matching approved assets")
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
            "veo_agent",
            "VeoStageInputs",
            f"Validated analyzed assets and subtitle cues are required: {exc}",
        )

    assets_by_id = {asset.asset_id: asset for asset in analyzed.assets}
    cues_by_id = {cue.cue_id: cue.text for cue in subtitle_cues.cues}
    settings_payload = _veo_settings_payload(settings)
    reserved_external_cost = settings.image_call_cost_usd + settings.veo_call_cost_usd

    for shot in pending:
        asset = next((assets_by_id[asset_id] for asset_id in shot.source_asset_ids if asset_id in assets_by_id), None)
        if asset is None:
            return _halt(
                "veo_agent",
                "AnalyzedAssetsContract",
                f"Shot {shot.sequence} has no analyzed source asset",
            )
        subtitle_text = " ".join(
            cues_by_id[cue_id] for cue_id in shot.primary_subtitle_cue_ids if cue_id in cues_by_id
        ).strip()
        stage_id = f"veo_agent_shot_{shot.sequence}"
        count = manifest.iteration_counts.get(stage_id, 0)
        if type(count) is not int or count < 0:
            return _halt("veo_agent", "RunManifest", f"Invalid iteration count for {stage_id}")
        correction = ""
        previous_outcome: object = None
        attempt_paths: list[str] = [
            str(path)
            for path in sorted(RUN_STATE_DIR.glob(f"veo_agent_shot_{shot.sequence}_attempt_*.json"))
        ]

        while count < settings.max_veo_attempts:
            remaining_budget = settings.max_budget_usd - manifest.budget_spent_usd
            if remaining_budget <= reserved_external_cost:
                # Not enough left for this clip. Running out of budget is a
                # resource fact, not a defect in the shot, so the shot falls
                # back to a still rather than halting the whole reel -- it
                # still has `still_motion`, and `run_stills_stage` re-reads the
                # plan from disk and picks it up. Retry-ceiling and
                # non-retryable Veo failures still halt: those signal a real
                # seed/QA problem worth a human look.
                _demote_veo_shot_to_still(
                    shot.sequence,
                    f"${remaining_budget:.4f} remains, ${reserved_external_cost:.4f} must be "
                    "reserved before paid image/Veo calls",
                )
                break
            count += 1
            manifest.iteration_counts[stage_id] = count
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            try:
                agent_result = await generate_veo_asset(
                    shot=shot.model_dump(mode="json"),
                    asset=asset.model_dump(mode="json"),
                    subtitle_text=subtitle_text,
                    settings=settings_payload,
                    attempt=count,
                    max_budget_usd=remaining_budget - reserved_external_cost,
                    correction=correction,
                    previous_outcome=previous_outcome,
                )
            except Exception as exc:
                # Tool execution may have reached a paid boundary before the
                # SDK/agent surfaced an exception, and up to the full Claude
                # budget ceiling given to this attempt (remaining_budget -
                # reserved_external_cost) may also have been spent on tokens
                # with no cost value ever returned. No real cost is available
                # in this branch, so charge the whole per-attempt allotment
                # (remaining_budget) as the conservative worst case rather
                # than only the reserved external cost.
                charged_cost = remaining_budget
                manifest.budget_spent_usd += charged_cost
                attempt_path = RUN_STATE_DIR / f"veo_agent_shot_{shot.sequence}_attempt_{count}.json"
                _atomic_write_json(attempt_path, {
                    "status": "EXCEPTION",
                    "error": f"{type(exc).__name__}: {exc}",
                    "charged_cost_usd": charged_cost,
                })
                attempt_paths.append(str(attempt_path))
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return _halt(
                    "veo_agent",
                    "VeoOutcomeContract",
                    "Veo agent ended without an accountable result after tools may have run: "
                    f"{type(exc).__name__}: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                )

            claude_cost = agent_result.total_cost_usd
            if claude_cost is None or not math.isfinite(claude_cost) or claude_cost < 0:
                return _halt(
                    "veo_agent", "Budget", "Veo agent returned no usable Claude cost",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                )
            try:
                if agent_result.is_error:
                    raise ValueError(
                        f"Claude failed ({agent_result.subtype}): {agent_result.errors or agent_result.result}"
                    )
                outcome = VeoOutcomeContract.model_validate(agent_result.structured_output)
                expected_fingerprint = shot_fingerprint(shot)
                outcome = _stamp_veo_outcome_fingerprint(
                    outcome,
                    shot_sequence=shot.sequence,
                    expected_fingerprint=expected_fingerprint,
                )
                if outcome.status == "SUCCESS" and outcome.result.seed.source_asset_id not in shot.source_asset_ids:
                    raise ValueError("Veo agent outcome seed source asset is not cited by the requested shot")
                if outcome.cost_usd > reserved_external_cost + 1e-9:
                    raise ValueError("Veo agent reported paid-call cost above the configured per-attempt ceiling")
            except ValueError as exc:
                # The agent cost is known, but a malformed response cannot safely
                # prove which external paid calls occurred. Charge the reserved
                # ceiling conservatively and halt instead of retrying blindly.
                manifest.budget_spent_usd += claude_cost + reserved_external_cost
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
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return _halt(
                    "veo_agent",
                    "VeoOutcomeContract",
                    f"Invalid Veo agent outcome: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                )

            manifest.budget_spent_usd += claude_cost + outcome.cost_usd
            manifest.session_id = agent_result.session_id
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            attempt_path = RUN_STATE_DIR / f"veo_agent_shot_{shot.sequence}_attempt_{count}.json"
            _atomic_write_json(attempt_path, {
                "structured_output": outcome.model_dump(mode="json"),
                "result": agent_result.result,
                "subtype": agent_result.subtype,
                "errors": agent_result.errors,
                "claude_cost_usd": claude_cost,
                "paid_tool_cost_usd": outcome.cost_usd,
            })
            attempt_paths.append(str(attempt_path))
            previous_outcome = outcome.model_dump(mode="json")

            budget_failure = _check_budget(settings, manifest.budget_spent_usd)
            if budget_failure:
                return _halt(
                    "veo_agent", "Budget", budget_failure.reason or "Budget exhausted",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                )

            if outcome.status == "SUCCESS":
                try:
                    entry = ProductionAssetEntry.from_veo_result(
                        outcome.result,
                        source_asset_ids=shot.source_asset_ids,
                    )
                    upsert_production_asset(
                        entry,
                        path=PRODUCTION_ASSETS_FILE,
                        writer="orchestrator",
                    )
                except Exception as exc:
                    return _halt(
                        "veo_agent",
                        "ProductionAssetsContract",
                        f"Approved Veo result could not be persisted: {type(exc).__name__}: {exc}",
                        attempt_count=count,
                        partial_artifact_paths=attempt_paths,
                    )
                print(f"veo_agent approved shot {shot.sequence}: {entry.local_path}")
                break

            failure = outcome.failure
            correction = failure.reason
            if not failure.retryable:
                return _halt(
                    "veo_agent",
                    "VeoFailureContract",
                    failure.reason,
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths + failure.partial_artifact_paths,
                )
            print(
                f"veo_agent shot {shot.sequence} attempt {count}/{settings.max_veo_attempts} "
                f"failed: {failure.reason}",
                file=sys.stderr,
            )
        else:
            reason = "Veo retry ceiling exhausted"
            last_failure_paths: list[str] = []
            if isinstance(previous_outcome, dict):
                last_failure = previous_outcome.get("failure") or {}
                reason = last_failure.get("reason", reason)
                last_failure_paths = last_failure.get("partial_artifact_paths", [])
            return _halt(
                "veo_agent",
                "VeoFailureContract",
                reason,
                attempt_count=count,
                partial_artifact_paths=attempt_paths + last_failure_paths,
            )

    return 0


def _stills_settings_payload(settings: Settings) -> dict:
    return {
        "project_id": settings.project_id,
        "location": settings.location,
        "image_model": settings.image_model,
        "image_call_cost_usd": settings.image_call_cost_usd,
    }


async def run_stills_stage(settings: Settings, manifest: RunManifest) -> int:
    """Generate only STILL-mode shots and atomically record approved stills.

    Mirrors `run_veo_stage`'s structure exactly, minus the seed stage: one
    Gemini image-edit call per shot, no GCS/video concept, no data
    dependency on the VEO shots (disjoint shot sets).
    """

    try:
        visual_plan = VisualPlanContract.model_validate_json(
            VISUAL_PLAN_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt("stills_agent", "VisualPlanContract", f"No validated visual plan available: {exc}")

    still_shots = [shot for shot in visual_plan.shots if shot.generation_mode == "STILL"]
    if not still_shots:
        print("stills_agent skipped: visual plan contains no STILL-mode shots")
        return 0

    try:
        production_assets = load_production_assets(PRODUCTION_ASSETS_FILE)
    except (ValueError, OSError) as exc:
        return _halt(
            "stills_agent",
            "ProductionAssetsContract",
            f"Existing production asset manifest is invalid; refusing to reset it: {exc}",
            partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE)],
        )

    shots_by_sequence = {shot.sequence: shot for shot in visual_plan.shots}
    for key, entry in production_assets.shots.items():
        current_shot = shots_by_sequence.get(entry.shot_sequence)
        if current_shot is None:
            return _halt(
                "stills_agent",
                "ProductionAssetsContract",
                f"Production asset {key} references a shot absent from the current visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE)],
            )
        if entry.shot_fingerprint != shot_fingerprint(current_shot):
            return _halt(
                "stills_agent",
                "ProductionAssetsContract",
                f"Production asset for shot {entry.shot_sequence} is stale for the current visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE), entry.local_path],
            )
        if entry.generation_mode != current_shot.generation_mode:
            return _halt(
                "stills_agent",
                "ProductionAssetsContract",
                f"Production asset mode for shot {entry.shot_sequence} no longer matches the visual plan",
                partial_artifact_paths=[str(PRODUCTION_ASSETS_FILE), entry.local_path],
            )

    pending = [
        shot for shot in still_shots
        if str(shot.sequence) not in production_assets.shots
    ]
    if not pending:
        print(f"stills_agent skipped: all {len(still_shots)} STILL shots have matching approved assets")
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
            "stills_agent",
            "StillsStageInputs",
            f"Validated analyzed assets and subtitle cues are required: {exc}",
        )

    assets_by_id = {asset.asset_id: asset for asset in analyzed.assets}
    cues_by_id = {cue.cue_id: cue.text for cue in subtitle_cues.cues}
    settings_payload = _stills_settings_payload(settings)
    reserved_external_cost = settings.image_call_cost_usd

    for shot in pending:
        asset = next((assets_by_id[asset_id] for asset_id in shot.source_asset_ids if asset_id in assets_by_id), None)
        if asset is None:
            return _halt(
                "stills_agent",
                "AnalyzedAssetsContract",
                f"Shot {shot.sequence} has no analyzed source asset",
            )
        subtitle_text = " ".join(
            cues_by_id[cue_id] for cue_id in shot.primary_subtitle_cue_ids if cue_id in cues_by_id
        ).strip()
        stage_id = f"stills_agent_shot_{shot.sequence}"
        count = manifest.iteration_counts.get(stage_id, 0)
        if type(count) is not int or count < 0:
            return _halt("stills_agent", "RunManifest", f"Invalid iteration count for {stage_id}")
        correction = ""
        previous_outcome: object = None
        attempt_paths: list[str] = [
            str(path)
            for path in sorted(RUN_STATE_DIR.glob(f"stills_agent_shot_{shot.sequence}_attempt_*.json"))
        ]

        while count < MAX_STILLS_ATTEMPTS:
            remaining_budget = settings.max_budget_usd - manifest.budget_spent_usd
            if remaining_budget <= reserved_external_cost:
                return _halt(
                    "stills_agent",
                    "Budget",
                    f"Insufficient budget for shot {shot.sequence}: ${remaining_budget:.6f} remains, "
                    f"${reserved_external_cost:.6f} must be reserved before the paid image call",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                )
            count += 1
            manifest.iteration_counts[stage_id] = count
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            try:
                agent_result = await generate_still_asset(
                    shot=shot.model_dump(mode="json"),
                    asset=asset.model_dump(mode="json"),
                    subtitle_text=subtitle_text,
                    settings=settings_payload,
                    attempt=count,
                    max_budget_usd=remaining_budget - reserved_external_cost,
                    correction=correction,
                    previous_outcome=previous_outcome,
                )
            except Exception as exc:
                # Same conservative accounting as run_veo_stage: no real cost
                # is available on this branch, so charge the whole
                # per-attempt allotment rather than only the reserved
                # external cost.
                charged_cost = remaining_budget
                manifest.budget_spent_usd += charged_cost
                attempt_path = RUN_STATE_DIR / f"stills_agent_shot_{shot.sequence}_attempt_{count}.json"
                _atomic_write_json(attempt_path, {
                    "status": "EXCEPTION",
                    "error": f"{type(exc).__name__}: {exc}",
                    "charged_cost_usd": charged_cost,
                })
                attempt_paths.append(str(attempt_path))
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return _halt(
                    "stills_agent",
                    "StillOutcomeContract",
                    "Stills agent ended without an accountable result after tools may have run: "
                    f"{type(exc).__name__}: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                )

            claude_cost = agent_result.total_cost_usd
            if claude_cost is None or not math.isfinite(claude_cost) or claude_cost < 0:
                return _halt(
                    "stills_agent", "Budget", "Stills agent returned no usable Claude cost",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                )
            try:
                if agent_result.is_error:
                    raise ValueError(
                        f"Claude failed ({agent_result.subtype}): {agent_result.errors or agent_result.result}"
                    )
                outcome = StillOutcomeContract.model_validate(agent_result.structured_output)
                expected_fingerprint = shot_fingerprint(shot)
                outcome = _stamp_still_outcome_fingerprint(
                    outcome,
                    shot_sequence=shot.sequence,
                    expected_fingerprint=expected_fingerprint,
                )
                if outcome.status == "SUCCESS" and outcome.result.source_asset_id not in shot.source_asset_ids:
                    raise ValueError("Stills agent outcome source asset is not cited by the requested shot")
                if outcome.cost_usd > reserved_external_cost + 1e-9:
                    raise ValueError("Stills agent reported paid-call cost above the configured per-attempt ceiling")
            except ValueError as exc:
                # The agent cost is known, but a malformed response cannot safely
                # prove which external paid calls occurred. Charge the reserved
                # ceiling conservatively and halt instead of retrying blindly.
                manifest.budget_spent_usd += claude_cost + reserved_external_cost
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
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return _halt(
                    "stills_agent",
                    "StillOutcomeContract",
                    f"Invalid stills agent outcome: {exc}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths,
                )

            manifest.budget_spent_usd += claude_cost + outcome.cost_usd
            manifest.session_id = agent_result.session_id
            save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
            attempt_path = RUN_STATE_DIR / f"stills_agent_shot_{shot.sequence}_attempt_{count}.json"
            _atomic_write_json(attempt_path, {
                "structured_output": outcome.model_dump(mode="json"),
                "result": agent_result.result,
                "subtype": agent_result.subtype,
                "errors": agent_result.errors,
                "claude_cost_usd": claude_cost,
                "paid_tool_cost_usd": outcome.cost_usd,
            })
            attempt_paths.append(str(attempt_path))
            previous_outcome = outcome.model_dump(mode="json")

            budget_failure = _check_budget(settings, manifest.budget_spent_usd)
            if budget_failure:
                return _halt(
                    "stills_agent", "Budget", budget_failure.reason or "Budget exhausted",
                    attempt_count=count, partial_artifact_paths=attempt_paths,
                )

            if outcome.status == "SUCCESS":
                try:
                    entry = ProductionAssetEntry.from_still_result(
                        outcome.result,
                        source_asset_ids=shot.source_asset_ids,
                    )
                    upsert_production_asset(
                        entry,
                        path=PRODUCTION_ASSETS_FILE,
                        writer="orchestrator",
                    )
                except Exception as exc:
                    return _halt(
                        "stills_agent",
                        "ProductionAssetsContract",
                        f"Approved still result could not be persisted: {type(exc).__name__}: {exc}",
                        attempt_count=count,
                        partial_artifact_paths=attempt_paths,
                    )
                print(f"stills_agent approved shot {shot.sequence}: {entry.local_path}")
                break

            failure = outcome.failure
            correction = failure.reason
            if not failure.retryable:
                return _halt(
                    "stills_agent",
                    "StillFailureContract",
                    failure.reason,
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths + failure.partial_artifact_paths,
                )
            print(
                f"stills_agent shot {shot.sequence} attempt {count}/{MAX_STILLS_ATTEMPTS} "
                f"failed: {failure.reason}",
                file=sys.stderr,
            )
        else:
            reason = "Stills retry ceiling exhausted"
            last_failure_paths: list[str] = []
            if isinstance(previous_outcome, dict):
                last_failure = previous_outcome.get("failure") or {}
                reason = last_failure.get("reason", reason)
                last_failure_paths = last_failure.get("partial_artifact_paths", [])
            return _halt(
                "stills_agent",
                "StillFailureContract",
                reason,
                attempt_count=count,
                partial_artifact_paths=attempt_paths + last_failure_paths,
            )

    return 0


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

    try:
        render_response = await render_remotion.handler({
            "output_path": str(DEFAULT_REMOTION_RENDER_OUTPUT),
        })
    except Exception as exc:
        return _halt("delivery", "render_remotion", f"{type(exc).__name__}: {exc}")

    try:
        render_payload = json.loads(render_response["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return _halt(
            "delivery",
            "render_remotion",
            f"Remotion render returned an unreadable tool response: {exc}",
        )
    print(f"delivery complete: {render_payload['output_path']}")
    return 0


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
    character_references = resolve_character_references()
    if character_references:
        print(
            "character reference: "
            + ", ".join(str(path) for path in character_references)
            + " (applied only to shots whose source art already shows a person)"
        )
    else:
        print("character reference: none configured -- source figures kept as drawn")
    print("=== stage: screenshot understanding (asset_analyst) ===")
    screenshot_result = asyncio.run(run_screenshot_stage(args.source_images_dir, settings, manifest))
    if screenshot_result != 0:
        return screenshot_result
    print("=== stage: narration (story_agent) ===")
    narration_result = asyncio.run(run_narration_stage(settings, manifest))
    if narration_result != 0:
        return narration_result
    print("=== stage: visual plan (visual_agent) ===")
    visual_result = asyncio.run(run_visual_stage(settings, manifest))
    if visual_result != 0:
        return visual_result
    print("=== stage: voice (TTS → STT → subtitles) ===")
    voice_result = asyncio.run(run_voice_stage(settings, manifest))
    if voice_result != 0:
        return voice_result
    print("=== stage: veo generation ===")
    veo_result = asyncio.run(run_veo_stage(settings, manifest))
    if veo_result != 0:
        return veo_result
    print("=== stage: stills generation ===")
    stills_result = asyncio.run(run_stills_stage(settings, manifest))
    if stills_result != 0:
        return stills_result
    print("=== stage: delivery (timeline → remotion sync → render) ===")
    return asyncio.run(run_delivery_stage(settings, manifest))


if __name__ == "__main__":
    sys.exit(main())
