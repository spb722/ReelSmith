from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from orchestrator.contracts.veo import VeoSeedContract, sha256_file, shot_fingerprint
from orchestrator.contracts.visual_plan import Shot
from orchestrator.tools.gemini_tools import _image_content
import orchestrator.tools.veo_tools as veo_tools


def shot_for(sequence: int = 1) -> Shot:
    return Shot(
        sequence=sequence,
        generation_mode="VEO",
        visual_treatment="AI_VIDEO_CANDIDATE",
        source_asset_ids=["img_aaaaaaaaaaaa"],
        shot_goal="Show the source scene.",
        frame_composition="Keep the subject centered.",
        motion_plan="Use restrained motion.",
        text_overlay="",
        source_support="Direct source support.",
    )


def seed_for(shot: Shot, *, approved: bool = True) -> VeoSeedContract:
    source = Path("source_images/source.png")
    seed_path = Path("generated/veo_seeds") / f"shot_{shot.sequence:02d}_seed.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    seed_path.write_bytes(b"seed")
    return VeoSeedContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        source_asset_id=shot.source_asset_ids[0],
        source_image_path=str(source),
        source_image_sha256=sha256_file(source),
        local_path=str(seed_path),
        seed_sha256=sha256_file(seed_path),
        prompt="clean seed",
        model="image-model",
        approved=approved,
        qa_summary="Seed passed QA." if approved else "",
        cost_usd=0.04,
    )


@pytest.fixture(autouse=True)
def working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_response_inline_bytes_materialize_deterministic_mp4_and_dump(monkeypatch):
    def _fake_previews(video_path, shot_sequence, clip_duration_seconds, shown_duration_seconds=0.0):
        # the contract requires previews to live under generated/veo/previews/
        preview = veo_tools.VEO_PREVIEW_DIR / f"shot_{shot_sequence:02d}_01.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"jpeg")
        return [str(preview)]

    monkeypatch.setattr(veo_tools, "extract_video_previews", _fake_previews)
    shot = shot_for()
    operation = {
        "name": "operations/inline",
        "done": True,
        "response": {
            "generated_videos": [{"video": {"video_bytes": base64.b64encode(b"mp4-inline").decode()}}]
        },
    }
    outcome = veo_tools.inspect_completed_operation(
        operation,
        shot=shot,
        seed=seed_for(shot),
        model="veo-model",
        project_id="project",
        attempt=1,
        cost_usd=1.2,
        clip_duration_seconds=8,
    )

    assert outcome.status == "SUCCESS"
    assert Path(outcome.result.local_video_path).read_bytes() == b"mp4-inline"
    assert Path(outcome.result.operation_dump_path).is_file()


def test_result_uri_is_downloaded_and_kept_as_provenance(monkeypatch):
    def _fake_previews(video_path, shot_sequence, clip_duration_seconds, shown_duration_seconds=0.0):
        # the contract requires previews to live under generated/veo/previews/
        preview = veo_tools.VEO_PREVIEW_DIR / f"shot_{shot_sequence:02d}_01.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"jpeg")
        return [str(preview)]

    monkeypatch.setattr(veo_tools, "extract_video_previews", _fake_previews)
    shot = shot_for(2)
    operation = {
        "name": "operations/uri",
        "done": True,
        "result": {"generated_videos": [{"video": {"uri": "gs://bucket/object.mp4"}}]},
    }
    calls = []

    def download(uri: str, destination: Path, project_id: str) -> None:
        calls.append((uri, project_id))
        destination.write_bytes(b"downloaded")

    outcome = veo_tools.inspect_completed_operation(
        operation,
        shot=shot,
        seed=seed_for(shot),
        model="veo-model",
        project_id="project",
        attempt=1,
        cost_usd=1.2,
        downloader=download,
            clip_duration_seconds=8,
)

    assert calls == [("gs://bucket/object.mp4", "project")]
    assert outcome.result.gcs_uri == "gs://bucket/object.mp4"
    assert Path(outcome.result.local_video_path).read_bytes() == b"downloaded"


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        (
            {
                "name": "operations/rai",
                "done": True,
                "result": {
                    "rai_media_filtered_count": 1,
                    "rai_media_filtered_reasons": ["policy reason"],
                },
            },
            "RAI_FILTERED",
        ),
        ({"name": "operations/empty", "done": True, "response": {}}, "MALFORMED_OUTPUT"),
        (
            {"name": "operations/error", "done": True, "response": {"error": {"code": 13, "message": "failed"}}},
            "OPERATION_ERROR",
        ),
    ],
)
def test_completed_failures_are_structured_and_preserve_full_operation(operation, code):
    shot = shot_for()
    outcome = veo_tools.inspect_completed_operation(
        operation,
        shot=shot,
        seed=seed_for(shot),
        model="veo-model",
        project_id="project",
        attempt=2,
        cost_usd=1.2,
        clip_duration_seconds=8,
    )

    assert outcome.status == "FAILURE"
    assert outcome.failure.code == code
    assert outcome.failure.operation_name == operation["name"]
    assert Path(outcome.failure.operation_dump_path).is_file()
    if code == "RAI_FILTERED":
        assert outcome.failure.rai_media_filtered_count == 1
        assert outcome.failure.rai_media_filtered_reasons == ["policy reason"]
    if code == "OPERATION_ERROR":
        assert outcome.failure.operation_error == [{"code": 13, "message": "failed"}]


