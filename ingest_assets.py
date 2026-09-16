from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


SOURCE_DIR = Path("source_images")
OUTPUT_DIR = Path("metadata")
OUTPUT_FILE = OUTPUT_DIR / "assets.json"

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
}


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

    # Stable internal ID.
    # Filename is deliberately NOT used.
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

        # Kept only so we know where the physical file lives.
        # It will NOT determine meaning or story order.
        "source_path": str(path),

        "original_filename": path.name,

        "sha256": file_hash,

        "file_size_bytes": path.stat().st_size,

        "image": {
            "format": image_format,
            "width": width,
            "height": height,
            "orientation": orientation,
            "mode": image_mode,
            "aspect_ratio": round(width / height, 4),
        },

        # Gemini will populate these later.
        "semantic_analysis": None,
    }


def discover_images() -> list[Path]:
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(
            f"Missing directory: {SOURCE_DIR.resolve()}"
        )

    files = []

    for path in SOURCE_DIR.rglob("*"):
        if (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ):
            files.append(path)

    # Sorting is purely for deterministic processing.
    # It does NOT represent narrative/story order.
    return sorted(files)


def main() -> None:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    image_files = discover_images()

    assets = [
        inspect_image(path)
        for path in image_files
    ]

    manifest = {
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "asset_count": len(assets),

        "assets": assets,
    }

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            manifest,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"Discovered {len(assets)} image(s)."
    )

    print(
        f"Manifest written to: {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()