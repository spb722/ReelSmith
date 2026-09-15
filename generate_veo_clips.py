from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "gen-lang-client-0240752803")

# Veo 3.1 is documented as available in us-central1.
# If your account or SDK setup prefers "global", override with:
#   export GOOGLE_CLOUD_LOCATION=global
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

MODEL = "veo-3.1-fast-generate-001"

VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")

MANIFEST_FILE = Path("metadata/veo_generation_manifest.json")
RESULTS_FILE = Path("metadata/veo_generation_results.json")
STATUS_FILE = Path("metadata/veo_generation_status.txt")

DEFAULT_OUTPUT_GCS_URI = os.getenv(
    "VEO_OUTPUT_GCS_URI",
    "",
)

ASPECT_RATIO = "9:16"
RESOLUTION = os.getenv("VEO_RESOLUTION", "720p")
DURATION_SECONDS = 8
NUMBER_OF_VIDEOS = int(os.getenv("VEO_NUMBER_OF_VIDEOS", "1"))

GENERATE_AUDIO = False
ENHANCE_PROMPT = True

POLL_SECONDS = 15
PIPELINE_SCHEMA_VERSION = "1.0"


# ============================================================
# SHOT SELECTION
# ============================================================

# After reviewing visual_plan.json, these are the strongest Veo moments:
# - Shot 2: Bukowski / rejection phase
# - Shot 5: self-acceptance / mirror beat
# - Shot 6: letting go / briefcase release
#
# This is a slight refinement from the broader discussion:
# Shot 6 is visually more kinetic than Shot 3 and makes better use of Veo.
TARGET_SHOTS = [2, 5, 6]


@dataclass
class ClipSpec:
    clip_id: str
    shot_sequence: int
    asset_id: str
    local_image_path: str
    reel_start_seconds: float
    reel_end_seconds: float
    subtitle_cue_ids: list[str]
    prompt: str
    negative_prompt: str
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def write_status(text: str) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(text, encoding="utf-8")


def find_asset_map(analyzed_assets: dict) -> dict[str, dict]:
    return {
        asset["asset_id"]: asset
        for asset in analyzed_assets.get("assets", [])
    }


def find_shot_map(visual_plan: dict) -> dict[int, dict]:
    return {
        int(shot["sequence"]): shot
        for shot in visual_plan.get("shots", [])
    }


def resolve_local_image_path(asset: dict) -> Path:
    source_path = asset.get("source_path")
    if not source_path:
        raise RuntimeError(
            f"Asset {asset.get('asset_id')} is missing source_path."
        )

    path = Path(source_path)
    if path.exists():
        return path

    # Fallback to cwd / source_path
    candidate = Path.cwd() / source_path
    if candidate.exists():
        return candidate

    raise FileNotFoundError(
        "Could not resolve local source image for "
        f"{asset.get('asset_id')}: {source_path}"
    )


def join_cue_texts(shot: dict, cue_lookup: dict[str, str] | None = None) -> str:
    cue_ids = shot.get("primary_subtitle_cue_ids", [])
    if not cue_ids or not cue_lookup:
        return ""
    texts = [cue_lookup[cue_id] for cue_id in cue_ids if cue_id in cue_lookup]
    return " ".join(texts).strip()


def safe_get(operation: Any, path: list[str], default: Any = None) -> Any:
    current = operation
    for key in path:
        if current is None:
            return default

        if isinstance(current, dict):
            current = current.get(key)
            continue

        current = getattr(current, key, None)

    return current if current is not None else default


def extract_generated_video_uris(operation: Any) -> list[str]:
    candidates = []

    for path in [
        ["result", "generated_videos"],
        ["response", "generated_videos"],
    ]:
        videos = safe_get(operation, path, default=[])
        if not videos:
            continue

        for item in videos:
            uri = safe_get(item, ["video", "uri"], default=None)
            if uri:
                candidates.append(uri)

    # Deduplicate while preserving order.
    seen = set()
    ordered = []
    for uri in candidates:
        if uri not in seen:
            seen.add(uri)
            ordered.append(uri)

    return ordered


