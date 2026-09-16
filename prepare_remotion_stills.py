from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
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

MANIFEST_FILE = Path("metadata/remotion_still_manifest.json")
RESULTS_FILE = Path("metadata/remotion_still_results.json")
STATUS_FILE = Path("metadata/remotion_still_status.txt")

OUTPUT_DIR = Path("generated/remotion_stills")

TARGET_SHOTS = [1, 3, 4, 7, 8]

ASPECT_RATIO = "9:16"


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


def resolve_existing_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.exists():
        return path

    candidate = Path.cwd() / path_str
    if candidate.exists():
        return candidate

    raise FileNotFoundError(f"Missing source image: {path_str}")


def extract_generated_image(response) -> Image.Image:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")

    parts = getattr(candidates[0].content, "parts", None) or []

    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            return Image.open(BytesIO(inline.data)).convert("RGB")

    raise RuntimeError("Gemini returned no image data.")


def subtitle_text_for_shot(
    shot: dict,
    cue_lookup: dict[str, str],
) -> str:
    return " ".join(
        cue_lookup.get(cue_id, "")
        for cue_id in shot.get("primary_subtitle_cue_ids", [])
    ).strip()


# ============================================================
# PROMPTS
# ============================================================

def build_prompt(
    shot_sequence: int,
    subtitle_context: str,
) -> str:
    common = (
        "Use the provided mobile screenshot only as visual reference for the embedded illustration. "
        "Create a clean standalone vertical 9:16 editorial illustration for a cinematic short-form reel. "
        "Remove all app UI, status bars, buttons, progress indicators, card chrome, captions, and surrounding interface. "
        "Do not include any visible text, letters, numbers, logos, or watermarks. "
        "Preserve the mature illustrated storybook/editorial aesthetic, muted warm palette, texture, and symbolic simplicity. "
        "Do not make the image photorealistic. "
        "Do not add extra characters unless explicitly requested. "
        "Leave enough clean visual space for later subtitles and typography in Remotion. "
    )

    if shot_sequence == 1:
        return (
            common
            + "Create the opening hook image around the tombstone scene. "
              "Show EXACTLY ONE tombstone only. Do not duplicate the tombstone. "
              "Do not show a second grave marker, reflection, foreground duplicate, background duplicate, or repeating stone shape anywhere in the frame. "
              "A single weathered stone grave marker stands in a warm field beneath a broad atmospheric sky. "
              "The composition should feel quiet, weighty, and slightly mysterious. "
              "Do not put any writing on the tombstone; Remotion will add the words later. "
              "Place the single tombstone in the lower-middle portion of the frame, with generous open sky above it for a slow cinematic push-in. "
              "Keep the foreground simple and uncluttered so the image reads clearly as one focal subject. "
              f"Narration context: {subtitle_context}"
        )

    if shot_sequence == 3:
        return (
            common
            + "Use the same tombstone-world visual language, but make this shot feel softer and more accepting than the opening. "
              "Show warm grass, open sky, subdued late-afternoon light, and the tombstone as a quieter secondary anchor. "
              "The composition should communicate acceptance rather than death or drama. "
              "Leave room for subtle Remotion drift and a restrained overlay. "
              f"Narration context: {subtitle_context}"
        )

    if shot_sequence == 4:
        return (
            common
            + "Create a symbolic visual explanation of the Backwards Law rather than recreating a text card. "
              "Use a restrained conceptual composition suggesting that chasing something makes it recede: "
              "for example, a simple figure reaching toward a warm glowing point that appears farther away, "
              "while a calmer open space behind or around the figure suggests release. "
              "Keep it elegant, minimal, philosophical, and visually readable in vertical format. "
              "Do not include any words; Remotion will add 'The Backwards Law' as typography. "
              f"Narration context: {subtitle_context}"
        )

    if shot_sequence == 7:
        return (
            common
            + "Continue the cliff and ocean visual world from the previous letting-go shot, "
              "but remove the briefcase as the focal action and shift attention to the open horizon. "
              "Show expansive ocean, warm sky, small distant birds, subtle cliff edge or grass in the foreground, "
              "and a calm feeling of release after action. "
              "Keep the composition open and spacious for a gentle Remotion drift. "
              f"Narration context: {subtitle_context}"
        )

    if shot_sequence == 8:
        return (
            common
            + "Create the final reflective image using the tombstone landscape as a visual callback, "
              "but frame mostly the open sky and warm field with the tombstone small and understated. "
              "The mood should be contemplative, unresolved, and spacious rather than somber. "
              "Leave generous negative space for the final reflection question and a very slow drift. "
              "Do not include any writing on the tombstone. "
              f"Narration context: {subtitle_context}"
        )

    raise RuntimeError(f"Unsupported still shot: {shot_sequence}")


# ============================================================
# MANIFEST
# ============================================================

