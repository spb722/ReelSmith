"""Deterministic `timeline.json` converter (Story 2.1). Combines a validated
`VisualPlanContract`'s per-shot timing/cue linkage (`start_seconds`,
`end_seconds`, `primary_subtitle_cue_ids` -- this story's additive `Shot`
fields, Code Map) with a validated `SubtitleCuesContract` (used only to
confirm every cue id a shot cites actually exists -- never to re-derive or
re-judge timing) and each shot's already-selected final asset path, and
produces `timeline.json`'s shot list. Purely deterministic: no LLM/agent
call, no re-judgment of its inputs -- `@tool`-wrapped and invoked via
`.handler(...)` per `run.py`'s convention (Code Map), never an
`AgentDefinition`.

Output schema is intentionally minimal: every shot field is traceable
1:1 to a `VisualPlanContract.Shot` field or the caller-supplied final asset
path (`type` is derived from that path's extension) -- never invented
independently (Boundaries). `fps`/resolution are Remotion composition-level
constants owned by Story 2.2's renderer, not shot data, so they are not
emitted here.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from orchestrator.contracts.subtitle_cues import SubtitleCuesContract
from orchestrator.contracts.visual_plan import VisualPlanContract

# Matches `remotion/src/timeline.ts`'s `ShotType` ("still" | "video"). Any
# other extension is treated as a still image.
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm"})


def shot_type_for_asset(asset_path: str) -> str:
    return "video" if Path(asset_path).suffix.lower() in VIDEO_EXTENSIONS else "still"


def build_timeline_data(
    visual_plan: VisualPlanContract,
    subtitle_cues: SubtitleCuesContract,
    shot_assets: dict[int, str],
) -> dict:
    """Deterministically derive `timeline.json`'s shot list.

    Raises `ValueError` (never silently drops or guesses) when: a shot has
    no supplied final asset path, a shot cites a subtitle cue id that does
    not exist in `subtitle_cues`, a shot's own end is not after its own
    start, or consecutive shots don't butt up against each other (a gap or
    overlap in the reel's timeline).
    """
    known_cue_ids = {cue.cue_id for cue in subtitle_cues.cues}
    shots_out: list[dict] = []
    previous_end: float | None = None

    for shot in visual_plan.shots:
        if shot.sequence not in shot_assets:
            raise ValueError(f"No final asset path supplied for shot {shot.sequence}.")

        unknown_cues = sorted(set(shot.primary_subtitle_cue_ids) - known_cue_ids)
        if unknown_cues:
            raise ValueError(
                f"Shot {shot.sequence} references subtitle cue id(s) not present in "
                f"SubtitleCuesContract: {unknown_cues}"
            )

        if shot.start_seconds == 0.0 and shot.end_seconds == 0.0:
            # The `SkipJsonSchema` default for both fields at once (Story
            # 2.1) -- almost certainly an un-backfilled VisualPlanContract,
            # not a genuine zero-duration shot. Named distinctly from the
            # generic end<=start check below, which this case would
            # otherwise also trigger with a far less obvious message.
            raise ValueError(
                f"Shot {shot.sequence} has no real timing yet (start_seconds and "
                "end_seconds are both 0.0, the SkipJsonSchema default) -- has this "
                "VisualPlanContract been backfilled with real per-shot timing (Story 2.1)?"
            )

        if shot.end_seconds <= shot.start_seconds:
            raise ValueError(
                f"Shot {shot.sequence} has end_seconds ({shot.end_seconds}) <= "
                f"start_seconds ({shot.start_seconds})."
            )

        if previous_end is not None and abs(shot.start_seconds - previous_end) > 1e-6:
            raise ValueError(
                f"Shot {shot.sequence} does not start where the previous shot ended "
                f"(gap or overlap): previous end {previous_end}, this start {shot.start_seconds}."
            )
        previous_end = shot.end_seconds

        asset_path = shot_assets[shot.sequence]
        shots_out.append({
            "sequence": shot.sequence,
            "type": shot_type_for_asset(asset_path),
            "src": asset_path,
            "start_seconds": shot.start_seconds,
            "end_seconds": shot.end_seconds,
            "primary_subtitle_cue_ids": list(shot.primary_subtitle_cue_ids),
        })

    return {"shots": shots_out}


@tool(
    "build_timeline",
    "Deterministically convert a validated VisualPlanContract + SubtitleCuesContract + "
    "per-shot final asset paths into timeline.json",
    {"visual_plan": dict, "subtitle_cues": dict, "shot_assets": dict},
)
async def build_timeline(args: dict) -> dict:
    visual_plan = VisualPlanContract.model_validate(args["visual_plan"])
    subtitle_cues = SubtitleCuesContract.model_validate(args["subtitle_cues"])
    # `@tool` args round-trip through JSON, so integer shot-sequence keys
    # arrive as strings.
    shot_assets = {int(sequence): path for sequence, path in args["shot_assets"].items()}
    timeline = build_timeline_data(visual_plan, subtitle_cues, shot_assets)
    return {"content": [{"type": "text", "text": json.dumps(timeline, ensure_ascii=False)}]}


timeline_converter_server = create_sdk_mcp_server(
    name="timeline_converter", tools=[build_timeline],
)
