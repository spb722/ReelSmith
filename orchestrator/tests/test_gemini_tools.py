from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from orchestrator.contracts.stills import StillResultContract
from orchestrator.contracts.veo import VeoSeedContract
from orchestrator.contracts.visual_plan import Shot
from orchestrator.tools.gemini_tools import (
    build_seed_spec,
    build_still_spec,
    extract_generated_image,
    generate_seed_image,
    generate_still_image,
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


@pytest.fixture(autouse=True)
def no_image_call_delays(monkeypatch):
    """Strip every real-time delay from the image-call path under test."""
    from orchestrator.tools import gemini_tools

    monkeypatch.setattr(gemini_tools, "EMPTY_IMAGE_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(gemini_tools, "GEMINI_IMAGE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(gemini_tools, "QUOTA_RETRY_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(gemini_tools, "_last_image_call_monotonic", 0.0)


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


# -- build_still_spec -------------------------------------------------------


def still_shot_dict(**overrides) -> dict:
    base = dict(
        sequence=1,
        generation_mode="STILL",
        visual_treatment="USE_EXISTING_ART",
        source_asset_ids=["img_aaaaaaaaaaaa"],
        shot_goal="Show the scene.",
        frame_composition="Center the subject.",
        motion_plan="Slow push-in.",
        text_overlay="",
        source_support="Directly supported.",
        fade_in_frames=6,
        fade_out_frames=4,
        still_motion={"scale_from": 1.0, "scale_to": 1.06, "easing": "ease"},
    )
    base.update(overrides)
    return Shot.model_validate(base).model_dump(mode="json")


def test_build_still_spec_rejects_non_still_mode():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = still_shot_dict(generation_mode="VEO", visual_treatment="AI_VIDEO_CANDIDATE")
    with pytest.raises(ValueError, match="not assigned STILL mode"):
        build_still_spec(shot, asset_for(source), "Narration context.")


def test_build_still_spec_rejects_asset_not_cited_by_shot():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = still_shot_dict()
    with pytest.raises(ValueError, match="not cited by the STILL shot"):
        build_still_spec(shot, asset_for(source, asset_id="img_bbbbbbbbbbbb"), "Narration context.")


def test_build_still_spec_rejects_missing_source_file():
    source = Path("source_images/missing.png")
    shot = still_shot_dict()
    with pytest.raises(FileNotFoundError, match="does not exist"):
        build_still_spec(shot, asset_for(source), "Narration context.")


def test_build_still_spec_returns_expected_spec_for_valid_shot():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = still_shot_dict()
    spec = build_still_spec(shot, asset_for(source), "Narration context.")

    assert spec["shot_sequence"] == 1
    assert spec["source_asset_id"] == "img_aaaaaaaaaaaa"
    assert spec["source_image_path"] == str(source)
    assert spec["output_image_path"] == "generated/stills/shot_01.png"
    assert "Narration context." in spec["prompt"]
    assert "duplicate" in spec["prompt"]


def test_build_still_spec_never_branches_on_shot_number():
    """The anti-pattern this story exists to avoid: the prompt must be built
    from Shot fields, not a hardcoded per-shot-number ladder. Two different
    shot numbers with identical Shot fields (besides sequence) must produce
    prompts differing only in whatever those fields actually say.
    """
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot_one = still_shot_dict(sequence=1, source_asset_ids=["img_aaaaaaaaaaaa"])
    shot_seven = still_shot_dict(sequence=7, source_asset_ids=["img_aaaaaaaaaaaa"])
    spec_one = build_still_spec(shot_one, asset_for(source), "Narration context.")
    spec_seven = build_still_spec(shot_seven, asset_for(source), "Narration context.")
    assert spec_one["prompt"] == spec_seven["prompt"]


# -- generate_still_image ----------------------------------------------------


def test_generate_still_image_raises_when_no_image_in_response():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = still_shot_dict()
    spec = build_still_spec(shot, asset_for(source), "Narration context.")
    response = FakeResponse([FakeCandidate(FakeContent([FakePart(text="I could not do this.")]))])
    client = FakeGeminiClient(response=response)

    with pytest.raises(RuntimeError, match="no image"):
        generate_still_image(client, spec, model="image-model", cost_usd=0.04)


def test_generate_still_image_propagates_client_exception():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = still_shot_dict()
    spec = build_still_spec(shot, asset_for(source), "Narration context.")
    client = RaisingGeminiClient(RuntimeError("Gemini unavailable"))

    with pytest.raises(RuntimeError, match="Gemini unavailable"):
        generate_still_image(client, spec, model="image-model", cost_usd=0.04)


def test_generate_still_image_writes_still_and_returns_contract():
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = still_shot_dict()
    spec = build_still_spec(shot, asset_for(source), "Narration context.")
    response = FakeResponse([
        FakeCandidate(FakeContent([
            FakePart(text="Recomposed cleanly."),
            FakePart(inline_data=FakeInlineData(_png_bytes())),
        ]))
    ])
    client = FakeGeminiClient(response=response)

    result, model_text = generate_still_image(client, spec, model="image-model", cost_usd=0.04)

    assert isinstance(result, StillResultContract)
    assert model_text == "Recomposed cleanly."
    assert Path(result.local_image_path).is_file()
    assert result.local_image_path == "generated/stills/shot_01.png"
    assert result.approved is False
    assert result.cost_usd == 0.04


# -- no-text override -----------------------------------------------------

# The real shot 6 composition that caused Gemini to burn the closing narration
# into the seed plate twice before the QA loop got a clean one.
OVERLAY_COMPOSITION = (
    "Open on the same story_art_region as the previous shot, framed slightly wider, "
    "then settle the closing narration as text_overlay layered over the warm-lit scene."
)


@pytest.mark.parametrize("builder,mode", [
    (build_seed_spec, "VEO"),
    (build_still_spec, "STILL"),
])
def test_image_prompt_forbids_text_after_a_composition_that_asks_for_it(builder, mode):
    """A composition describing post-production text is fed almost verbatim to
    an image generator, which draws the words into the plate -- where they
    cannot be timed or removed, and smear once the shot moves. The overriding
    no-text rule must come after the descriptions, not before them.
    """
    source = Path("source_images/a.png")
    _write_source_image(source)
    shot = shot_dict(
        generation_mode=mode,
        visual_treatment="AI_VIDEO_CANDIDATE" if mode == "VEO" else "USE_EXISTING_ART",
        frame_composition=OVERLAY_COMPOSITION,
        text_overlay="Happiness isn't a finish line",
    )
    prompt = builder(shot, asset_for(source), "Happiness isn't a finish line.")["prompt"]

    assert "FINAL RULE" in prompt
    # The override has to be the last word on the subject.
    assert prompt.index("FINAL RULE") > prompt.index("text_overlay layered over")
    assert prompt.index("FINAL RULE") > prompt.index("Narration context")
    assert "render NO text of any kind" in prompt


# -- character reference ----------------------------------------------------
#
# The autouse `working_directory` fixture chdirs into an empty tmp_path, so the
# default `assets/character/character.png` never exists unless a test creates
# it. That is what keeps every test above running with the feature off.


def _write_character(tmp_path: Path, *, face: bool = True) -> tuple[Path, Path | None]:
    body = tmp_path / "assets" / "character" / "character.png"
    body.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 30), "green").save(body)
    face_path = None
    if face:
        face_path = body.parent / "character_face.png"
        Image.new("RGB", (10, 10), "yellow").save(face_path)
    return body, face_path


def _enable_character(monkeypatch, body: Path, face: Path | None) -> None:
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.setenv("CHARACTER_FACE_REFERENCE_PATH", str(face) if face else "")


def asset_with_figure(source_path: Path, *, asset_id: str = "img_aaaaaaaaaaaa") -> dict:
    asset = asset_for(source_path, asset_id=asset_id)
    asset["analysis"]["visual"].update(
        {
            "real_people_visible": False,
            "illustrated_or_cartoon_figures_visible": True,
            "figure_descriptions": ["Bald round-headed cartoon figure in a blue sweater."],
            "visual_subjects": [{"subject_type": "ILLUSTRATED_PERSON"}],
        }
    )
    return asset


def asset_without_figure(source_path: Path, *, asset_id: str = "img_aaaaaaaaaaaa") -> dict:
    asset = asset_for(source_path, asset_id=asset_id)
    asset["analysis"]["visual"].update(
        {
            "real_people_visible": False,
            "illustrated_or_cartoon_figures_visible": False,
            "figure_descriptions": [],
            "visual_subjects": [{"subject_type": "SYMBOL"}],
        }
    )
    return asset


@pytest.mark.parametrize(
    "builder,mode",
    [(build_seed_spec, "VEO"), (build_still_spec, "STILL")],
)
def test_no_character_configured_leaves_prompt_and_contents_unchanged(builder, mode, tmp_path):
    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = builder(shot_dict(generation_mode=mode), asset_with_figure(source), "Narration.")

    assert spec["character_reference_paths"] == []
    assert "CHARACTER SUBSTITUTION" not in spec["prompt"]

    client = FakeGeminiClient(
        FakeResponse([FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))])
    )
    runner = generate_seed_image if mode == "VEO" else generate_still_image
    runner(client, spec, model="m", cost_usd=0.04)
    assert len(client.models.calls[0]["contents"]) == 2