class RefusingModels:
    def __init__(self):
        self.calls = 0

    def generate_videos(self, **kwargs):
        self.calls += 1
        raise AssertionError("unsafe seed must be rejected before the paid call")


class FakeClient:
    def __init__(self):
        self.models = RefusingModels()


class RaisingModels:
    def __init__(self, operation=None):
        self.calls = 0
        self.operation = operation

    def generate_videos(self, **kwargs):
        self.calls += 1
        if self.operation is not None:
            return self.operation
        raise RuntimeError("SDK unavailable")


class RaisingOperations:
    def get(self, operation):
        raise RuntimeError("poll unavailable")


class RaisingClient:
    def __init__(self, operation=None):
        self.models = RaisingModels(operation)
        self.operations = RaisingOperations()


def test_unapproved_seed_is_rejected_before_paid_veo_call():
    shot = shot_for()
    client = FakeClient()
    outcome = veo_tools.run_veo_generation(
        client,
        shot=shot,
        seed=seed_for(shot, approved=False),
        project_id="project",
        model="veo-model",
        gcs_output_uri="gs://bucket/prefix/",
        resolution="720p",
        duration_seconds=8,
        poll_seconds=0.01,
        max_poll_seconds=1,
        attempt=1,
        cost_usd=1.2,
    )

    assert client.models.calls == 0
    assert outcome.failure.code == "UNSAFE_SEED"
    assert outcome.failure.retryable is False
    assert outcome.failure.cost_usd == 0.0


def test_missing_seed_is_rechecked_at_paid_call_boundary():
    shot = shot_for()
    seed = seed_for(shot)
    Path(seed.local_path).unlink()
    client = FakeClient()

    outcome = veo_tools.run_veo_generation(
        client,
        shot=shot,
        seed=seed,
        project_id="project",
        model="veo-model",
        gcs_output_uri="gs://bucket/prefix/",
        resolution="720p",
        duration_seconds=8,
        poll_seconds=0.01,
        max_poll_seconds=1,
        attempt=1,
        cost_usd=1.2,
    )

    assert client.models.calls == 0
    assert outcome.failure.code == "UNSAFE_SEED"


def test_sdk_generation_exception_becomes_structured_failure():
    shot = shot_for()
    client = RaisingClient()
    outcome = veo_tools.run_veo_generation(
        client,
        shot=shot,
        seed=seed_for(shot),
        project_id="project",
        model="veo-model",
        gcs_output_uri="gs://bucket/prefix/",
        resolution="720p",
        duration_seconds=8,
        poll_seconds=0.01,
        max_poll_seconds=1,
        attempt=1,
        cost_usd=1.2,
    )
    assert outcome.failure.code == "SDK_ERROR"
    assert outcome.failure.cost_usd == 1.2


def test_sdk_poll_exception_preserves_operation_dump():
    shot = shot_for()
    client = RaisingClient({"name": "operations/poll", "done": False})
    outcome = veo_tools.run_veo_generation(
        client,
        shot=shot,
        seed=seed_for(shot),
        project_id="project",
        model="veo-model",
        gcs_output_uri="gs://bucket/prefix/",
        resolution="720p",
        duration_seconds=8,
        poll_seconds=0,
        max_poll_seconds=1,
        attempt=1,
        cost_usd=1.2,
    )
    assert outcome.failure.code == "SDK_ERROR"
    assert Path(outcome.failure.operation_dump_path).is_file()


class TimeoutModels:
    def __init__(self, operation):
        self.operation = operation

    def generate_videos(self, **kwargs):
        return self.operation


class TimeoutOperations:
    def get(self, operation):
        return operation


class TimeoutClient:
    def __init__(self, operation):
        self.models = TimeoutModels(operation)
        self.operations = TimeoutOperations()


def test_poll_deadline_produces_structured_timeout_and_preserves_dump():
    shot = shot_for()
    operation = {"name": "operations/never-done", "done": False}
    client = TimeoutClient(operation)

    outcome = veo_tools.run_veo_generation(
        client,
        shot=shot,
        seed=seed_for(shot),
        project_id="project",
        model="veo-model",
        gcs_output_uri="gs://bucket/prefix/",
        resolution="720p",
        duration_seconds=8,
        poll_seconds=0,
        max_poll_seconds=-1,
        attempt=1,
        cost_usd=1.2,
    )

    assert outcome.failure.code == "POLL_TIMEOUT"
    assert outcome.failure.retryable is True
    assert Path(outcome.failure.operation_dump_path).is_file()


def test_tool_images_use_mcp_image_content_shape():
    png = Path("seed.png")
    jpg = Path("preview.jpg")
    png.write_bytes(b"png")
    jpg.write_bytes(b"jpg")

    assert _image_content(png) == {
        "type": "image",
        "data": base64.b64encode(b"png").decode("ascii"),
        "mimeType": "image/png",
    }
    assert veo_tools._preview_content(jpg) == {
        "type": "image",
        "data": base64.b64encode(b"jpg").decode("ascii"),
        "mimeType": "image/jpeg",
    }


@pytest.mark.parametrize("shot_seconds,expected", [
    # 4.02 still takes the 4s clip: CLIP_DURATION_EPSILON absorbs a sub-frame
    # overshoot rather than paying for the next size up.
    (3.0, 4), (4.0, 4), (4.02, 4), (4.3, 6), (5.48, 6), (5.96, 6), (6.0, 6), (7.64, 8), (8.0, 8),
])
def test_select_clip_duration_picks_the_shortest_clip_that_covers_the_shot(shot_seconds, expected):
    """A 5.48s shot should not pay for 8 seconds of generated video."""
    assert veo_tools.select_clip_duration_seconds(shot_seconds, 8) == expected


