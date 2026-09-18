from __future__ import annotations

import base64
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


def test_response_inline_bytes_materialize_deterministic_mp4_and_dump():
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
    )

    assert outcome.status == "SUCCESS"
    assert Path(outcome.result.local_video_path).read_bytes() == b"mp4-inline"
    assert Path(outcome.result.operation_dump_path).is_file()


def test_result_uri_is_downloaded_and_kept_as_provenance():
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