@pytest.mark.parametrize(
    "builder,mode",
    [(build_seed_spec, "VEO"), (build_still_spec, "STILL")],
)
def test_character_is_sent_when_the_asset_already_has_a_figure(builder, mode, tmp_path, monkeypatch):
    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    source = tmp_path / "shot.png"
    _write_source_image(source)

    spec = builder(shot_dict(generation_mode=mode), asset_with_figure(source), "Narration.")

    assert spec["character_reference_paths"] == [str(body), str(face)]
    assert "CHARACTER SUBSTITUTION" in spec["prompt"]
    assert "Bald round-headed cartoon figure" in spec["prompt"]
    assert "authority on their facial features" in spec["prompt"]

    client = FakeGeminiClient(
        FakeResponse([FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))])
    )
    runner = generate_seed_image if mode == "VEO" else generate_still_image
    runner(client, spec, model="m", cost_usd=0.04)
    # scene first, then body reference, then face reference, then the prompt
    contents = client.models.calls[0]["contents"]
    assert len(contents) == 4
    assert isinstance(contents[-1], str)


@pytest.mark.parametrize(
    "builder,mode",
    [(build_seed_spec, "VEO"), (build_still_spec, "STILL")],
)
def test_character_is_not_sent_when_the_scene_has_no_person(builder, mode, tmp_path, monkeypatch):
    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    source = tmp_path / "shot.png"
    _write_source_image(source)

    spec = builder(shot_dict(generation_mode=mode), asset_without_figure(source), "Narration.")

    assert spec["character_reference_paths"] == []
    assert "CHARACTER SUBSTITUTION" not in spec["prompt"]


