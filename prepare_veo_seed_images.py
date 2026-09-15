from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image
from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ID = os.getenv(
    "GOOGLE_CLOUD_PROJECT",
    "gen-lang-client-0240752803",
)

LOCATION = os.getenv(
    "GOOGLE_CLOUD_LOCATION",
    "global",
)

MODEL = "gemini-3.1-flash-image"

VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")

MANIFEST_FILE = Path("metadata/veo_seed_manifest.json")
RESULTS_FILE = Path("metadata/veo_seed_results.json")
STATUS_FILE = Path("metadata/veo_seed_status.txt")

OUTPUT_DIR = Path("generated/veo_seeds")

TARGET_SHOTS = [2, 5, 6]

ASPECT_RATIO = "9:16"
OUTPUT_RESOLUTION = "1K"

# Set to True if you want the model's text explanation saved too.
SAVE_TEXT_RESPONSE = True


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class SeedSpec:
    seed_id: str
    shot_sequence: int
    asset_id: str
    local_image_path: str
    output_image_path: str
    reel_start_seconds: float
    reel_end_seconds: float
    subtitle_cue_ids: list[str]
    prompt: str
    shot_goal: str
    source_support: str


# ============================================================
# HELPERS
# ============================================================

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp.replace(path)


def write_status(text: str) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(text, encoding="utf-8")


def resolve_local_image_path(source_path: str) -> Path:
    path = Path(source_path)

    if path.exists():
        return path

    candidate = Path.cwd() / source_path
    if candidate.exists():
        return candidate

    raise FileNotFoundError(
        f"Could not resolve source image path: {source_path}"
    )


def cue_text_for_shot(shot: dict, cue_lookup: dict[str, str]) -> str:
    texts = []
    for cue_id in shot.get("primary_subtitle_cue_ids", []):
        text = cue_lookup.get(cue_id)
        if text:
            texts.append(text)
    return " ".join(texts).strip()


def extract_generated_image(response) -> tuple[Image.Image | None, str]:
    """
    Returns:
        (image_or_none, combined_text)
    """
    combined_text_parts = []
    output_image = None

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None, ""

    parts = getattr(candidates[0].content, "parts", None) or []

    for part in parts:
        if getattr(part, "text", None):
            combined_text_parts.append(part.text)

        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            output_image = Image.open(BytesIO(inline.data)).convert("RGB")

    return output_image, "\n".join(combined_text_parts).strip()


# ============================================================
# PROMPTS
# ============================================================

