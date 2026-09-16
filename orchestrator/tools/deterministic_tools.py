"""Asset ingestion, ported from ingest_assets without invoking the script."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool
from PIL import Image


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
ASSETS_FILE = Path("metadata/assets.json")


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_image(path: Path) -> dict:
    file_hash = sha256_file(path)
    asset_id = f"img_{file_hash[:12]}"
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format
        image_mode = image.mode
    if width > height:
        orientation = "landscape"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "square"
    return {
        "asset_id": asset_id,
        "source_path": str(path),
        "original_filename": path.name,
        "sha256": file_hash,
        "file_size_bytes": path.stat().st_size,
        "image": {
            "format": image_format, "width": width, "height": height,
            "orientation": orientation, "mode": image_mode,
            "aspect_ratio": round(width / height, 4),
        },
        "semantic_analysis": None,
    }


def discover_images(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Missing directory: {source_dir.resolve()}")
    return sorted(
        path for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


@tool("ingest", "Discover screenshots and write the deterministic asset registry", {"source_images_dir": str})
async def ingest(args: dict) -> dict:
    assets = [inspect_image(path) for path in discover_images(Path(args["source_images_dir"]))]
    if not assets:
        raise ValueError("No supported screenshots found in source_images_dir")
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "asset_count": len(assets),
        "assets": assets,
    }
    ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ASSETS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(ASSETS_FILE)
    return {"content": [{"type": "text", "text": json.dumps(manifest, ensure_ascii=False)}]}


deterministic_server = create_sdk_mcp_server(name="deterministic", tools=[ingest])