def test_face_reference_alone_does_not_enable_the_character(tmp_path, monkeypatch):
    face = tmp_path / "assets" / "character" / "character_face.png"
    face.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), "yellow").save(face)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(tmp_path / "missing.png"))
    monkeypatch.setenv("CHARACTER_FACE_REFERENCE_PATH", str(face))

    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), asset_with_figure(source), "N.")
    assert spec["character_reference_paths"] == []


def test_character_block_precedes_the_final_no_text_rule(tmp_path, monkeypatch):
    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    source = tmp_path / "shot.png"
    _write_source_image(source)

    prompt = build_still_spec(
        shot_dict(generation_mode="STILL"), asset_with_figure(source), "Narration."
    )["prompt"]
    assert prompt.index("CHARACTER SUBSTITUTION") < prompt.index("FINAL RULE")
    assert prompt.rstrip().endswith("clean plate without it.")


@pytest.mark.parametrize(
    "builder,mode",
    [(build_seed_spec, "VEO"), (build_still_spec, "STILL")],
)
def test_blanket_character_ban_is_replaced_by_an_invention_ban(builder, mode, tmp_path):
    source = tmp_path / "shot.png"
    _write_source_image(source)
    prompt = builder(shot_dict(generation_mode=mode), asset_for(source), "Narration.")["prompt"]

    assert "do not add characters" not in prompt.lower()
    assert "do not add extra characters" not in prompt.lower()
    assert "invent people who are not" in prompt


