from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from orchestrator.contracts.veo import VeoSeedContract
from orchestrator.contracts.visual_plan import Shot
from orchestrator.tools.gemini_tools import (
    build_seed_spec,
    extract_generated_image,
    generate_seed_image,
)


def shot_dict(**overrides) -> dict:
    base = dict(
        sequence=1,
        generation_mode="VEO",
        visual_treatment="AI_VIDEO_CANDIDATE",
        source_asset_ids=["img_aaaaaaaaaaaa"],
        shot_goal="Show the scene.",
        frame_composition="Center the subject.",
        motion_plan="Use quiet motion.",
        text_overlay="",
        source_support="Directly supported.",
    )
    base.update(overrides)
    return Shot.model_validate(base).model_dump(mode="json")


def asset_for(source_path: Path, *, asset_id: str = "img_aaaaaaaaaaaa") -> dict:
    return {
        "asset_id": asset_id,
        "source_path": str(source_path),
        "analysis": {
            "visual": {"description": "A red card"},
            "production": {"story_art_description": "A card"},
        },
    }


@pytest.fixture(autouse=True)
def working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _write_source_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 40), "red").save(path)


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


class FakePart:
    def __init__(self, *, text: str | None = None, inline_data=None):
        self.text = text
        self.inline_data = inline_data


class FakeInlineData:
    def __init__(self, data):
        self.data = data


class FakeContent:
    def __init__(self, parts):
        self.parts = parts


class FakeCandidate:
    def __init__(self, content):
        self.content = content


class FakeResponse:
    def __init__(self, candidates):
        self.candidates = candidates


class FakeModels:
    def __init__(self, response=None, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class FakeGeminiClient:
    def __init__(self, response=None, raise_exc: Exception | None = None):
        self.models = FakeModels(response, raise_exc)


class RaisingGeminiClient:
    def __init__(self, exc: Exception):
        self.models = FakeModels(raise_exc=exc)


# -- build_seed_spec -----------------------------------------------------


def test_build_seed_spec_rejects_non_veo_mode():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict(generation_mode="STILL", visual_treatment="USE_EXISTING_ART")
    with pytest.raises(ValueError, match="not assigned VEO mode"):
        build_seed_spec(shot, asset_for(source), "Narration context.")


def test_build_seed_spec_rejects_asset_not_cited_by_shot():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict()
    with pytest.raises(ValueError, match="not cited by the VEO shot"):
        build_seed_spec(shot, asset_for(source, asset_id="img_bbbbbbbbbbbb"), "Narration context.")


def test_build_seed_spec_rejects_missing_source_file():
    source = Path("source_images/missing.png")
    shot = shot_dict()
    with pytest.raises(FileNotFoundError, match="does not exist"):
        build_seed_spec(shot, asset_for(source), "Narration context.")


def test_build_seed_spec_returns_expected_spec_for_valid_shot():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict()
    spec = build_seed_spec(shot, asset_for(source), "Narration context.")

    assert spec["shot_sequence"] == 1
    assert spec["source_asset_id"] == "img_aaaaaaaaaaaa"
    assert spec["source_image_path"] == str(source)
    assert spec["output_image_path"] == "generated/veo_seeds/shot_01_seed.png"
    assert "Narration context." in spec["prompt"]


# -- extract_generated_image ----------------------------------------------


def test_extract_generated_image_returns_none_and_text_when_no_image():
    response = FakeResponse([FakeCandidate(FakeContent([FakePart(text="no image here")]))])
    image, text = extract_generated_image(response)
    assert image is None
    assert text == "no image here"


def test_extract_generated_image_returns_image_and_text_when_present():
    raw = _png_bytes()
    response = FakeResponse([
        FakeCandidate(FakeContent([
            FakePart(text="here is the image"),
            FakePart(inline_data=FakeInlineData(raw)),
        ]))
    ])
    image, text = extract_generated_image(response)
    assert image is not None
    assert image.size == (4, 4)
    assert text == "here is the image"


# -- generate_seed_image ---------------------------------------------------


def test_generate_seed_image_raises_when_no_image_in_response():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict()
    spec = build_seed_spec(shot, asset_for(source), "Narration context.")
    response = FakeResponse([FakeCandidate(FakeContent([FakePart(text="I could not recompose this.")]))])
    client = FakeGeminiClient(response=response)

    with pytest.raises(RuntimeError, match="no image"):
        generate_seed_image(client, spec, model="image-model", cost_usd=0.04)


def test_generate_seed_image_raises_on_empty_response():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict()
    spec = build_seed_spec(shot, asset_for(source), "Narration context.")
    response = FakeResponse([])
    client = FakeGeminiClient(response=response)

    with pytest.raises(RuntimeError, match="no image"):
        generate_seed_image(client, spec, model="image-model", cost_usd=0.04)


def test_generate_seed_image_propagates_client_exception():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict()
    spec = build_seed_spec(shot, asset_for(source), "Narration context.")
    client = RaisingGeminiClient(RuntimeError("Gemini unavailable"))

    with pytest.raises(RuntimeError, match="Gemini unavailable"):
        generate_seed_image(client, spec, model="image-model", cost_usd=0.04)


def test_generate_seed_image_writes_seed_and_returns_contract():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict()
    spec = build_seed_spec(shot, asset_for(source), "Narration context.")
    response = FakeResponse([
        FakeCandidate(FakeContent([
            FakePart(text="Recomposed cleanly."),
            FakePart(inline_data=FakeInlineData(_png_bytes())),
        ]))
    ])
    client = FakeGeminiClient(response=response)

    seed, model_text = generate_seed_image(client, spec, model="image-model", cost_usd=0.04)

    assert isinstance(seed, VeoSeedContract)
    assert model_text == "Recomposed cleanly."
    assert Path(seed.local_path).is_file()
    assert seed.local_path == "generated/veo_seeds/shot_01_seed.png"
    assert seed.approved is False
    assert seed.cost_usd == 0.04
    seed.validate_seed_provenance()
