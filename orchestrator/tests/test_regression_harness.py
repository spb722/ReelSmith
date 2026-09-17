"""Tests for the reference-reel regression harness (Story 2.1).

`remotion/out/book_reel_v1.mp4` is gitignored/untracked (Code Map) -- these
tests skip rather than fail when it isn't present locally (e.g. a fresh
checkout that hasn't run the manual render pipeline), since the harness
itself is explicitly a one-time proof against that existing reference
render, not a per-run production gate (Boundaries), and CI/fresh clones
should not hard-fail on a file that was never meant to be committed.

AC3 ("the current hand-authored renderer... a fail/inconclusive result is
acceptable") is a design property of the harness (it must be able to fail),
not a claim that the current renderer is exercised here -- Story 2.2 (the
data-driven renderer rewrite) doesn't exist yet, so there is no way to
render `timeline.json` through anything yet; that property is instead
verified directly, by asserting the harness reports FAIL against a
deliberately mutated fixture/measurement.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from orchestrator.tools.regression_harness import (
    compare_to_fixture,
    frame_centre_index,
    hamming_distance,
    run_regression_harness,
)

REFERENCE_VIDEO = Path("remotion/out/book_reel_v1.mp4")
# Story 2.2's own data-driven renderer output -- not committed (same reason
# as REFERENCE_VIDEO: a rendered .mp4 is gitignored), produced by `npx
# remotion render BookReel out/book_reel_v2.mp4` from `remotion/`.
NEW_RENDERER_VIDEO = Path("remotion/out/book_reel_v2.mp4")
FIXTURE_FILE = Path("orchestrator/tests/fixtures/reference_reel_metrics.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")

requires_reference_video = pytest.mark.skipif(
    not REFERENCE_VIDEO.exists(),
    reason="remotion/out/book_reel_v1.mp4 is gitignored and not present in this checkout",
)
requires_new_renderer_video = pytest.mark.skipif(
    not NEW_RENDERER_VIDEO.exists(),
    reason="remotion/out/book_reel_v2.mp4 is gitignored and not present in this checkout",
)
requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def load_cues() -> list[dict]:
    cues = json.loads(SUBTITLE_CUES_FILE.read_text(encoding="utf-8"))["cues"]
    return [
        {"cue_id": cue["cue_id"], "start_seconds": cue["start_seconds"], "end_seconds": cue["end_seconds"]}
        for cue in cues
    ]


def load_fixture() -> dict:
    return json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))


def test_fixture_has_24_cue_centre_phashes():
    fixture = load_fixture()
    cues = load_cues()
    assert len(cues) == 24
    assert set(fixture["cue_centre_phashes"]) == {cue["cue_id"] for cue in cues}


def test_fixture_matches_known_reference_metrics():
    # Code Map's own confirmed ffprobe reading of the reference reel.
    fixture = load_fixture()
    assert fixture["width"] == 1080
    assert fixture["height"] == 1920
    assert fixture["fps"] == 30.0
    assert fixture["frame_count"] == 1573


def test_frame_centre_index_uses_floor_of_midpoint():
    # Design Notes: floor(midpoint(start, end) * fps), never rounded.
    cue = {"start_seconds": 0.2, "end_seconds": 2.59}
    midpoint = (0.2 + 2.59) / 2
    assert frame_centre_index(cue, fps=30.0) == int(midpoint * 30.0)  # 41, exact here
    # A cue whose midpoint*fps is not an integer must floor, not round.
    cue_fractional = {"start_seconds": 0.0, "end_seconds": 0.101}  # midpoint 0.0505
    assert frame_centre_index(cue_fractional, fps=30.0) == 1  # floor(1.515) == 1, not round(1.515)==2


def test_hamming_distance_of_identical_hashes_is_zero():
    assert hamming_distance(0xABCDEF, 0xABCDEF) == 0


def test_hamming_distance_counts_differing_bits():
    assert hamming_distance(0b0000, 0b1111) == 4


@requires_reference_video
@requires_ffmpeg
def test_harness_passes_against_the_reference_reel_itself():
    """AC2/Tasks & Acceptance: the harness run against the exact render the
    fixture was computed from must pass on every metric (duration/fps/
    resolution/frame count exact, all 24 cue-centre frames within
    threshold) -- proof the fixture and comparison logic agree with
    themselves before ever being trusted against a different render.
    """
    result = compare_to_fixture(REFERENCE_VIDEO, load_cues(), load_fixture())
    failed = [check for check in result["checks"] if not check["passed"]]
    assert result["passed"], f"Unexpected failing checks: {failed}"
    assert len(result["checks"]) == 24 + 7  # 24 phash checks + 7 metadata/audio checks


@requires_new_renderer_video
@requires_ffmpeg
def test_new_data_driven_renderer_output_passes_regression_against_reference_fixture():
    """Story 2.2's own acceptance criterion (I/O & Edge-Case Matrix's happy
    path / Acceptance Criteria): the new data-driven renderer's own output
    (`timeline.ts`/`BookReel.tsx`/`Composition.tsx` rendering the
    reconstructed `timeline.json`) must independently pass this same
    regression harness against the same reference-reel fixture the old,
    hand-authored renderer was measured against -- not merely proven by hand
    once, but wired into `pytest` so a future regression here is caught.
    """
    result = compare_to_fixture(NEW_RENDERER_VIDEO, load_cues(), load_fixture())
    failed = [check for check in result["checks"] if not check["passed"]]
    assert result["passed"], f"Unexpected failing checks: {failed}"
    assert len(result["checks"]) == 24 + 7  # 24 phash checks + 7 metadata/audio checks


@requires_reference_video
@requires_ffmpeg
def test_harness_tool_handler_matches_direct_call():
    cues = load_cues()
    direct = compare_to_fixture(REFERENCE_VIDEO, cues, load_fixture())
    response = asyncio.run(run_regression_harness.handler({
        "video_path": str(REFERENCE_VIDEO), "cues": cues, "fixture_path": str(FIXTURE_FILE),
    }))
    via_tool = json.loads(response["content"][0]["text"])
    assert via_tool == direct
    assert via_tool["passed"] is True


@requires_reference_video
@requires_ffmpeg
def test_harness_reports_a_clear_per_metric_diagnostic_on_mismatch():
    """I/O & Edge-Case Matrix's "Regression mismatch" row: a diverging
    metric must produce a clear pass/fail diagnostic naming which metric
    failed and its expected vs. actual value -- not a bare boolean. Also the
    concrete proof that the harness can fail at all (AC3's underlying
    property).
    """
    mutated_fixture = dict(load_fixture())
    mutated_fixture["frame_count"] = mutated_fixture["frame_count"] + 1
    mutated_fixture["fps"] = 24.0  # also wrong, on purpose
    result = compare_to_fixture(REFERENCE_VIDEO, load_cues(), mutated_fixture)

    assert result["passed"] is False
    by_metric = {check["metric"]: check for check in result["checks"]}
    assert by_metric["frame_count"]["passed"] is False
    assert by_metric["frame_count"]["expected"] == mutated_fixture["frame_count"]
    assert by_metric["frame_count"]["actual"] == 1573
    assert by_metric["fps"]["passed"] is False
    assert by_metric["fps"]["expected"] == 24.0
    assert by_metric["fps"]["actual"] == 30.0
    # Everything not deliberately mutated must still independently pass.
    assert by_metric["width"]["passed"] is True
    assert by_metric["height"]["passed"] is True


@requires_reference_video
@requires_ffmpeg
def test_harness_reports_phash_hamming_distance_over_threshold_as_failure():
    fixture = dict(load_fixture())
    fixture["cue_centre_phashes"] = dict(fixture["cue_centre_phashes"])
    real_hash = fixture["cue_centre_phashes"]["cue_001"]
    # Flip every bit -- guaranteed Hamming distance of 64, far past the
    # default threshold of 5.
    flipped = format(int(real_hash, 16) ^ ((1 << 64) - 1), "016x")
    fixture["cue_centre_phashes"]["cue_001"] = flipped

    result = compare_to_fixture(REFERENCE_VIDEO, load_cues(), fixture)
    assert result["passed"] is False
    phash_checks = {check["metric"]: check for check in result["checks"] if check["metric"].startswith("phash[cue_001]")}
    (check,) = phash_checks.values()
    assert check["passed"] is False
    assert check["hamming_distance"] == 64