def test_missing_reference_image_raises_naming_the_reference(tmp_path):
    from orchestrator.tools.gemini_tools import run_gemini_image_edit

    source = tmp_path / "shot.png"
    _write_source_image(source)
    missing = tmp_path / "nope.png"
    client = FakeGeminiClient(FakeResponse([]))

    with pytest.raises(FileNotFoundError, match="nope.png"):
        run_gemini_image_edit(
            client,
            source_image_path=source,
            prompt="p",
            model="m",
            reference_image_paths=[missing],
        )


def test_tool_result_carries_labelled_character_references(tmp_path, monkeypatch):
    import asyncio
    import json as _json

    from orchestrator.tools import gemini_tools

    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    source = tmp_path / "shot.png"
    _write_source_image(source)

    response = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    monkeypatch.setattr(gemini_tools.genai, "Client", lambda **_: FakeGeminiClient(response))

    result = asyncio.run(
        gemini_tools.generate_still.handler(
            {
                "shot": shot_dict(generation_mode="STILL"),
                "asset": asset_with_figure(source),
                "subtitle_text": "Narration.",
                "project_id": "p",
                "location": "global",
                "image_model": "m",
                "cost_usd": 0.04,
            }
        )
    )

    blocks = result["content"]
    images = [block for block in blocks if block["type"] == "image"]
    assert len(images) == 3  # the still under QA, plus body and face references
    labels = " ".join(block["text"] for block in blocks if block["type"] == "text")
    assert "ONLY image under QA" in labels
    # the reference must never leak into the payload the agent copies into its
    # extra="forbid" outcome contract
    payload = _json.loads(blocks[0]["text"])
    assert not any("character" in key for key in payload)


def test_tool_result_has_no_reference_blocks_when_the_scene_has_no_person(tmp_path, monkeypatch):
    import asyncio

    from orchestrator.tools import gemini_tools

    body, face = _write_character(tmp_path)
    _enable_character(monkeypatch, body, face)
    source = tmp_path / "shot.png"
    _write_source_image(source)

    response = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    monkeypatch.setattr(gemini_tools.genai, "Client", lambda **_: FakeGeminiClient(response))

    result = asyncio.run(
        gemini_tools.generate_still.handler(
            {
                "shot": shot_dict(generation_mode="STILL"),
                "asset": asset_without_figure(source),
                "subtitle_text": "Narration.",
                "project_id": "p",
                "location": "global",
                "image_model": "m",
                "cost_usd": 0.04,
            }
        )
    )

    assert len([block for block in result["content"] if block["type"] == "image"]) == 1


class FlakyModels(FakeModels):
    """Returns an image-less response first, then a real image."""

    def __init__(self, empty_first: int, response):
        super().__init__(response)
        self.empty_first = empty_first

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.empty_first:
            return FakeResponse([FakeCandidate(FakeContent([FakePart(text="I cannot do that.")]))])
        return self.response


def test_transient_empty_image_response_is_retried_inside_the_tool(tmp_path):
    from orchestrator.tools.gemini_tools import run_gemini_image_edit

    source = tmp_path / "shot.png"
    _write_source_image(source)
    good = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    client = FakeGeminiClient()
    client.models = FlakyModels(2, good)

    image, _ = run_gemini_image_edit(
        client, source_image_path=source, prompt="p", model="m"
    )
    assert image is not None
    assert len(client.models.calls) == 3


