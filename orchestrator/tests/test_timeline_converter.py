"""Unit tests for the deterministic `timeline.json` converter (Story 2.1),
run against the reference reel's real, already-backfilled `VisualPlanContract`
(`metadata/visual_plan.json`'s per-shot `start_seconds`/`end_seconds`/
`primary_subtitle_cue_ids` -- backfilled from `remotion/src/timeline.ts`
before this story, Code Map) and real `SubtitleCuesContract` cue data
(`metadata/subtitle_cues.json`).

Both source files predate these contracts (they are the legacy pipeline's
manifests, Code Map) and are missing fields those contracts require that
this story was never asked to backfill (`produced_by`, `overall_visual_style`,
per-shot `generation_mode`, `quality_review` on the visual plan;
`produced_by`, `source_narration_script` on the subtitle cues). Rather than
writing invented content into those locked, already-approved reference-reel
artifacts (AGENTS.md), the envelope fields these contracts need are filled
in here, in memory, from real reference values (the reel's own already-
approved status; its own narration script) -- the per-shot timing/cue data
itself always comes straight from the real files.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.contracts.subtitle_cues import SubtitleCuesContract
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.tools.timeline_converter import build_timeline, build_timeline_data

VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")
FINAL_STORY_PLAN_FILE = Path("metadata/final_story_plan.json")

# The reference reel's final asset paths, as hand-authored in
# `remotion/src/timeline.ts` (Code Map ground truth) -- shots 2/5/6 are Veo
# clips, every other shot is a still.
REFERENCE_SHOT_ASSETS = {
    1: "stills/shot_01.png",
    2: "video/shot_02.mp4",
    3: "stills/shot_03.png",
    4: "stills/shot_04.png",
    5: "video/shot_05.mp4",
    6: "video/shot_06.mp4",
    7: "stills/shot_07.png",
    8: "stills/shot_08.png",
}

# `remotion/src/BookReel.tsx`'s hand-authored `SHOT_TRANSITIONS`/`STILL_MOTION`
# tables -- the ground truth Story 2.2's backfill into `metadata/visual_plan.json`
# must match, keyed the same way `build_timeline_data` emits them.
SHOT_TRANSITIONS = {
    1: (0, 6), 2: (6, 6), 3: (6, 4), 4: (4, 0),
    5: (0, 4), 6: (4, 6), 7: (6, 8), 8: (8, 0),
}
STILL_MOTION = {
    1: {"scale_from": 1.0, "scale_to": 1.07, "easing": "linear"},
    3: {"scale_from": 1.02, "scale_to": 1.07, "translate_y_from": 0, "translate_y_to": -2, "easing": "ease"},
    4: {"scale_from": 1.0, "scale_to": 1.06, "translate_x_from": 0, "translate_x_to": 1.5, "easing": "ease"},
    7: {"scale_from": 1.02, "scale_to": 1.04, "translate_x_from": 0, "translate_x_to": 1.2, "easing": "ease"},
    8: {"scale_from": 1.0, "scale_to": 1.015, "easing": "easeOut"},
}

# `remotion/src/timeline.ts`'s hand-authored shot list -- the ground truth
# AC1 requires the converter's output to match.
TIMELINE_TS_SHOTS = [
    {
        "sequence": sequence, "type": shot_type, "start_seconds": start_seconds, "end_seconds": end_seconds,
        "fade_in_frames": SHOT_TRANSITIONS[sequence][0], "fade_out_frames": SHOT_TRANSITIONS[sequence][1],
        "still_motion": STILL_MOTION.get(sequence),
    }
    for sequence, shot_type, start_seconds, end_seconds in [
        (1, "still", 0.0, 12.0),
        (2, "video", 12.0, 18.5),
        (3, "still", 18.5, 24.5),
        (4, "still", 24.5, 33.8),
        (5, "video", 33.8, 38.4),
        (6, "video", 38.4, 45.0),
        (7, "still", 45.0, 49.6),
        (8, "still", 49.6, 52.44),
    ]
]


def load_reference_visual_plan() -> VisualPlanContract:
    """The real, persisted reference-reel plan, completed in memory with the
    envelope fields it predates (see module docstring) -- its real per-shot
    timing/cue-linkage data is untouched.
    """
    data = json.loads(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    data["produced_by"] = "visual_agent"
    data["overall_visual_style"] = data["visual_concept"]["overall_approach"]
    for shot in data["shots"]:
        shot["generation_mode"] = "STILL"
    data["quality_review"] = {
        "verdict": "APPROVE", "ready_for_generation": True, "confidence": 1.0,
        "scores": {
            "source_fidelity": 10, "generation_mode_appropriateness": 10,
            "visual_coherence": 10, "narrative_alignment": 10, "internal_consistency": 10,
        },
    }
    return VisualPlanContract.model_validate(data)


def load_reference_subtitle_cues() -> SubtitleCuesContract:
    data = json.loads(SUBTITLE_CUES_FILE.read_text(encoding="utf-8"))
    data["produced_by"] = "voice_agent"
    data["source_narration_script"] = json.loads(
        FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8")
    )["narration_script"]
    return SubtitleCuesContract.model_validate(data)


@pytest.fixture(scope="module")
def visual_plan() -> VisualPlanContract:
    return load_reference_visual_plan()


@pytest.fixture(scope="module")
def subtitle_cues() -> SubtitleCuesContract:
    return load_reference_subtitle_cues()


def test_reference_shots_backfilled_with_real_timing(visual_plan):
    # The named backfill task (Tasks & Acceptance) was already satisfied by
    # an earlier commit -- this asserts that fact rather than re-deriving it.
    assert len(visual_plan.shots) == len(TIMELINE_TS_SHOTS)
    for shot, expected in zip(visual_plan.shots, TIMELINE_TS_SHOTS):
        assert shot.start_seconds == expected["start_seconds"]
        assert shot.end_seconds == expected["end_seconds"]
        assert shot.primary_subtitle_cue_ids


def test_reference_shots_backfilled_with_transition_and_motion(visual_plan):
    # Story 2.2's own backfill task: fade_in_frames/fade_out_frames/
    # still_motion must match BookReel.tsx's SHOT_TRANSITIONS/STILL_MOTION
    # tables exactly -- present for stills, null for the VEO shots (2/5/6).
    for shot, expected in zip(visual_plan.shots, TIMELINE_TS_SHOTS):
        assert shot.fade_in_frames == expected["fade_in_frames"]
        assert shot.fade_out_frames == expected["fade_out_frames"]
        actual_still_motion = shot.still_motion.model_dump(exclude_none=True) if shot.still_motion else None
        assert actual_still_motion == expected["still_motion"]
        if expected["type"] == "video":
            assert shot.still_motion is None
        else:
            assert shot.still_motion is not None


def test_build_timeline_data_matches_timeline_ts(visual_plan, subtitle_cues):
    timeline = build_timeline_data(visual_plan, subtitle_cues, REFERENCE_SHOT_ASSETS)
    assert timeline["shots"] == [
        {
            "sequence": expected["sequence"],
            "type": expected["type"],
            "src": REFERENCE_SHOT_ASSETS[expected["sequence"]],
            "start_seconds": expected["start_seconds"],
            "end_seconds": expected["end_seconds"],
            "primary_subtitle_cue_ids": shot.primary_subtitle_cue_ids,
            "fade_in_frames": expected["fade_in_frames"],
            "fade_out_frames": expected["fade_out_frames"],
            "still_motion": expected["still_motion"],
        }
        for expected, shot in zip(TIMELINE_TS_SHOTS, visual_plan.shots)
    ]


def test_fade_frames_and_still_motion_pass_through(visual_plan, subtitle_cues):
    timeline = build_timeline_data(visual_plan, subtitle_cues, REFERENCE_SHOT_ASSETS)
    by_sequence = {shot["sequence"]: shot for shot in timeline["shots"]}
    for expected in TIMELINE_TS_SHOTS:
        shot = by_sequence[expected["sequence"]]
        assert shot["fade_in_frames"] == expected["fade_in_frames"]
        assert shot["fade_out_frames"] == expected["fade_out_frames"]
        if expected["type"] == "video":
            assert shot["still_motion"] is None
        else:
            assert shot["still_motion"] == expected["still_motion"]


def test_shot_type_derived_from_asset_extension(visual_plan, subtitle_cues):
    timeline = build_timeline_data(visual_plan, subtitle_cues, REFERENCE_SHOT_ASSETS)
    types_by_sequence = {shot["sequence"]: shot["type"] for shot in timeline["shots"]}
    assert types_by_sequence == {expected["sequence"]: expected["type"] for expected in TIMELINE_TS_SHOTS}


def test_primary_subtitle_cue_ids_pass_through(visual_plan, subtitle_cues):
    timeline = build_timeline_data(visual_plan, subtitle_cues, REFERENCE_SHOT_ASSETS)
    known_cue_ids = {cue.cue_id for cue in subtitle_cues.cues}
    for shot in timeline["shots"]:
        assert shot["primary_subtitle_cue_ids"]
        assert set(shot["primary_subtitle_cue_ids"]) <= known_cue_ids


def test_missing_final_asset_path_rejected(visual_plan, subtitle_cues):
    incomplete = dict(REFERENCE_SHOT_ASSETS)
    del incomplete[3]
    with pytest.raises(ValueError, match="No final asset path supplied for shot 3"):
        build_timeline_data(visual_plan, subtitle_cues, incomplete)


def test_unknown_subtitle_cue_id_rejected(visual_plan, subtitle_cues):
    mutated = copy.deepcopy(visual_plan)
    mutated.shots[0].primary_subtitle_cue_ids = ["cue_999"]
    with pytest.raises(ValueError, match="cue_999"):
        build_timeline_data(mutated, subtitle_cues, REFERENCE_SHOT_ASSETS)


def test_shot_end_not_after_start_rejected(visual_plan, subtitle_cues):
    mutated = copy.deepcopy(visual_plan)
    mutated.shots[0].end_seconds = mutated.shots[0].start_seconds
    with pytest.raises(ValueError, match="end_seconds"):
        build_timeline_data(mutated, subtitle_cues, REFERENCE_SHOT_ASSETS)


def test_gap_between_shots_rejected(visual_plan, subtitle_cues):
    mutated = copy.deepcopy(visual_plan)
    mutated.shots[1].start_seconds += 1.0  # shot 2 no longer starts where shot 1 ends
    with pytest.raises(ValueError, match="gap or overlap"):
        build_timeline_data(mutated, subtitle_cues, REFERENCE_SHOT_ASSETS)


def test_overlap_between_shots_rejected(visual_plan, subtitle_cues):
    mutated = copy.deepcopy(visual_plan)
    mutated.shots[1].start_seconds -= 1.0
    with pytest.raises(ValueError, match="gap or overlap"):
        build_timeline_data(mutated, subtitle_cues, REFERENCE_SHOT_ASSETS)


def test_build_timeline_tool_handler_round_trips_to_json(visual_plan, subtitle_cues, tmp_path):
    """Exercises the `@tool`-wrapped handler exactly as `run.py` calls other
    deterministic tools (`await tool.handler({...})`, Code Map), and proves
    the result serializes to a real `timeline.json` file and reloads intact.
    """
    args = {
        "visual_plan": visual_plan.model_dump(mode="json"),
        "subtitle_cues": subtitle_cues.model_dump(mode="json"),
        "shot_assets": {str(sequence): path for sequence, path in REFERENCE_SHOT_ASSETS.items()},
    }
    response = asyncio.run(build_timeline.handler(args))
    timeline = json.loads(response["content"][0]["text"])

    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
    reloaded = json.loads(timeline_path.read_text(encoding="utf-8"))

    assert reloaded == timeline
    assert [shot["sequence"] for shot in reloaded["shots"]] == list(range(1, 9))
    assert [shot["type"] for shot in reloaded["shots"]] == [expected["type"] for expected in TIMELINE_TS_SHOTS]


def test_visual_plan_without_backfilled_fields_still_parses_with_defaults():
    """Additive-schema guard: a `VisualPlanContract` that predates Story 2.1
    (no `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids` at all) and
    predates Story 2.2 (no `fade_in_frames`/`fade_out_frames`/`still_motion`
    either -- e.g. `visual_agent`'s own current output) must still validate --
    `SkipJsonSchema` defaults, never a required field visual_agent can't see.
    """
    data = json.loads(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    data["produced_by"] = "visual_agent"
    data["overall_visual_style"] = "placeholder"
    data["quality_review"] = {
        "verdict": "APPROVE", "ready_for_generation": True, "confidence": 1.0,
        "scores": {
            "source_fidelity": 10, "generation_mode_appropriateness": 10,
            "visual_coherence": 10, "narrative_alignment": 10, "internal_consistency": 10,
        },
    }
    for shot in data["shots"]:
        shot["generation_mode"] = "STILL"
        del shot["start_seconds"]
        del shot["end_seconds"]
        del shot["primary_subtitle_cue_ids"]
        del shot["fade_in_frames"]
        del shot["fade_out_frames"]
        del shot["still_motion"]

    contract = VisualPlanContract.model_validate(data)
    assert all(shot.start_seconds == 0.0 for shot in contract.shots)
    assert all(shot.primary_subtitle_cue_ids == [] for shot in contract.shots)
    assert all(shot.fade_in_frames == 0 for shot in contract.shots)
    assert all(shot.fade_out_frames == 0 for shot in contract.shots)
    assert all(shot.still_motion is None for shot in contract.shots)