def test_select_clip_duration_refuses_a_shot_longer_than_any_clip():
    """Veo makes one fixed-length clip and this pipeline never extends,
    stretches, or splits one across a shot, so such a shot cannot be video."""
    with pytest.raises(ValueError, match="longer than the longest available"):
        veo_tools.select_clip_duration_seconds(9.8, 8)


def test_preview_positions_scale_with_the_real_clip_length(tmp_path, monkeypatch):
    """QA frames were sampled at a hardcoded 0.5/2.5/5.5s, which falls outside a
    shorter clip. They now track the clip's own duration, and still land on the
    original positions for an 8s clip, plus one near the end so the last
    seconds of a clip are judged too."""
    calls: list[str] = []

    def fake_run(args, **kwargs):
        calls.append(args[args.index("-ss") + 1])
        Path(args[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(veo_tools, "resolve_binary", lambda name: name)
    monkeypatch.setattr(veo_tools.subprocess, "run", fake_run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")

    veo_tools.extract_video_previews(video, 1, 8)
    assert calls == ["0.5", "2.5", "5.5", "7.5"]

    calls.clear()
    veo_tools.extract_video_previews(video, 1, 6)
    assert calls == ["0.375", "1.875", "4.125", "5.625"]


def test_preview_positions_stay_within_the_part_of_the_clip_that_is_shown(tmp_path, monkeypatch):
    """Remotion shows only the shot's own duration of a clip: a 5.3s shot from a
    6s clip never shows 5.625s, so QA frames are sampled within the shown part.
    A shot as long as the clip, or one with unknown timing, samples as before."""
    calls: list[str] = []

    def fake_run(args, **kwargs):
        calls.append(args[args.index("-ss") + 1])
        Path(args[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(veo_tools, "resolve_binary", lambda name: name)
    monkeypatch.setattr(veo_tools.subprocess, "run", fake_run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")

    veo_tools.extract_video_previews(video, 1, 6, shown_duration_seconds=4.8)
    assert calls == ["0.3", "1.5", "3.3", "4.5"]

    for shown in (0.0, 6.0, 6.02):
        calls.clear()
        veo_tools.extract_video_previews(video, 1, 6, shown_duration_seconds=shown)
        assert calls == ["0.375", "1.875", "4.125", "5.625"]


def test_completed_clip_previews_use_the_shot_s_shown_duration(monkeypatch):
    seen: list[float] = []

    def _fake_previews(video_path, shot_sequence, clip_duration_seconds, shown_duration_seconds=0.0):
        seen.append(shown_duration_seconds)
        raise RuntimeError("stop after recording the call")

    monkeypatch.setattr(veo_tools, "extract_video_previews", _fake_previews)
    shot = shot_for().model_copy(update={"start_seconds": 2.0, "end_seconds": 7.3})
    operation = {
        "name": "operations/inline", "done": True,
        "response": {"generated_videos": [{"video": {"video_bytes": base64.b64encode(b"mp4").decode()}}]},
    }
    outcome = veo_tools.inspect_completed_operation(
        operation, shot=shot, seed=seed_for(shot), model="veo-test", project_id="project",
        attempt=1, cost_usd=0.4, clip_duration_seconds=6,
    )

    assert outcome.failure.code == "PREVIEW_EXTRACTION_FAILED"
    assert seen == [pytest.approx(5.3)]


def _prompt_shot(**overrides) -> Shot:
    fields = dict(
        sequence=1,
        generation_mode="VEO",
        visual_treatment="AI_VIDEO_CANDIDATE",
        source_asset_ids=["img_aaaaaaaaaaaa"],
        shot_goal="Deliver the reversal as a physical act.",
        frame_composition="Wide shot of a pale cream corridor with two buckets.",
        motion_plan="The figure walks away while the camera drifts back.",
        text_overlay="",
        source_support="Directly supported.",
        video_candidate_rank=1,
    )
    fields.update(overrides)
    return Shot.model_validate(fields)


def test_video_prompt_is_the_motion_only():
    """Google's image-to-video guidance: the seed already fixes subject, scene
    and style, so prompt the motion alone -- re-describing the picture confuses
    the model, and Veo's always-on rewriter builds on every extra word."""
    from orchestrator.tools.veo_tools import STATIC_CAMERA, build_video_prompt

    shot = _prompt_shot(video_motion_intent='The man lifts the "near" bucket.')
    prompt, negative_prompt = build_video_prompt(shot, VeoSeedContract.model_construct())

    # the camera is always static, stated first in Google's own term
    assert prompt.startswith(f"{STATIC_CAMERA} The man lifts the near bucket.")
    assert '"' not in prompt  # quotation marks invite rendered text
    assert shot.shot_goal not in prompt and shot.frame_composition not in prompt
    assert shot.motion_plan not in prompt  # the video intent wins when there is one
    assert "same faces throughout" in prompt
    assert "editorial illustration" not in prompt  # the seed carries the reel's own style
    assert len(prompt.split()) < 80
    for term in ("text", "extra people", "duplicate subjects", "extra limbs", "photorealistic live action",
                 "camera movement", "dolly", "zoom", "tracking shot"):
        assert term in negative_prompt


def test_video_prompt_falls_back_to_motion_plan_and_caps_the_correction():
    from orchestrator.tools.veo_tools import STATIC_CAMERA, VIDEO_CORRECTION_MAX_WORDS, build_video_prompt

    shot = _prompt_shot()
    correction = "keep the camera still " + "and re-describe the corridor " * 100
    prompt, _ = build_video_prompt(shot, VeoSeedContract.model_construct(), correction=correction)

    assert prompt.startswith(f"{STATIC_CAMERA} {shot.motion_plan}")
    adjustment = prompt.split("Adjustment from the last take: ")[1]
    assert adjustment.startswith("keep the camera still")
    assert len(adjustment.split()) <= VIDEO_CORRECTION_MAX_WORDS + len(
        "The picture stays a clean plate with no writing anywhere.".split()
    )


def test_preview_extraction_failure_is_raised_not_swallowed(tmp_path, monkeypatch):
    """A broken ffmpeg must not surface as a silently empty preview list.

    It previously did, which cost three agent attempts and halted a run with
    "preview_paths was empty" instead of naming the real cause.
    """
    import subprocess as _subprocess

    def failing_run(args, **kwargs):
        return _subprocess.CompletedProcess(args, 134, "", "dyld: Library not loaded: libintl.8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(veo_tools, "resolve_binary", lambda name: name)
    monkeypatch.setattr(veo_tools.subprocess, "run", failing_run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")

    with pytest.raises(RuntimeError, match="extracted no preview frames"):
        veo_tools.extract_video_previews(video, 1, 8)


def test_resolve_binary_skips_a_broken_candidate(tmp_path, monkeypatch):
    """PATH order is not enough: a shadowing binary that aborts at load time
    must be stepped over, which is the real Anaconda-ffmpeg situation."""
    from orchestrator.tools import deterministic_tools

    broken = tmp_path / "broken" / "ffmpeg"
    working = tmp_path / "working" / "ffmpeg"
    for path in (broken, working):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)

    monkeypatch.setattr(deterministic_tools, "_RESOLVED_BINARIES", {})
    monkeypatch.setattr(deterministic_tools.shutil, "which", lambda name: str(broken))
    monkeypatch.setattr(deterministic_tools, "FALLBACK_BINARY_DIRS", (str(working.parent),))

    import subprocess as _subprocess

    def fake_run(args, **kwargs):
        code = 134 if args[0] == str(broken) else 0
        return _subprocess.CompletedProcess(args, code, "", "")

    monkeypatch.setattr(deterministic_tools.subprocess, "run", fake_run)
    assert deterministic_tools.resolve_binary("ffmpeg") == str(working)


def test_video_prompt_overrides_a_plan_that_asks_for_text():
    """The motion reaches Veo verbatim, and a real plan said "text fades in
    softly over the closing seconds" -- which Veo obeyed, burning garbled
    pseudo-text into an otherwise clean clip."""
    from orchestrator.tools.veo_tools import build_video_prompt

    shot = _prompt_shot(
        sequence=6,
        motion_plan="Slow pull-back; text fades in softly over the closing seconds.",
        text_overlay="Happiness isn't a finish line",
    )
    prompt, negative_prompt = build_video_prompt(shot, VeoSeedContract.model_construct(), correction="redo it")

    # the clean-plate line comes after both the plan text and the correction
    assert prompt.rstrip().endswith("The picture stays a clean plate with no writing anywhere.")
    assert prompt.index("clean plate") > prompt.index("text fades in softly")
    assert prompt.index("clean plate") > prompt.index("redo it")
    assert negative_prompt.startswith("text, captions, subtitles")
    assert shot.text_overlay not in prompt


def test_the_clip_tool_draws_the_saved_plan_not_an_agent_edited_shot(monkeypatch):
    """A live agent rewrote frame_composition before each tool call; the seed
    and clip fingerprints then disagreed and the run halted. The tools now
    take the shot from the saved plan, so an edit changes nothing."""
    import json

    saved = shot_for(5)
    seed = seed_for(saved)
    Path("metadata").mkdir()
    Path("metadata/visual_plan.json").write_text(
        json.dumps({"shots": [saved.model_dump(mode="json")]}), encoding="utf-8"
    )
    edited = {**saved.model_dump(mode="json"), "frame_composition": "Much wider, camera high and far."}
    used: list[Shot] = []

    def fake_generation(client, *, shot, seed, **kwargs):
        used.append(shot)
        return veo_tools._failure(shot=shot, attempt=1, code="STOP", reason="stop", retryable=True, cost_usd=0.0)

    monkeypatch.setattr(veo_tools, "run_veo_generation", fake_generation)
    monkeypatch.setattr(veo_tools.genai, "Client", lambda **kwargs: object())
    veo_tools._generate_veo_clip({
        "shot": edited, "seed": seed.model_dump(mode="json"), "project_id": "p", "location": "global",
        "model": "veo-model", "gcs_output_uri": "gs://b/", "resolution": "720p", "duration_seconds": 8,
        "poll_seconds": 0.01, "max_poll_seconds": 1, "attempt": 1, "cost_usd": 1.2,
    })

    assert [shot.frame_composition for shot in used] == [saved.frame_composition]


def test_without_a_saved_plan_the_relayed_shot_is_used():
    from orchestrator.tools.gemini_tools import authoritative_shot

    relayed = shot_for(2).model_dump(mode="json")
    assert authoritative_shot(relayed) is relayed


def test_the_result_carries_the_measured_camera_motion_and_survives_a_broken_measure(monkeypatch):
    def _fake_previews(video_path, shot_sequence, clip_duration_seconds, shown_duration_seconds=0.0):
        preview = veo_tools.VEO_PREVIEW_DIR / f"shot_{shot_sequence:02d}_01.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"jpeg")
        return [str(preview)]

    monkeypatch.setattr(veo_tools, "extract_video_previews", _fake_previews)
    shot = shot_for()
    operation = {
        "name": "operations/inline", "done": True,
        "response": {"generated_videos": [{"video": {"video_bytes": base64.b64encode(b"mp4").decode()}}]},
    }

    def inspect():
        return veo_tools.inspect_completed_operation(
            operation, shot=shot, seed=seed_for(shot), model="veo-model", project_id="project",
            attempt=1, cost_usd=1.2, clip_duration_seconds=8,
        )

    monkeypatch.setattr(veo_tools, "measure_camera_motion",
                        lambda path: {"zoom": 1.31, "pan_x_percent": 1, "pan_y_percent": 0})
    assert "zoom x1.31" in inspect().result.camera_motion

    def broken(path):
        raise RuntimeError("ffmpeg died")

    monkeypatch.setattr(veo_tools, "measure_camera_motion", broken)
    outcome = inspect()
    assert outcome.status == "SUCCESS"
    assert "could not be measured" in outcome.result.camera_motion


# --- Veo extend: shots up to 15 s as one 8 s clip plus one 7 s extension ---

from orchestrator.agents._guard import VEO_CLIP_TOOL, _failure_billed  # noqa: E402

STATIC = {"zoom": 1.0, "pan_x_percent": 0, "pan_y_percent": 0}


def _done_op(name: str, uri: str) -> dict:
    return {"name": name, "done": True, "response": {"generated_videos": [{"video": {"uri": uri}}]}}


CLIP1_OP = _done_op("operations/clip1", "gs://bucket/out/clip1.mp4")
EXT_OP = _done_op("operations/ext", "gs://bucket/out/ext.mp4")


class ScriptedModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate_videos(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ScriptedOperations:
    def __init__(self, poll_error: bool = False):
        self.poll_error = poll_error

    def get(self, operation):
        if self.poll_error:
            raise RuntimeError("poll unavailable")
        return operation


class ScriptedClient:
    def __init__(self, responses, poll_error: bool = False):
        self.models = ScriptedModels(responses)
        self.operations = ScriptedOperations(poll_error)


def _timed_shot(seconds: float, sequence: int = 1) -> Shot:
    return shot_for(sequence).model_copy(update={"start_seconds": 0.0, "end_seconds": seconds})


@pytest.fixture
def extended_fakes(monkeypatch):
    """Fakes for everything past the Veo calls; records what they were given."""
    seen = {"measure": [], "positions": [], "stripped": []}

    def download(uri, destination, project_id):
        destination.write_bytes(f"mp4 {uri}".encode())

    def previews(video_path, shot_sequence, clip_duration_seconds, shown_duration_seconds=0.0,
                 *, positions=None):
        seen["positions"].append(positions)
        paths = []
        for index in range(1, len(positions or [0, 0, 0, 0]) + 1):
            preview = veo_tools.VEO_PREVIEW_DIR / f"shot_{shot_sequence:02d}_{index:02d}.jpg"
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes(b"jpeg")
            paths.append(str(preview))
        return paths[: len(paths) - seen.get("dropped_previews", 0)]

    def measure(path, max_seconds=None):
        seen["measure"].append((Path(path), max_seconds))
        motion = seen.get("motion", STATIC)
        if isinstance(motion, Exception):
            raise motion
        return motion

    def strip(path):
        seen["stripped"].append(Path(path))
        return seen.get("strip_error")

    monkeypatch.setattr(veo_tools, "_download_gcs", download)
    monkeypatch.setattr(veo_tools, "extract_video_previews", previews)
    monkeypatch.setattr(veo_tools, "measure_camera_motion", measure)
    monkeypatch.setattr(veo_tools, "_strip_audio_track", strip)
    monkeypatch.setattr(veo_tools, "_clip_duration_seconds", lambda path: seen.get("duration", 15.0))
    return seen


def _run(client, shot, *, extension_client=None, extend=True, resolution="720p",
         max_poll_seconds=1, correction=""):
    return veo_tools.run_veo_generation(
        client, shot=shot, seed=seed_for(shot), project_id="project", model="veo-model",
        gcs_output_uri="gs://bucket/out/", resolution=resolution, duration_seconds=8,
        poll_seconds=0, max_poll_seconds=max_poll_seconds, attempt=1, cost_usd=1.2,
        correction=correction, extend_long_shots=extend, extension_client=extension_client,
    )


def test_select_clip_plan_without_extension_is_todays_choice():
    for seconds, clip in ((3.9, 4), (6, 6), (8.04, 8)):
        for extend in (True, False):
            assert veo_tools.select_clip_plan(seconds, 8, extend=extend) == (clip, 0)
    with pytest.raises(ValueError) as today:
        veo_tools.select_clip_duration_seconds(9.8, 8)
    with pytest.raises(ValueError) as planned:
        veo_tools.select_clip_plan(9.8, 8, extend=False)
    assert str(planned.value) == str(today.value)


def test_select_clip_plan_extends_only_8_to_15s_at_720p_with_an_8s_ceiling():
    for seconds in (8.5, 10.84, 15.04):
        assert veo_tools.select_clip_plan(seconds, 8, extend=True) == (8, 7)
    with pytest.raises(ValueError, match=r"plus one 7s extension \(15s\)"):
        veo_tools.select_clip_plan(15.2, 8, extend=True)
    with pytest.raises(ValueError, match="VEO_DURATION_SECONDS"):
        veo_tools.select_clip_plan(10.84, 6, extend=True)
    with pytest.raises(ValueError, match="720p"):
        veo_tools.select_clip_plan(10.84, 8, extend=True, resolution="1080p")


def test_is_extension_length():
    assert [veo_tools.is_extension_length(d) for d in (8.0, 8.05, 8.06, 10.84, 15.05, 15.1)] == [
        False, False, True, True, True, False,
    ]


def test_extended_preview_positions_straddle_the_join():
    assert veo_tools.extended_preview_positions(10.84) == [0.5, 4.0, 7.5, 8.5, 9.42, 10.59]
    short = veo_tools.extended_preview_positions(8.3)
    assert len(short) == 5 and short == sorted(set(short)) and max(short) < 8.3
    assert short[2] < 8.0 < short[3]
    full = veo_tools.extended_preview_positions(15.0)
    assert len(full) == 6 and full[-1] == 14.75


def test_extract_video_previews_positions_override_and_default_unchanged(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_run(args, **kwargs):
        calls.append(args[args.index("-ss") + 1])
        Path(args[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(veo_tools, "resolve_binary", lambda name: name)
    monkeypatch.setattr(veo_tools.subprocess, "run", fake_run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")

    paths = veo_tools.extract_video_previews(video, 1, 15.0, shown_duration_seconds=10.84,
                                             positions=[0.5, 4.0, 7.5, 8.5, 9.42, 10.59])
    assert calls == ["0.5", "4.0", "7.5", "8.5", "9.42", "10.59"]
    assert Path(paths[-1]).name == "shot_01_06.jpg"
    calls.clear()
    veo_tools.extract_video_previews(video, 1, 8)
    assert calls == ["0.5", "2.5", "5.5", "7.5"]


def test_extended_generation_calls_veo_twice_with_the_tested_config(extended_fakes):
    shot = _timed_shot(10.84)
    client, extension_client = ScriptedClient([CLIP1_OP]), ScriptedClient([EXT_OP])
    outcome = _run(client, shot, extension_client=extension_client)

    assert outcome.status == "SUCCESS", outcome.failure
    first, = client.models.calls
    second, = extension_client.models.calls
    assert first["source"].image is not None and first["source"].video is None
    assert first["config"].duration_seconds == 8
    assert first["source"].prompt == veo_tools.build_video_prompt(shot, seed_for(shot))[0]
    assert second["source"].video.uri == "gs://bucket/out/clip1.mp4"
    assert second["source"].video.mime_type == "video/mp4"
    assert second["source"].image is None
    assert second["model"] == first["model"] == "veo-model"
    assert second["config"].duration_seconds == 7
    for field in ("aspect_ratio", "resolution", "seed", "output_gcs_uri", "enhance_prompt",
                  "person_generation"):
        assert getattr(second["config"], field) == getattr(first["config"], field)
    assert second["config"].generate_audio is False
    assert veo_tools.EXTENSION_MOTION in second["source"].prompt
    assert second["config"].negative_prompt.endswith(veo_tools.EXTENSION_NEGATIVE_EXTRA)

    result = outcome.result
    assert result.operation_dump_path.endswith("shot_01_attempt_01_extension.json")
    assert (veo_tools.VEO_OPERATION_DIR / "shot_01_attempt_01.json").is_file()
    assert result.local_video_path == str(veo_tools.VEO_OUTPUT_DIR / "shot_01.mp4")
    assert result.gcs_uri == "gs://bucket/out/ext.mp4"
    assert result.operation_name == "operations/ext"
    assert result.cost_usd == pytest.approx(2.4)
    assert len(result.preview_paths) == 6
    assert "join at 8.0s" in result.camera_motion
    clip1 = veo_tools.EXTENSION_PARTS_DIR / "shot_01_clip1.mp4"
    assert clip1.read_bytes() == b"mp4 gs://bucket/out/clip1.mp4"
    assert extended_fakes["measure"][0] == (clip1, None)
    assert extended_fakes["measure"][-1][1] == pytest.approx(10.84)
    assert extended_fakes["stripped"] == [Path(result.local_video_path)]
    assert list(veo_tools.VEO_OUTPUT_DIR.glob("*.mp4")) == [Path(result.local_video_path)]


def test_short_shot_with_extension_on_is_todays_single_call(monkeypatch):
    def previews(video_path, shot_sequence, clip_duration_seconds, shown_duration_seconds=0.0):
        preview = veo_tools.VEO_PREVIEW_DIR / f"shot_{shot_sequence:02d}_01.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"jpeg")
        return [str(preview)]

    monkeypatch.setattr(veo_tools, "extract_video_previews", previews)
    monkeypatch.setattr(veo_tools, "measure_camera_motion", lambda path: STATIC)
    op = {"name": "operations/short", "done": True,
          "response": {"generated_videos": [{"video": {"video_bytes": base64.b64encode(b"mp4").decode()}}]}}
    shot = _timed_shot(6.0)
    runs = []
    for extend in (True, False):
        client = ScriptedClient([op])
        outcome = _run(client, shot, extension_client=ScriptedClient([]), extend=extend)
        runs.append((client.models.calls, outcome.model_dump(mode="json")))

    assert runs[0] == runs[1]
    assert len(runs[0][0]) == 1
    assert not list(veo_tools.VEO_OPERATION_DIR.glob("*_extension.json"))
    assert "join" not in runs[0][1]["result"]["camera_motion"]


def test_long_shot_with_extension_off_is_todays_refusal():
    shot = _timed_shot(10.84)
    client = ScriptedClient([])
    outcome = _run(client, shot, extend=False)
    with pytest.raises(ValueError) as today:
        veo_tools.select_clip_duration_seconds(10.84, 8)

    assert outcome.failure.code == "UNSAFE_SEED"
    assert outcome.failure.cost_usd == 0.0
    assert outcome.failure.retryable is False
    assert outcome.failure.reason == str(today.value)
    assert client.models.calls == []


def test_long_shot_extension_refused_when_not_720p():
    client = ScriptedClient([])
    outcome = _run(client, _timed_shot(10.84), resolution="1080p")
    assert outcome.failure.code == "UNSAFE_SEED"
    assert outcome.failure.cost_usd == 0.0
    assert "720p" in outcome.failure.reason
    assert client.models.calls == []


def test_clip1_failure_keeps_todays_code_and_never_extends(extended_fakes):
    rai = {"name": "operations/rai", "done": True,
           "result": {"rai_media_filtered_count": 1, "rai_media_filtered_reasons": ["policy"]}}
    client, extension_client = ScriptedClient([rai]), ScriptedClient([EXT_OP])
    outcome = _run(client, _timed_shot(10.84), extension_client=extension_client)
    assert outcome.failure.code == "RAI_FILTERED"
    assert outcome.failure.cost_usd == pytest.approx(1.2)
    assert len(client.models.calls) + len(extension_client.models.calls) == 1


def test_clip1_without_gcs_uri_cannot_be_extended(extended_fakes):
    inline = {"name": "operations/inline", "done": True,
              "response": {"generated_videos": [{"video": {"video_bytes": base64.b64encode(b"mp4").decode()}}]}}
    client, extension_client = ScriptedClient([inline]), ScriptedClient([EXT_OP])
    outcome = _run(client, _timed_shot(10.84), extension_client=extension_client)
    assert outcome.failure.code == "EXTENSION_NEEDS_GCS_URI"
    assert outcome.failure.retryable is False
    assert len(client.models.calls) + len(extension_client.models.calls) == 1


def test_first_clip_camera_gate_stops_before_the_extension(extended_fakes):
    extended_fakes["motion"] = {"zoom": 1.12, "pan_x_percent": 1, "pan_y_percent": 0}
    client, extension_client = ScriptedClient([CLIP1_OP]), ScriptedClient([EXT_OP])
    outcome = _run(client, _timed_shot(10.84), extension_client=extension_client)
    failure = outcome.failure
    assert (failure.stage, failure.code, failure.retryable) == ("clip_qa", "FIRST_CLIP_CAMERA_MOVED", True)
    assert "x1.12" in failure.reason
    assert failure.cost_usd == pytest.approx(1.2)
    assert str(veo_tools.EXTENSION_PARTS_DIR / "shot_01_clip1.mp4") in failure.partial_artifact_paths
    assert extension_client.models.calls == []
    assert _failure_billed(VEO_CLIP_TOOL, failure.model_dump())

    for motion in (None, RuntimeError("ffmpeg died")):
        extended_fakes["motion"] = motion
        client, extension_client = ScriptedClient([CLIP1_OP]), ScriptedClient([EXT_OP])
        outcome = _run(client, _timed_shot(10.84), extension_client=extension_client)
        assert outcome.status == "SUCCESS"
        assert len(client.models.calls) + len(extension_client.models.calls) == 2


@pytest.mark.parametrize(("response", "poll_error", "max_poll", "code"), [
    (RuntimeError("SDK unavailable"), False, 1, "EXTENSION_SDK_ERROR"),
    ({"name": "operations/ext", "done": False}, True, 1, "EXTENSION_SDK_ERROR"),
    ({"name": "operations/ext", "done": False}, False, -1, "EXTENSION_POLL_TIMEOUT"),
    ({"name": "operations/ext", "done": True,
      "result": {"rai_media_filtered_count": 1, "rai_media_filtered_reasons": ["policy"]}},
     False, 1, "EXTENSION_RAI_FILTERED"),
    ({"name": "operations/ext", "done": True, "response": {"error": {"code": 13, "message": "failed"}}},
     False, 1, "EXTENSION_OPERATION_ERROR"),
], ids=["submit", "poll-exception", "poll-timeout", "rai", "operation-error"])
def test_extension_failures_carry_extension_codes(extended_fakes, response, poll_error, max_poll, code):
    client = ScriptedClient([CLIP1_OP])
    extension_client = ScriptedClient([response], poll_error=poll_error)
    outcome = _run(client, _timed_shot(10.84), extension_client=extension_client,
                   max_poll_seconds=max_poll)
    failure = outcome.failure
    assert (failure.code, failure.stage, failure.retryable) == (code, "veo_generation", True)
    assert failure.reason.startswith("Extension: ")
    assert str(veo_tools.VEO_OPERATION_DIR / "shot_01_attempt_01.json") in failure.partial_artifact_paths
    assert str(veo_tools.EXTENSION_PARTS_DIR / "shot_01_clip1.mp4") in failure.partial_artifact_paths
    assert failure.cost_usd == pytest.approx(2.4)
    assert Path(failure.operation_dump_path).is_file()
    assert _failure_billed(VEO_CLIP_TOOL, failure.model_dump())


def test_extension_shorter_than_the_shown_part_is_refused(extended_fakes):
    extended_fakes["duration"] = 7.0
    outcome = _run(ScriptedClient([CLIP1_OP]), _timed_shot(10.84),
                   extension_client=ScriptedClient([EXT_OP]))
    assert outcome.failure.code == "EXTENSION_TOO_SHORT"
    assert outcome.failure.retryable is False
    assert "7.00s" in outcome.failure.reason


def test_extension_with_a_missing_preview_frame_is_retried(extended_fakes):
    # The join note names frames 3 and 4; a skipped frame would shift them.
    extended_fakes["dropped_previews"] = 1
    outcome = _run(ScriptedClient([CLIP1_OP]), _timed_shot(10.84),
                   extension_client=ScriptedClient([EXT_OP]))
    assert outcome.failure.code == "EXTENSION_PREVIEW_EXTRACTION_FAILED"
    assert outcome.failure.retryable is True
    assert "5 of 6 preview frames" in outcome.failure.reason


def _probe_streams(path: Path) -> list[dict]:
    import json

    completed = subprocess.run(
        [veo_tools.resolve_binary("ffprobe"), "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,codec_name,nb_read_packets", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(completed.stdout)["streams"]


def test_strip_audio_track_is_lossless(tmp_path, monkeypatch):
    try:
        ffmpeg = veo_tools.resolve_binary("ffmpeg")
    except Exception:
        pytest.skip("no working ffmpeg")
    clip = tmp_path / "extended.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1",
         "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(clip)],
        check=True, capture_output=True,
    )
    before = _probe_streams(clip)
    assert {stream["codec_type"] for stream in before} == {"video", "audio"}

    assert veo_tools._strip_audio_track(clip) is None
    after = _probe_streams(clip)
    video_before = next(stream for stream in before if stream["codec_type"] == "video")
    assert after == [video_before]
    assert not clip.with_suffix(".noaudio.mp4").exists()

    original = clip.read_bytes()
    monkeypatch.setattr(veo_tools, "resolve_binary", lambda name: "false")
    assert veo_tools._strip_audio_track(clip)
    assert clip.read_bytes() == original


def test_extended_clip_audio_strip_failure_still_succeeds(extended_fakes, capsys):
    extended_fakes["strip_error"] = "exit 1 broken"
    outcome = _run(ScriptedClient([CLIP1_OP]), _timed_shot(10.84),
                   extension_client=ScriptedClient([EXT_OP]))
    assert outcome.status == "SUCCESS"
    assert "could not remove the silent audio track" in capsys.readouterr().out


def test_extension_prompt_is_a_quiet_hold(extended_fakes):
    shot = _timed_shot(10.84)
    prompt, negative = veo_tools.build_extension_prompt(shot, seed_for(shot))
    assert prompt.startswith(veo_tools.STATIC_CAMERA)
    assert "mouth closed" in prompt and "clean plate" in prompt
    assert '"' not in prompt
    assert negative == veo_tools.build_video_prompt(shot, seed_for(shot))[1] + veo_tools.EXTENSION_NEGATIVE_EXTRA

    client, extension_client = ScriptedClient([CLIP1_OP]), ScriptedClient([EXT_OP])
    _run(client, shot, extension_client=extension_client, correction="make him wave slowly")
    assert "make him wave slowly" in client.models.calls[0]["source"].prompt
    assert extension_client.models.calls[0]["source"].prompt == prompt


def test_clip_tool_reads_the_extension_setting_and_builds_a_default_version_client(monkeypatch):
    shot = shot_for(3)
    seed = seed_for(shot)
    clients: list[dict] = []
    captured: list[dict] = []

    def fake_client(**kwargs):
        clients.append(kwargs)
        return object()

    def fake_generation(client, *, shot, seed, **kwargs):
        captured.append(kwargs)
        return veo_tools._failure(shot=shot, attempt=1, code="STOP", reason="stop", retryable=True, cost_usd=0.0)

    monkeypatch.setattr(veo_tools.genai, "Client", fake_client)
    monkeypatch.setattr(veo_tools, "run_veo_generation", fake_generation)
    args = {
        "shot": shot.model_dump(mode="json"), "seed": seed.model_dump(mode="json"), "project_id": "p",
        "location": "global", "model": "veo-model", "gcs_output_uri": "gs://b/", "resolution": "720p",
        "duration_seconds": 8, "poll_seconds": 0.01, "max_poll_seconds": 1, "attempt": 1, "cost_usd": 1.2,
    }

    veo_tools._generate_veo_clip(args)
    assert len(clients) == 1 and clients[0]["http_options"].api_version == "v1"
    assert captured[-1]["extend_long_shots"] is False
    assert captured[-1]["extension_client"] is None

    clients.clear()
    monkeypatch.setenv("VEO_EXTEND_LONG_SHOTS", "true")
    veo_tools._generate_veo_clip(args)
    assert len(clients) == 2
    assert clients[0]["http_options"].api_version == "v1"
    assert "http_options" not in clients[1]
    assert clients[1] == {"vertexai": True, "project": "p", "location": "global"}
    assert captured[-1]["extend_long_shots"] is True
    assert captured[-1]["extension_client"] is not None