def test_persistent_empty_image_response_reports_the_model_text(tmp_path):
    from orchestrator.tools.gemini_tools import run_gemini_image_edit

    source = tmp_path / "shot.png"
    _write_source_image(source)
    client = FakeGeminiClient()
    client.models = FlakyModels(99, None)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        run_gemini_image_edit(client, source_image_path=source, prompt="p", model="m")
    assert len(client.models.calls) == 3


class BlockedFeedback:
    def __init__(self, reason):
        self.block_reason = reason


class BlockedResponse(FakeResponse):
    def __init__(self, reason="OTHER"):
        super().__init__([])
        self.prompt_feedback = BlockedFeedback(reason)


def test_a_blocked_request_fails_immediately_without_burning_quota(tmp_path):
    from orchestrator.tools.gemini_tools import run_gemini_image_edit

    source = tmp_path / "shot.png"
    _write_source_image(source)
    client = FakeGeminiClient(BlockedResponse("OTHER"))

    with pytest.raises(RuntimeError, match="will keep refusing"):
        run_gemini_image_edit(client, source_image_path=source, prompt="p", model="m")
    # one call, not three: a refusal is not retried
    assert len(client.models.calls) == 1


class QuotaThenSuccessModels(FakeModels):
    def __init__(self, failures: int, response):
        super().__init__(response)
        self.failures = failures

    def generate_content(self, **kwargs):
        from google.genai import errors as genai_errors

        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise genai_errors.ClientError(
                429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}
            )
        return self.response


def test_quota_errors_are_retried_with_backoff(tmp_path):
    from orchestrator.tools.gemini_tools import run_gemini_image_edit

    source = tmp_path / "shot.png"
    _write_source_image(source)
    good = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    client = FakeGeminiClient()
    client.models = QuotaThenSuccessModels(2, good)

    image, _ = run_gemini_image_edit(client, source_image_path=source, prompt="p", model="m")
    assert image is not None
    assert len(client.models.calls) == 3


def test_persistent_quota_errors_surface_to_the_caller(tmp_path):
    from google.genai import errors as genai_errors

    from orchestrator.tools.gemini_tools import run_gemini_image_edit

    source = tmp_path / "shot.png"
    _write_source_image(source)
    client = FakeGeminiClient()
    client.models = QuotaThenSuccessModels(99, None)

    with pytest.raises(genai_errors.APIError):
        run_gemini_image_edit(client, source_image_path=source, prompt="p", model="m")


def test_image_calls_are_paced_apart(tmp_path, monkeypatch):
    from orchestrator.tools import gemini_tools

    slept: list[float] = []
    monkeypatch.setattr(gemini_tools, "GEMINI_IMAGE_MIN_INTERVAL_SECONDS", 5.0)
    monkeypatch.setattr(gemini_tools, "_last_image_call_monotonic", 0.0)
    monkeypatch.setattr(gemini_tools.time, "sleep", lambda s: slept.append(s))

    source = tmp_path / "shot.png"
    _write_source_image(source)
    good = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    client = FakeGeminiClient(good)

    gemini_tools.run_gemini_image_edit(client, source_image_path=source, prompt="p", model="m")
    gemini_tools.run_gemini_image_edit(client, source_image_path=source, prompt="p", model="m")

    # the second call waits out the remainder of the 5s window
    assert slept and 0 < slept[0] <= 5.0


def test_only_the_body_reference_is_sent_by_default(tmp_path, monkeypatch):
    """Default config sends one reference; the face crop is opt-in."""
    body, face = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)

    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), asset_with_figure(source), "N.")

    assert spec["character_reference_paths"] == [str(body)]
    assert "authority on their facial features" not in spec["prompt"]


def test_prompt_lifts_a_downturned_face_toward_the_viewer(tmp_path, monkeypatch):
    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)

    source = tmp_path / "shot.png"
    _write_source_image(source)
    prompt = build_still_spec(
        shot_dict(generation_mode="STILL"), asset_with_figure(source), "N."
    )["prompt"]

    # "away" is deliberately absent here: an away-facing figure now takes the
    # other branch, because demanding a face on one is what the likeness filter
    # refuses.
    assert "looking down or into shadow" in prompt
    assert "reads clearly toward the viewer" in prompt