def build_seed_spec(
    shot: dict,
    asset: dict,
    cue_lookup: dict[str, str],
) -> SeedSpec:
    shot_sequence = int(shot["sequence"])
    asset_id = asset["asset_id"]

    local_image_path = resolve_local_image_path(asset["source_path"])

    output_filename = f"shot_{shot_sequence:02d}_seed.png"
    output_path = OUTPUT_DIR / output_filename

    subtitle_context = cue_text_for_shot(shot, cue_lookup)

    common_rules = (
        "Remove all mobile app UI, status bar elements, buttons, progress bars, "
        "card chrome, instruction text, and any surrounding app layout. "
        "Do not include any visible words or subtitles in the output. "
        "Preserve the underlying illustration's style, mood, palette, and key objects. "
        "Recompose the scene into a clean vertical 9:16 image suitable as a Veo image-to-video seed. "
        "Keep the result as a mature editorial illustration, not photorealistic. "
        "Do not add logos, watermarks, or extra characters."
    )

    if shot_sequence == 2:
        seed_id = "seed_shot_02_bukowski_rejection"
        prompt = (
            f"{common_rules} "
            "Use the source screenshot only as reference for the embedded illustration. "
            "Focus on the writer's room scene: wooden desk, vintage typewriter, ashtray with smoke, "
            "coffee mug, crumpled papers, desk lamp, and unmade bed in the background. "
            "Make the typewriter and desk the clear focal point in the 9:16 composition. "
            "Extend the room naturally where needed so the image feels like a complete standalone scene. "
            "Keep the lighting moody, warm, and intimate. "
            f"Narration context: {subtitle_context}"
        )

    elif shot_sequence == 5:
        seed_id = "seed_shot_05_failure_mirror"
        prompt = (
            f"{common_rules} "
            "Use the source screenshot only as reference for the embedded illustration. "
            "Focus on the bathroom sink and mirror scene: simplified figure from behind, mirror reflection, "
            "hourglass, steaming mug, tiled wall, and subdued introspective mood. "
            "Recompose so the mirror and reflection dominate the vertical frame while still keeping the sink, "
            "hourglass, and mug visible. "
            "The emotion should feel like strain, self-judgment, and discomfort. "
            "Do not make it supernatural or horror-like. "
            f"Narration context: {subtitle_context}"
        )

    elif shot_sequence == 6:
        seed_id = "seed_shot_06_letting_go"
        prompt = (
            f"{common_rules} "
            "Use the source screenshot only as reference for the embedded illustration. "
            "Focus on the cliffside release scene: simplified figure on grassy edge, black briefcase mid-drop, "
            "ocean below, warm sunset sky, and distant birds. "
            "Recompose into a strong vertical frame with enough sky and ocean space for later animation. "
            "The emotion should feel calm, freeing, and symbolic rather than dramatic. "
            f"Narration context: {subtitle_context}"
        )

    else:
        raise RuntimeError(f"Unsupported shot for seed generation: {shot_sequence}")

    return SeedSpec(
        seed_id=seed_id,
        shot_sequence=shot_sequence,
        asset_id=asset_id,
        local_image_path=str(local_image_path),
        output_image_path=str(output_path),
        reel_start_seconds=float(shot["start_seconds"]),
        reel_end_seconds=float(shot["end_seconds"]),
        subtitle_cue_ids=list(shot["primary_subtitle_cue_ids"]),
        prompt=prompt,
        shot_goal=shot["shot_goal"],
        source_support=shot["source_support"],
    )


# ============================================================
# MANIFEST
# ============================================================

def build_manifest() -> dict:
    for required in [VISUAL_PLAN_FILE, ANALYZED_ASSETS_FILE, SUBTITLE_CUES_FILE]:
        if not required.exists():
            raise FileNotFoundError(f"Missing required file: {required}")

    visual_plan = load_json(VISUAL_PLAN_FILE)
    analyzed_assets = load_json(ANALYZED_ASSETS_FILE)
    subtitle_cues = load_json(SUBTITLE_CUES_FILE)

    shot_lookup = {
        int(shot["sequence"]): shot
        for shot in visual_plan.get("shots", [])
    }

    asset_lookup = {
        asset["asset_id"]: asset
        for asset in analyzed_assets.get("assets", [])
    }

    cue_lookup = {
        cue["cue_id"]: cue["text"]
        for cue in subtitle_cues.get("cues", [])
    }

    specs: list[SeedSpec] = []

    for shot_sequence in TARGET_SHOTS:
        shot = shot_lookup.get(shot_sequence)
        if not shot:
            raise RuntimeError(f"Shot {shot_sequence} not found in visual_plan.json")

        source_asset_ids = shot.get("source_asset_ids", [])
        if not source_asset_ids:
            raise RuntimeError(f"Shot {shot_sequence} has no source_asset_ids")

        asset_id = source_asset_ids[0]
        asset = asset_lookup.get(asset_id)
        if not asset:
            raise RuntimeError(f"Asset {asset_id} not found in analyzed_assets.json")

        specs.append(build_seed_spec(shot, asset, cue_lookup))

    return {
        "generated_at_utc": now_utc(),
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "model": MODEL,
        "settings": {
            "aspect_ratio": ASPECT_RATIO,
            "output_resolution": OUTPUT_RESOLUTION,
        },
        "shots": [
            {
                "seed_id": spec.seed_id,
                "shot_sequence": spec.shot_sequence,
                "asset_id": spec.asset_id,
                "local_image_path": spec.local_image_path,
                "output_image_path": spec.output_image_path,
                "reel_start_seconds": spec.reel_start_seconds,
                "reel_end_seconds": spec.reel_end_seconds,
                "target_used_duration_seconds": round(
                    spec.reel_end_seconds - spec.reel_start_seconds,
                    3,
                ),
                "subtitle_cue_ids": spec.subtitle_cue_ids,
                "shot_goal": spec.shot_goal,
                "source_support": spec.source_support,
                "prompt": spec.prompt,
            }
            for spec in specs
        ],
    }