def start_generation(
    client: genai.Client,
    prompt: str,
    config: types.GenerateVideosConfig,
) -> Any:
    """
    Be resilient to minor SDK signature differences.
    """

    try:
        return client.models.generate_videos(
            model=MODEL,
            prompt=prompt,
            config=config,
        )
    except TypeError:
        return client.models.generate_videos(
            model=MODEL,
            source=types.GenerateVideosSource(
                prompt=prompt,
            ),
            config=config,
        )


# ============================================================
# PROMPT WRITING
# ============================================================

def build_clip_spec(
    shot: dict,
    asset: dict,
    cue_lookup: dict[str, str],
) -> ClipSpec:
    asset_id = asset["asset_id"]
    local_path = resolve_local_image_path(asset)

    shot_sequence = int(shot["sequence"])
    subtitle_text = join_cue_texts(shot, cue_lookup)

    if shot_sequence == 2:
        prompt = (
            "Create an 8-second vertical editorial illustration-to-video clip "
            "that stays faithful to the provided reference image. "
            "Scene: a dim, humble writer's room at night with a wooden desk, "
            "vintage typewriter, ashtray with a thin curl of smoke, stained coffee mug, "
            "crumpled papers, and an unmade bed in the background. "
            "Emotion: long struggle, rejection, persistence without glamour. "
            "Motion: slow cinematic push-in toward the desk, faint cigarette smoke drift, "
            "very subtle paper movement, tiny warm desk-lamp flicker, almost no camera shake. "
            "Keep the illustrated, textured, slightly grainy editorial storybook look "
            "from the source image. Do not turn it into photoreal live action. "
            "Do not add subtitles or extra text. "
            f"Narration beat: {subtitle_text}"
        )
        negative_prompt = (
            "photorealistic live action, modern office, extra characters, "
            "visible subtitle text, logo, watermark, distorted anatomy, "
            "camera shake, fast motion, surreal mutations"
        )
        clip_id = "veo_clip_01_shot_02_bukowski_rejection"

    elif shot_sequence == 5:
        prompt = (
            "Create an 8-second vertical editorial illustration-to-video clip "
            "that stays faithful to the provided reference image. "
            "Scene: a simplified human figure seen from behind at a bathroom sink, "
            "looking into a mirror where the reflected face appears subdued and introspective. "
            "An hourglass and a steaming mug sit on the sink counter. "
            "Emotion: self-acceptance, stillness, honesty, quiet emotional release. "
            "Motion: slow push-in, tiny breathing or shoulder movement, faint steam drift from the mug, "
            "subtle movement in the reflected expression, slight hourglass sand motion. "
            "Maintain the original illustrated texture and mature editorial tone. "
            "Do not make it photorealistic. Do not add subtitles or extra text. "
            f"Narration beat: {subtitle_text}"
        )
        negative_prompt = (
            "photorealistic live action, horror mirror, exaggerated facial distortion, "
            "extra people, visible subtitle text, logo, watermark, fast motion, surreal body changes"
        )
        clip_id = "veo_clip_02_shot_05_accepting_self"

    elif shot_sequence == 6:
        prompt = (
            "Create an 8-second vertical editorial illustration-to-video clip "
            "that stays faithful to the provided reference image. "
            "Scene: a simple figure on a grassy cliff at sunset lets a black briefcase "
            "fall into the ocean below. Warm orange-pink sky, distant birds, a feeling of release. "
            "Emotion: letting go, freedom from obsession, bold calm action. "
            "Motion: slow lateral camera drift, natural briefcase drop, slight grass and cloud motion, "
            "subtle ocean shimmer, restrained cinematic pacing. "
            "Maintain the original illustrated storybook/editorial style and texture. "
            "Do not make it photorealistic. Do not add subtitles or extra text. "
            f"Narration beat: {subtitle_text}"
        )
        negative_prompt = (
            "photorealistic live action, extreme action scene, dramatic explosions, "
            "extra people, visible subtitle text, logo, watermark, camera shake, surreal distortions"
        )
        clip_id = "veo_clip_03_shot_06_letting_go"

    else:
        raise RuntimeError(f"Unsupported shot selection: {shot_sequence}")

    return ClipSpec(
        clip_id=clip_id,
        shot_sequence=shot_sequence,
        asset_id=asset_id,
        local_image_path=str(local_path),
        reel_start_seconds=float(shot["start_seconds"]),
        reel_end_seconds=float(shot["end_seconds"]),
        subtitle_cue_ids=list(shot["primary_subtitle_cue_ids"]),
        prompt=prompt,
        negative_prompt=negative_prompt,
        shot_goal=shot["shot_goal"],
        source_support=shot["source_support"],
    )