def _write_analyzed_assets(tmp_path: Path, asset: dict) -> None:
    path = tmp_path / "metadata" / "analyzed_assets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"assets": [asset]}), encoding="utf-8")


def test_character_survives_an_agent_trimming_the_asset_analysis(tmp_path, monkeypatch):
    """The agent relays the asset dict into the tool call and can drop its
    analysis. A live run lost the character on shot 6 exactly this way, so the
    figure check reads the persisted analysis instead of trusting the relay.
    """
    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)

    source = tmp_path / "shot.png"
    _write_source_image(source)
    _write_analyzed_assets(tmp_path, asset_with_figure(source))

    trimmed = {"asset_id": "img_aaaaaaaaaaaa", "source_path": str(source)}
    spec = build_still_spec(shot_dict(generation_mode="STILL"), trimmed, "Narration.")

    assert spec["character_reference_paths"] == [str(body)]
    assert "CHARACTER SUBSTITUTION" in spec["prompt"]
    # the figure description also comes from the persisted record
    assert "Bald round-headed cartoon figure" in spec["prompt"]


def test_persisted_analysis_still_withholds_the_character_from_a_peopleless_scene(
    tmp_path, monkeypatch
):
    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)

    source = tmp_path / "shot.png"
    _write_source_image(source)
    _write_analyzed_assets(tmp_path, asset_without_figure(source))

    trimmed = {"asset_id": "img_aaaaaaaaaaaa", "source_path": str(source)}
    spec = build_still_spec(shot_dict(generation_mode="STILL"), trimmed, "Narration.")
    assert spec["character_reference_paths"] == []


def test_actual_tts_request_respects_voice_override(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.tools import gemini_tools as tools

    monkeypatch.setenv("TTS_VOICE_NAME", "Algieba")
    requests = []

    def generate_content(**kwargs):
        requests.append(kwargs)
        return object()

    monkeypatch.setattr(tools.genai, "Client", lambda **kwargs: SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)))
    monkeypatch.setattr(tools, "extract_pcm", lambda response: b"\x00\x00" * 24000)
    response = asyncio.run(tools.generate_narration_audio.handler({
        "narration_script": "British cyclists improved one percent.", "voice_direction": {},
        "project_id": "test", "location": "global",
    }))
    assert requests[0]["config"].speech_config.voice_config.prebuilt_voice_config.voice_name == "Algieba"
    assert json.loads(response["content"][0]["text"])["voice_name"] == "Algieba"
    assert "Don't try" not in requests[0]["contents"]


def test_empty_tts_voice_fails_before_client_creation(monkeypatch):
    import asyncio
    from orchestrator.tools import gemini_tools as tools
    monkeypatch.setenv("TTS_VOICE_NAME", "")
    monkeypatch.setattr(tools.genai, "Client", lambda **kwargs: pytest.fail("No API call allowed"))
    with pytest.raises(ValueError, match="TTS_VOICE_NAME"):
        asyncio.run(tools.generate_narration_audio.handler({"narration_script": "Some text."}))


# -- prompt ladder ----------------------------------------------------------
#
# The likeness filter refuses some scenes outright (block_reason=OTHER) and the
# refusal is deterministic, so the strongest acceptable wording can only be
# found by asking. A live run halted on shot 6 -- a figure the source draws from
# behind -- because the prompt demanded a recognisable face on it.


