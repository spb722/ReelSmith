"""Orchestrator CLI entrypoint: `python -m orchestrator.run --project <name>`.

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
import shutil
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
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
    NARRATION_WAV,
    generate_narration_audio,
    resolve_character_references,
)
from orchestrator.state.voice_cache import (
    AUDIO_CACHE_FILE, discard_audio_cache, load_audio_cache, save_audio_cache,
    selected_voice_name,
)
from orchestrator.tools.veo_tools import select_clip_duration_seconds
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

    log(f"STOPPED — {reason}", error=True)
    log(f"Details of what went wrong: {path}", error=True)

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
        log(f"Try {count} of {max_attempts} · ${settings.max_budget_usd - manifest.budget_spent_usd:.2f} left to spend")
        try:
            with heartbeat("still working"):
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
                log(
                    f"Done in {format_duration(time.monotonic() - attempt_started)} · "
                    f"cost ${manifest.budget_spent_usd - spend_before:.2f} · saved {persisted_file}"
                )
                return 0

        log(f"Try {count} of {max_attempts} didn't work: {feedback}", error=True)

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
    log(f"Shot {sequence}: switched from video to a still picture — {reason}", error=True)


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
    else:
        budget_failure = _check_budget(settings, budget_spent_usd)
        if budget_failure:
            return result("error_max_budget_usd", budget_failure.reason)
        try:
            log(f"Part 1 of 3: reading the script out loud (voice: {voice_name})")
            tts_response = await generate_narration_audio.handler({
                "narration_script": narration_script, "voice_direction": voice_direction,
                "project_id": settings.project_id, "location": settings.location,
            })
            tts_payload = json.loads(tts_response["content"][0]["text"])
            duration = float(tts_payload["duration_seconds"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("TTS returned an invalid duration")
            tts_cost = round(
                tts_payload["prompt_word_count"] * GEMINI_TTS_INPUT_TOKEN_COST_USD
                + duration * GEMINI_TTS_AUDIO_TOKENS_PER_SECOND * GEMINI_TTS_OUTPUT_TOKEN_COST_USD, 6,
            )
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
        stt_response = await extract_word_timing.handler({
            "audio_path": tts_payload["audio_path"], "narration_script": narration_script,
            "project_id": settings.project_id,
            "diagnostics_path": str(RUN_STATE_DIR / "word_timing_diagnostics.json"),
        })
        stt_payload = json.loads(stt_response["content"][0]["text"])
        stt_cost = round(stt_payload.get("attempted_audio_seconds", audio_duration_seconds) * STT_COST_PER_SECOND_USD, 6)
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
        dp_response = await build_subtitle_cues.handler({
            "word_timing": stt_payload, "narration_script": narration_script,
            "voice_direction": voice_direction,
            "scenes": [scene.model_dump(mode="json") for scene in story_plan.scenes],
            "viewer_reflection": story_plan.story_arc.viewer_reflection,
        })
        dp_payload = json.loads(dp_response["content"][0]["text"])
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
        log("This reel needs no video clips — skipping")
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
        log(f"All {len(veo_shots)} video clip(s) already made — skipping")
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
    # Ceilings come from the project's config.json and nowhere else, so there
    # is one file to edit when a stage needs more room.
    max_veo_attempts = manifest.attempts_allowed("veo_agent")

    for shot_index, shot in enumerate(pending, start=1):
        log(f"Video clip {shot_index} of {len(pending)} — shot {shot.sequence}")
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
                    settings={**settings_payload, "seed_prompt_level": seed_prompt_level},
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
                # Which rung drew this seed. A resume needs the rung that was
                # refused, not how many refusals there were: three rejections
                # of the same wording mean "try the next rung", not "skip three".
                "seed_prompt_level": seed_prompt_level,
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
                log(f"Shot {shot.sequence}: video clip approved → {entry.local_path}", indent=2)
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
        "image_provider": settings.image_provider,
        "codex_timeout_seconds": settings.codex_image_timeout_seconds,
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
        log("This reel needs no still pictures — skipping")
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
        log(f"All {len(still_shots)} still picture(s) already made — skipping")
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
    max_stills_attempts = manifest.attempts_allowed("stills_agent")

    for shot_index, shot in enumerate(pending, start=1):
        log(f"Picture {shot_index} of {len(pending)} — shot {shot.sequence}")
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

        while count < max_stills_attempts:
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
                log(f"Shot {shot.sequence}: picture approved → {entry.local_path}", indent=2)
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
            log(
                f"Shot {shot.sequence}: try {count} of {max_stills_attempts} "
                f"didn't work — {failure.reason}",
                error=True,
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
    steps = [
        ("Writing the narration", run_narration_stage),
        ("Planning the shots", run_visual_stage),
        ("Recording the voice and building subtitles", run_voice_stage),
        ("Making the moving video clips", run_veo_stage),
        ("Making the still pictures", run_stills_stage),
        ("Putting the final video together", run_delivery_stage),
    ]
    total = len(steps) + 1
    banner(f"Step 1 of {total} — Looking at your screenshots")
    started = time.monotonic()
    result = await run_screenshot_stage(source_images_dir, settings, manifest)
    if result:
        return result
    log(f"Step 1 took {format_duration(time.monotonic() - started)}")
    for index, (name, stage) in enumerate(steps, start=2):
        banner(f"Step {index} of {total} — {name}")
        started = time.monotonic()
        result = await stage(settings, manifest, retry_voice=True) if stage is run_voice_stage and retry_voice else await stage(settings, manifest)
        if result:
            return result
        log(f"Step {index} took {format_duration(time.monotonic() - started)} · ${manifest.budget_spent_usd:.2f} spent so far")
    return 0


if __name__ == "__main__":
    sys.exit(main())