# ============================================================
# MANIFEST
# ============================================================

def build_manifest() -> dict:
    if not VISUAL_PLAN_FILE.exists():
        raise FileNotFoundError(f"Missing file: {VISUAL_PLAN_FILE}")

    if not ANALYZED_ASSETS_FILE.exists():
        raise FileNotFoundError(f"Missing file: {ANALYZED_ASSETS_FILE}")

    visual_plan = load_json(VISUAL_PLAN_FILE)
    analyzed_assets = load_json(ANALYZED_ASSETS_FILE)

    asset_map = find_asset_map(analyzed_assets)
    shot_map = find_shot_map(visual_plan)

    cue_lookup: dict[str, str] = {}
    # Allow missing subtitle cue text gracefully if not present.
    # visual_plan.json already contains cue ids; the script only uses
    # cue text to enrich prompts.
    subtitle_cues_path = Path("metadata/subtitle_cues.json")
    if subtitle_cues_path.exists():
        subtitle_cues = load_json(subtitle_cues_path)
        for cue in subtitle_cues.get("cues", []):
            cue_lookup[cue["cue_id"]] = cue["text"]

    clips: list[ClipSpec] = []

    for shot_sequence in TARGET_SHOTS:
        if shot_sequence not in shot_map:
            raise RuntimeError(f"Shot {shot_sequence} not found in visual_plan.json")

        shot = shot_map[shot_sequence]
        source_asset_ids = shot.get("source_asset_ids", [])

        if not source_asset_ids:
            raise RuntimeError(f"Shot {shot_sequence} has no source assets.")

        primary_asset_id = source_asset_ids[0]

        if primary_asset_id not in asset_map:
            raise RuntimeError(
                f"Asset {primary_asset_id} from shot {shot_sequence} "
                "not found in analyzed_assets.json"
            )

        asset = asset_map[primary_asset_id]
        clips.append(
            build_clip_spec(
                shot=shot,
                asset=asset,
                cue_lookup=cue_lookup,
            )
        )

    manifest = {
        "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
        "generated_at_utc": now_utc(),
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "model": MODEL,
        "settings": {
            "aspect_ratio": ASPECT_RATIO,
            "resolution": RESOLUTION,
            "duration_seconds": DURATION_SECONDS,
            "number_of_videos": NUMBER_OF_VIDEOS,
            "generate_audio": GENERATE_AUDIO,
            "enhance_prompt": ENHANCE_PROMPT,
            "output_gcs_uri": DEFAULT_OUTPUT_GCS_URI,
        },
        "notes": [
            "Selected Veo moments are limited to the strongest motion-worthy beats.",
            "Each clip is generated as an 8-second 9:16 source clip and will be trimmed in the final edit.",
            "The rest of the reel should remain in the Remotion pipeline using still-art motion and typography.",
        ],
        "clips": [
            {
                "clip_id": clip.clip_id,
                "shot_sequence": clip.shot_sequence,
                "asset_id": clip.asset_id,
                "local_image_path": clip.local_image_path,
                "reel_start_seconds": clip.reel_start_seconds,
                "reel_end_seconds": clip.reel_end_seconds,
                "target_used_duration_seconds": round(
                    clip.reel_end_seconds - clip.reel_start_seconds,
                    3,
                ),
                "subtitle_cue_ids": clip.subtitle_cue_ids,
                "shot_goal": clip.shot_goal,
                "source_support": clip.source_support,
                "prompt": clip.prompt,
                "negative_prompt": clip.negative_prompt,
            }
            for clip in clips
        ],
    }

    return manifest