def test_prompt_ladder_descends_from_face_to_pose_to_no_character(tmp_path, monkeypatch):
    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)

    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), asset_with_figure(source), "N.")

    ladder = spec["prompt_ladder"]
    assert [r["level"] for r in ladder] == [0, 1, 2]

    assert "The face must be clearly visible" in ladder[0]["prompt"]
    assert ladder[0]["reference_image_paths"] == [str(body)]

    assert "keeping the source figure's existing pose" in ladder[1]["prompt"]
    assert "The face must be clearly visible" not in ladder[1]["prompt"]
    assert ladder[1]["reference_image_paths"] == [str(body)]

    assert "CHARACTER SUBSTITUTION" not in ladder[2]["prompt"]
    assert ladder[2]["reference_image_paths"] == []

    # every rung still ends with the no-text override
    assert all(r["prompt"].rstrip().endswith("clean plate without it.") for r in ladder)


def test_ladder_is_a_single_rung_when_no_character_is_configured(tmp_path):
    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), asset_with_figure(source), "N.")

    assert [r["level"] for r in spec["prompt_ladder"]] == [2]
    assert spec["prompt"] == spec["prompt_ladder"][0]["prompt"]


class RefusingModels(FakeModels):
    """Refuses the first `refusals` calls, then returns an image."""

    def __init__(self, refusals: int, response):
        super().__init__(response)
        self.refusals = refusals

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.refusals:
            return BlockedResponse("OTHER")
        return self.response


def test_a_refused_rung_falls_through_to_the_next(tmp_path, monkeypatch):
    from orchestrator.tools.gemini_tools import run_image_edit_ladder

    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)
    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), asset_with_figure(source), "N.")

    good = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    client = FakeGeminiClient()
    client.models = RefusingModels(1, good)

    image, _, rung = run_image_edit_ladder(client, spec, model="m")
    assert image is not None
    assert rung["level"] == 1, "should have settled on the pose-preserving wording"
    assert len(client.models.calls) == 2


def test_refusing_every_rung_says_the_scene_is_the_trigger(tmp_path, monkeypatch):
    from orchestrator.tools.gemini_tools import ImageEditRefused, run_image_edit_ladder

    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)
    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), asset_with_figure(source), "N.")

    client = FakeGeminiClient()
    client.models = RefusingModels(99, None)

    with pytest.raises(ImageEditRefused, match="the scene itself is the trigger"):
        run_image_edit_ladder(client, spec, model="m")
    assert len(client.models.calls) == 3


def test_a_transient_null_does_not_advance_the_ladder(tmp_path, monkeypatch):
    """Only a refusal descends. An empty response is retried at the same rung."""
    from orchestrator.tools.gemini_tools import run_image_edit_ladder

    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)
    source = tmp_path / "shot.png"
    _write_source_image(source)
    spec = build_still_spec(shot_dict(generation_mode="STILL"), asset_with_figure(source), "N.")

    good = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    client = FakeGeminiClient()
    client.models = FlakyModels(1, good)

    _, _, rung = run_image_edit_ladder(client, spec, model="m")
    assert rung["level"] == 0, "a transient null must not cost the best wording"


def test_a_downgraded_rung_tells_the_qa_agent_not_to_expect_a_face(tmp_path, monkeypatch):
    """The agent must not reject an image for a face the model refused to draw."""
    import asyncio

    from orchestrator.tools import gemini_tools

    body, _ = _write_character(tmp_path)
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", str(body))
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)
    source = tmp_path / "shot.png"
    _write_source_image(source)

    good = FakeResponse(
        [FakeCandidate(FakeContent([FakePart(inline_data=FakeInlineData(_png_bytes()))]))]
    )
    client = FakeGeminiClient()
    client.models = RefusingModels(1, good)   # level 0 refused, level 1 accepted
    monkeypatch.setattr(gemini_tools.genai, "Client", lambda **_: client)

    result = asyncio.run(
        gemini_tools.generate_still.handler({
            "shot": shot_dict(generation_mode="STILL"),
            "asset": asset_with_figure(source),
            "subtitle_text": "N.",
            "project_id": "p", "location": "global",
            "image_model": "m", "cost_usd": 0.04,
        })
    )

    labels = " ".join(b["text"] for b in result["content"] if b["type"] == "text")
    assert "do NOT reject this image for a face that is turned away" in labels