# ============================================================
# IMAGE GENERATION
# ============================================================

def generate_seed_image(client: genai.Client, item: dict) -> dict:
    source_path = Path(item["local_image_path"])
    output_path = Path(item["output_image_path"])

    if not source_path.exists():
        raise FileNotFoundError(f"Missing source image: {source_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_image = Image.open(source_path).convert("RGB")

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            source_image,
            item["prompt"],
        ],
        config=types.GenerateContentConfig(
            response_modalities=[
                types.Modality.TEXT,
                types.Modality.IMAGE,
            ],
        ),
    )

    generated_image, text_response = extract_generated_image(response)

    if generated_image is None:
        raise RuntimeError(
            f"No image was returned for {item['seed_id']}."
        )

    generated_image.save(output_path, format="PNG", optimize=True)

    result = {
        "seed_id": item["seed_id"],
        "shot_sequence": item["shot_sequence"],
        "asset_id": item["asset_id"],
        "source_image_path": item["local_image_path"],
        "output_image_path": str(output_path),
        "reel_start_seconds": item["reel_start_seconds"],
        "reel_end_seconds": item["reel_end_seconds"],
        "subtitle_cue_ids": item["subtitle_cue_ids"],
        "completed_at_utc": now_utc(),
    }

    if SAVE_TEXT_RESPONSE:
        result["model_text_response"] = text_response

    return result


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare clean Veo seed images using Gemini image editing."
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only create metadata/veo_seed_manifest.json and exit.",
    )

    parser.add_argument(
        "--shot",
        type=int,
        choices=TARGET_SHOTS,
        help="Generate only one seed image as a harness test.",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all selected seed images.",
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    args = parse_args()

    manifest = build_manifest()
    save_json(MANIFEST_FILE, manifest)

    if args.plan_only:
        write_status(
            "SUCCESS\n"
            f"Manifest only.\n"
            f"Output: {MANIFEST_FILE}\n"
        )
        print()
        print("Veo seed manifest created.")
        print(f"Manifest: {MANIFEST_FILE}")
        print()
        return

    selected_items = manifest["shots"]

    if args.shot is not None:
        selected_items = [
            item
            for item in selected_items
            if int(item["shot_sequence"]) == args.shot
        ]

    elif not args.all:
        # Default harness behavior: only shot 2 first.
        selected_items = [
            item
            for item in selected_items
            if int(item["shot_sequence"]) == 2
        ]

    if not selected_items:
        raise RuntimeError("No seed items selected.")

    write_status(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Items: {len(selected_items)}\n"
    )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(api_version="v1"),
    )

    results = {
        "generated_at_utc": now_utc(),
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "model": MODEL,
        "settings": manifest["settings"],
        "results": [],
    }

    total = len(selected_items)

    for index, item in enumerate(selected_items, start=1):
        print()
        print(f"Generating seed {index}/{total}: {item['seed_id']}")

        result = generate_seed_image(client, item)
        results["results"].append(result)
        save_json(RESULTS_FILE, results)

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Results: {RESULTS_FILE}\n"
    )

    print()
    print("Seed image generation completed.")
    print(f"Manifest: {MANIFEST_FILE}")
    print(f"Results: {RESULTS_FILE}")
    for item in results["results"]:
        print(f"- {item['output_image_path']}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text("FAILED\n\n" + error, encoding="utf-8")

        print()
        print("Seed image generation failed.")
        print(f"See: {STATUS_FILE}")
        print()

        sys.exit(1)