# ============================================================
# GENERATION
# ============================================================

def generate_clip(
    client: genai.Client,
    clip: dict,
    output_gcs_uri: str,
) -> dict:
    image = types.Image.from_file(location=clip["local_image_path"])

    config = types.GenerateVideosConfig(
        number_of_videos=NUMBER_OF_VIDEOS,
        duration_seconds=DURATION_SECONDS,
        aspect_ratio=ASPECT_RATIO,
        resolution=RESOLUTION,
        output_gcs_uri=output_gcs_uri,
        reference_images=[
            types.VideoGenerationReferenceImage(
                image=image,
                reference_type="asset",
            )
        ],
        generate_audio=GENERATE_AUDIO,
        enhance_prompt=ENHANCE_PROMPT,
        negative_prompt=clip["negative_prompt"],
        person_generation="allow_adult",
        seed=1000 + int(clip["shot_sequence"]),
    )

    operation = start_generation(
        client=client,
        prompt=clip["prompt"],
        config=config,
    )

    operation_name = safe_get(operation, ["name"], default=None)

    while not safe_get(operation, ["done"], default=False):
        time.sleep(POLL_SECONDS)
        operation = client.operations.get(operation)

    uris = extract_generated_video_uris(operation)

    result = {
        "clip_id": clip["clip_id"],
        "shot_sequence": clip["shot_sequence"],
        "operation_name": operation_name,
        "done": bool(safe_get(operation, ["done"], default=False)),
        "generated_video_uris": uris,
        "reel_start_seconds": clip["reel_start_seconds"],
        "reel_end_seconds": clip["reel_end_seconds"],
        "target_used_duration_seconds": clip["target_used_duration_seconds"],
    }

    if not uris:
        raise RuntimeError(
            f"No generated video URIs returned for {clip['clip_id']}."
        )

    return result


def run_generation(manifest: dict) -> dict:
    output_gcs_uri = manifest["settings"].get("output_gcs_uri", "").strip()

    if not output_gcs_uri:
        raise RuntimeError(
            "Missing output GCS URI.\n"
            "Set environment variable VEO_OUTPUT_GCS_URI, for example:\n"
            "export VEO_OUTPUT_GCS_URI=gs://your-bucket/book_reels/veo/"
        )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(api_version="v1"),
    )

    results = {
        "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
        "generated_at_utc": now_utc(),
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "model": MODEL,
        "output_gcs_uri": output_gcs_uri,
        "clips": [],
    }

    total = len(manifest["clips"])

    for index, clip in enumerate(manifest["clips"], start=1):
        write_status(
            "RUNNING\n"
            f"Started: {now_utc()}\n"
            f"Model: {MODEL}\n"
            f"Location: {LOCATION}\n"
            f"Clip {index}/{total}: {clip['clip_id']}\n"
        )

        clip_result = generate_clip(
            client=client,
            clip=clip,
            output_gcs_uri=output_gcs_uri,
        )

        results["clips"].append(clip_result)
        save_json(RESULTS_FILE, results)

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Output: {RESULTS_FILE}\n"
    )

    return results


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a Veo clip manifest and optionally generate the selected clips."
        )
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only write metadata/veo_generation_manifest.json and exit.",
    )

    return parser.parse_args()


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
        print("Veo generation manifest created.")
        print(f"Manifest: {MANIFEST_FILE}")
        print()
        return

    results = run_generation(manifest)

    print()
    print("Veo generation completed.")
    print(f"Manifest: {MANIFEST_FILE}")
    print(f"Results: {RESULTS_FILE}")
    print("Generated clips:")
    for clip in results["clips"]:
        print(f"- {clip['clip_id']}: {clip['generated_video_uris']}")
    print()


if __name__ == "__main__":
    try:
        main()

    except Exception:
        error = traceback.format_exc()

        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(
            "FAILED\n\n" + error,
            encoding="utf-8",
        )

        print()
        print("Veo generation failed.")
        print(f"See: {STATUS_FILE}")
        print()

        sys.exit(1)
