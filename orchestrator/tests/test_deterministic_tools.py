from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from PIL import Image

from orchestrator.tools.deterministic_tools import (
    deterministic_server, discover_images, ingest, inspect_image, sha256_file,
)


def test_stable_hash_ids_and_original_metadata(tmp_path):
    source = tmp_path / "one.PNG"
    renamed = tmp_path / "renamed.PNG"
    Image.new("RGB", (20, 40), "red").save(source)
    renamed.write_bytes(source.read_bytes())
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert sha256_file(source) == expected_hash
    original, copy = inspect_image(source), inspect_image(renamed)
    assert original["asset_id"] == copy["asset_id"] == f"img_{expected_hash[:12]}"
    assert original["image"] == {
        "format": "PNG", "width": 20, "height": 40, "orientation": "portrait",
        "mode": "RGB", "aspect_ratio": 0.5,
    }
    assert copy["original_filename"] == "renamed.PNG"
    Image.new("RGB", (20, 40), "blue").save(source)
    assert inspect_image(source)["asset_id"] != original["asset_id"]


def test_discovery_and_sdk_tool_write_registry_recursively(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sources"
    (source / "nested").mkdir(parents=True)
    Image.new("RGB", (10, 20)).save(source / "z.PNG")
    Image.new("RGB", (20, 10)).save(source / "nested" / "a.jpg")
    (source / "ignore.txt").write_text("not an image")
    assert discover_images(source) == [source / "nested" / "a.jpg", source / "z.PNG"]
    response = asyncio.run(ingest.handler({"source_images_dir": str(source)}))
    manifest = json.loads(response["content"][0]["text"])
    assert manifest == json.loads((tmp_path / "metadata/assets.json").read_text())
    assert manifest["asset_count"] == 2
    assert all(a["semantic_analysis"] is None for a in manifest["assets"])
    assert deterministic_server["name"] == "deterministic"


@pytest.mark.parametrize("kind", ["missing", "empty", "corrupt"])
def test_bad_input_does_not_publish_a_registry(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sources"
    if kind != "missing":
        source.mkdir()
    if kind == "corrupt":
        (source / "invalid.png").write_text("not an image")
    with pytest.raises((ValueError, OSError)):
        asyncio.run(ingest.handler({"source_images_dir": str(source)}))
    assert not (tmp_path / "metadata/assets.json").exists()