def build_manifest() -> dict:
    for required in [
        VISUAL_PLAN_FILE,
        ANALYZED_ASSETS_FILE,
        SUBTITLE_CUES_FILE,
    ]:
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

    items = []

    for shot_sequence in TARGET_SHOTS:
        shot = shot_lookup.get(shot_sequence)
        if not shot:
            raise RuntimeError(
                f"Shot {shot_sequence} not found in visual_plan.json"
            )

        asset_ids = shot.get("source_asset_ids", [])
        if not asset_ids:
            raise RuntimeError(
                f"Shot {shot_sequence} has no source asset."
            )

        asset_id = asset_ids[0]
        asset = asset_lookup.get(asset_id)

        if not asset:
            raise RuntimeError(
                f"Asset {asset_id} not found for shot {shot_sequence}."
            )

        source_path = resolve_existing_path(asset["source_path"])
        subtitle_context = subtitle_text_for_shot(
            shot,
            cue_lookup,
        )

        output_path = (
            OUTPUT_DIR
            / f"shot_{shot_sequence:02d}_still.png"
        )

        items.append(
            {
                "shot_sequence": shot_sequence,
                "asset_id": asset_id,
                "source_image_path": str(source_path),
                "output_image_path": str(output_path),
                "reel_start_seconds": float(
                    shot["start_seconds"]
                ),
                "reel_end_seconds": float(
                    shot["end_seconds"]
                ),
                "subtitle_cue_ids": list(
                    shot["primary_subtitle_cue_ids"]
                ),
                "shot_goal": shot["shot_goal"],
                "prompt": build_prompt(
                    shot_sequence,
                    subtitle_context,
                ),
            }
        )

    return {
        "generated_at_utc": now_utc(),
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "model": MODEL,
        "aspect_ratio": ASPECT_RATIO,
        "shots": items,
    }


# ============================================================
# GENERATION
# ============================================================

def generate_one(
    client: genai.Client,
    item: dict,
) -> dict:
    source_path = Path(
        item["source_image_path"]
    )

    output_path = Path(
        item["output_image_path"]
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with Image.open(source_path) as source:
        source = source.convert("RGB")

        response = client.models.generate_content(
            model=MODEL,
            contents=[
                source,
                item["prompt"],
            ],
            config=types.GenerateContentConfig(
                response_modalities=[
                    types.Modality.TEXT,
                    types.Modality.IMAGE,
                ],
            ),
        )

    generated = extract_generated_image(
        response
    )

    generated.save(
        output_path,
        format="PNG",
        optimize=True,
    )

    return {
        "shot_sequence": item["shot_sequence"],
        "asset_id": item["asset_id"],
        "source_image_path": item["source_image_path"],
        "output_image_path": str(output_path),
        "reel_start_seconds": item["reel_start_seconds"],
        "reel_end_seconds": item["reel_end_seconds"],
        "subtitle_cue_ids": item["subtitle_cue_ids"],
        "completed_at_utc": now_utc(),
    }


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate clean 9:16 still assets for non-Veo Remotion shots."
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only write metadata/remotion_still_manifest.json.",
    )

    parser.add_argument(
        "--shot",
        type=int,
        choices=TARGET_SHOTS,
        help="Generate only one still shot.",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all remaining still shots.",
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    args = parse_args()

    manifest = build_manifest()
    save_json(
        MANIFEST_FILE,
        manifest,
    )

    if args.plan_only:
        write_status(
            "SUCCESS\n"
            f"Manifest: {MANIFEST_FILE}\n"
        )
        print()
        print("Remotion still manifest created.")
        print(MANIFEST_FILE)
        print()
        return

    selected = manifest["shots"]

    if args.shot is not None:
        selected = [
            item
            for item in selected
            if int(item["shot_sequence"]) == args.shot
        ]
    elif not args.all:
        # Safe harness default: only Shot 1.
        selected = [
            item
            for item in selected
            if int(item["shot_sequence"]) == 1
        ]

    if not selected:
        raise RuntimeError("No still shots selected.")

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(
            api_version="v1"
        ),
    )

    results = {
        "generated_at_utc": now_utc(),
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "model": MODEL,
        "results": [],
    }

    write_status(
        "RUNNING\n"
        f"Shots: {len(selected)}\n"
    )

    for index, item in enumerate(
        selected,
        start=1,
    ):
        print()
        print(
            f"Generating still {index}/{len(selected)}: "
            f"Shot {item['shot_sequence']}"
        )

        result = generate_one(
            client,
            item,
        )

        results["results"].append(
            result
        )

        save_json(
            RESULTS_FILE,
            results,
        )

    write_status(
        "SUCCESS\n"
        f"Results: {RESULTS_FILE}\n"
    )

    print()
    print("Still generation completed.")
    print(f"Results: {RESULTS_FILE}")
    for item in results["results"]:
        print(f"- {item['output_image_path']}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        STATUS_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        STATUS_FILE.write_text(
            "FAILED\n\n" + error,
            encoding="utf-8",
        )

        print()
        print("Still generation failed.")
        print(f"See: {STATUS_FILE}")
        print()

        sys.exit(1)
