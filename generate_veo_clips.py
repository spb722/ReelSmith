from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types


# ============================================================
# CONFIG
# ============================================================

PROJECT_ID = os.getenv(
    "GOOGLE_CLOUD_PROJECT",
    "gen-lang-client-0240752803",
)

LOCATION = os.getenv(
    "GOOGLE_CLOUD_LOCATION",
    "global",
)

MODEL = "veo-3.1-fast-generate-001"

VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")
SEED_RESULTS_FILE = Path("metadata/veo_seed_results.json")

MANIFEST_FILE = Path("metadata/veo_generation_manifest.json")
RESULTS_FILE = Path("metadata/veo_generation_results.json")
STATUS_FILE = Path("metadata/veo_generation_status.txt")
LAST_OPERATION_FILE = Path("metadata/veo_last_operation.json")
LOCAL_VIDEO_DIR = Path("generated/veo")

OUTPUT_GCS_URI = os.getenv("VEO_OUTPUT_GCS_URI", "").strip()

ASPECT_RATIO = "9:16"
RESOLUTION = os.getenv("VEO_RESOLUTION", "720p")
DURATION_SECONDS = 8
NUMBER_OF_VIDEOS = int(os.getenv("VEO_NUMBER_OF_VIDEOS", "1"))
GENERATE_AUDIO = False
ENHANCE_PROMPT = True
POLL_SECONDS = 15

TARGET_SHOTS = [2, 5, 6]


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


def get_value(obj: Any, path: list[str], default: Any = None) -> Any:
    current = obj
    for key in path:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
    return current if current is not None else default


def operation_to_jsonable(operation: Any) -> dict:
    """
    Best-effort serialization of the completed long-running operation.
    This is saved before we decide whether generation succeeded.
    """
    if hasattr(operation, "model_dump"):
        try:
            return operation.model_dump(
                mode="json",
                exclude_none=False,
            )
        except Exception:
            pass

    if hasattr(operation, "to_json_dict"):
        try:
            return operation.to_json_dict()
        except Exception:
            pass

    return {
        "repr": repr(operation),
    }


def generated_videos_from_operation(operation: Any) -> list[Any]:
    for root in ["response", "result"]:
        generated_videos = get_value(
            operation,
            [root, "generated_videos"],
            default=[],
        )
        if generated_videos:
            return list(generated_videos)

    return []


def extract_video_outputs(
    operation: Any,
    clip_id: str,
) -> tuple[list[str], list[str]]:
    """
    Return:
      (GCS URIs, local MP4 paths)

    With output_gcs_uri configured, Veo normally returns a URI.
    The SDK can also return inline video bytes, so we support both.
    """
    uris: list[str] = []
    local_paths: list[str] = []

    generated_videos = generated_videos_from_operation(
        operation
    )

    for index, generated in enumerate(
        generated_videos,
        start=1,
    ):
        video = get_value(
            generated,
            ["video"],
        )

        if video is None:
            continue

        uri = get_value(
            video,
            ["uri"],
        )

        if uri and uri not in uris:
            uris.append(uri)

        video_bytes = get_value(
            video,
            ["video_bytes"],
        )

        if video_bytes:
            LOCAL_VIDEO_DIR.mkdir(
                parents=True,
                exist_ok=True,
            )

            local_path = (
                LOCAL_VIDEO_DIR
                / f"{clip_id}_{index:02d}.mp4"
            )

            if isinstance(
                video_bytes,
                str,
            ):
                payload = base64.b64decode(
                    video_bytes
                )
            else:
                payload = bytes(
                    video_bytes
                )

            local_path.write_bytes(
                payload
            )

            local_paths.append(
                str(local_path)
            )

    return uris, local_paths


def resolve_existing_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.exists():
        return path

    candidate = Path.cwd() / path_str
    if candidate.exists():
        return candidate

    raise FileNotFoundError(f"Missing file: {path_str}")


def join_cue_texts(cue_ids: list[str], cue_lookup: dict[str, str]) -> str:
    return " ".join(cue_lookup.get(cue_id, "") for cue_id in cue_ids).strip()


# ============================================================
# PROMPTS
# ============================================================

def build_prompt_for_shot(shot_sequence: int, subtitle_text: str) -> tuple[str, str, str]:
    """
    Returns:
      clip_id, prompt, negative_prompt
    """
    if shot_sequence == 2:
        return (
            "veo_clip_01_shot_02_bukowski_rejection",
            (
                "Animate the provided illustration as the exact starting frame. "
                "Preserve the illustrated editorial storybook style, textures, warm desk lighting, "
                "typewriter, ashtray with smoke, coffee mug, crumpled papers, chair, wooden table, "
                "and the unmade bed in the background. "
                "Emotion: long struggle, rejection, and persistence without glamour. "
                "Motion must remain restrained: a slow cinematic push-in toward the desk and typewriter, "
                "faint cigarette smoke drift, tiny light flicker, and barely perceptible movement in the room. "
                "Do not add text, subtitles, logos, or extra people. "
                "Do not make it photorealistic. "
                f"Narration context: {subtitle_text}"
            ),
            (
                "photorealistic live action, modern office, extra people, extra text, subtitles, logo, "
                "watermark, camera shake, fast motion, surreal distortions"
            ),
        )

    if shot_sequence == 5:
        return (
            "veo_clip_02_shot_05_failure_mirror",
            (
                "Animate the provided illustration as the exact starting frame. "
                "Preserve the bathroom setting, mirror, tiled wall, sink, simplified figure from behind, "
                "hourglass, mug, and subdued editorial illustration style. "
                "Emotion: strain, self-judgment, discomfort, and the feeling of chasing success while still "
                "feeling inadequate. "
                "Motion should be subtle: slow push toward the mirror, slight body stillness with tiny breathing, "
                "faint steam from the mug, minimal sand motion in the hourglass, and restrained life in the reflection. "
                "Do not make it supernatural or horror-like. "
                "Do not add text, subtitles, logos, or extra people. "
                "Do not make it photorealistic. "
                f"Narration context: {subtitle_text}"
            ),
            (
                "photorealistic live action, horror mirror, supernatural reflection, extra people, extra text, "
                "subtitles, logo, watermark, fast motion, surreal body changes"
            ),
        )

    if shot_sequence == 6:
        return (
            "veo_clip_03_shot_06_letting_go",
            (
                "Animate the provided illustration as the exact starting frame. "
                "Preserve the simple figure on the grassy cliff, the black briefcase, ocean, warm sunset sky, "
                "and distant birds in the same illustrated editorial storybook style. "
                "Emotion: letting go of the outcome, release, and quiet freedom. "
                "Motion: restrained lateral camera drift, natural briefcase drop, subtle grass motion, gentle cloud drift, "
                "small bird movement, and soft ocean shimmer. "
                "The pacing should feel calm and cinematic, not dramatic. "
                "Do not add text, subtitles, logos, or extra people. "
                "Do not make it photorealistic. "
                f"Narration context: {subtitle_text}"
            ),
            (
                "photorealistic live action, extreme action, explosion, extra people, extra text, subtitles, "
                "logo, watermark, camera shake, surreal distortions"
            ),
        )

    raise RuntimeError(f"Unsupported shot sequence: {shot_sequence}")


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

    seed_lookup: dict[int, dict] = {}
    if SEED_RESULTS_FILE.exists():
        seed_results = load_json(SEED_RESULTS_FILE)
        for item in seed_results.get("results", []):
            seed_lookup[int(item["shot_sequence"])] = item

    shot_lookup = {int(shot["sequence"]): shot for shot in visual_plan.get("shots", [])}
    asset_lookup = {asset["asset_id"]: asset for asset in analyzed_assets.get("assets", [])}
    cue_lookup = {cue["cue_id"]: cue["text"] for cue in subtitle_cues.get("cues", [])}

    manifest_clips = []

    for shot_sequence in TARGET_SHOTS:
        shot = shot_lookup.get(shot_sequence)
        if not shot:
            raise RuntimeError(f"Shot {shot_sequence} not found in visual_plan.json")

        asset_ids = shot.get("source_asset_ids", [])
        if not asset_ids:
            raise RuntimeError(f"Shot {shot_sequence} has no source asset ids")

        asset_id = asset_ids[0]
        if asset_id not in asset_lookup:
            raise RuntimeError(f"Missing asset for shot {shot_sequence}: {asset_id}")

        subtitle_text = join_cue_texts(shot["primary_subtitle_cue_ids"], cue_lookup)
        clip_id, prompt, negative_prompt = build_prompt_for_shot(
            shot_sequence,
            subtitle_text,
        )

        # Prefer the deterministic AI-cleaned seed file on disk.
        #
        # Important: veo_seed_results.json may contain only the most recently
        # generated shot if seed generation was run one shot at a time.
        # Therefore the existence of the actual seed image is authoritative.
        deterministic_seed_path = (
            Path("generated/veo_seeds")
            / f"shot_{shot_sequence:02d}_seed.png"
        )

        if deterministic_seed_path.exists():
            image_path = str(deterministic_seed_path)
            image_source = "AI_RECOMPOSED_SEED"
        elif shot_sequence in seed_lookup:
            image_path = seed_lookup[shot_sequence]["output_image_path"]
            image_source = "AI_RECOMPOSED_SEED"
        else:
            image_path = asset_lookup[asset_id]["source_path"]
            image_source = "ORIGINAL_SOURCE_IMAGE"

        resolved = resolve_existing_path(image_path)

        manifest_clips.append(
            {
                "clip_id": clip_id,
                "shot_sequence": shot_sequence,
                "asset_id": asset_id,
                "input_image_path": str(resolved),
                "input_image_source": image_source,
                "reel_start_seconds": float(shot["start_seconds"]),
                "reel_end_seconds": float(shot["end_seconds"]),
                "target_used_duration_seconds": round(
                    float(shot["end_seconds"]) - float(shot["start_seconds"]),
                    3,
                ),
                "subtitle_cue_ids": list(shot["primary_subtitle_cue_ids"]),
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "shot_goal": shot["shot_goal"],
                "source_support": shot["source_support"],
            }
        )

    return {
        "generated_at_utc": now_utc(),
        "project_id": PROJECT_ID,
        "location": LOCATION,
        "model": MODEL,
        "generation_mode": "IMAGE_TO_VIDEO",
        "settings": {
            "aspect_ratio": ASPECT_RATIO,
            "resolution": RESOLUTION,
            "duration_seconds": DURATION_SECONDS,
            "number_of_videos": NUMBER_OF_VIDEOS,
            "generate_audio": GENERATE_AUDIO,
            "enhance_prompt": ENHANCE_PROMPT,
            "output_gcs_uri": OUTPUT_GCS_URI,
        },
        "clips": manifest_clips,
    }


# ============================================================
# GENERATION
# ============================================================

def generate_one_clip(client: genai.Client, clip: dict) -> dict:
    image_path = resolve_existing_path(clip["input_image_path"])
    image = types.Image.from_file(location=str(image_path))

    config = types.GenerateVideosConfig(
        number_of_videos=NUMBER_OF_VIDEOS,
        duration_seconds=DURATION_SECONDS,
        aspect_ratio=ASPECT_RATIO,
        resolution=RESOLUTION,
        output_gcs_uri=OUTPUT_GCS_URI,
        generate_audio=GENERATE_AUDIO,
        enhance_prompt=ENHANCE_PROMPT,
        negative_prompt=clip["negative_prompt"],
        person_generation="allow_adult",
        seed=1000 + int(clip["shot_sequence"]),
    )

    operation = client.models.generate_videos(
        model=MODEL,
        prompt=clip["prompt"],
        image=image,
        config=config,
    )

    operation_name = get_value(operation, ["name"])

    while not get_value(operation, ["done"], default=False):
        print(f"Waiting for {clip['clip_id']}...")
        time.sleep(POLL_SECONDS)
        operation = client.operations.get(operation)

    # Save the completed operation before interpreting it.
    operation_debug = operation_to_jsonable(
        operation
    )
    save_json(
        LAST_OPERATION_FILE,
        operation_debug,
    )

    generated_video_uris, local_video_paths = (
        extract_video_outputs(
            operation,
            clip["clip_id"],
        )
    )

    filtered_count = get_value(
        operation,
        [
            "response",
            "rai_media_filtered_count",
        ],
        default=0,
    )

    filtered_reasons = get_value(
        operation,
        [
            "response",
            "rai_media_filtered_reasons",
        ],
        default=[],
    )

    operation_error = get_value(
        operation,
        ["error"],
        default=None,
    )

    if (
        not generated_video_uris
        and not local_video_paths
    ):
        raise RuntimeError(
            "Veo completed but returned no usable video.\n"
            f"Clip: {clip['clip_id']}\n"
            f"Operation: {operation_name}\n"
            f"RAI filtered count: {filtered_count}\n"
            f"RAI filtered reasons: {filtered_reasons}\n"
            f"Operation error: {operation_error}\n"
            f"Raw completed operation saved to: "
            f"{LAST_OPERATION_FILE}"
        )

    return {
        "clip_id": clip["clip_id"],
        "shot_sequence": clip["shot_sequence"],
        "asset_id": clip["asset_id"],
        "input_image_path": clip["input_image_path"],
        "input_image_source": clip["input_image_source"],
        "operation_name": operation_name,
        "generated_video_uris": generated_video_uris,
        "local_video_paths": local_video_paths,
        "rai_media_filtered_count": filtered_count,
        "rai_media_filtered_reasons": filtered_reasons,
        "reel_start_seconds": clip["reel_start_seconds"],
        "reel_end_seconds": clip["reel_end_seconds"],
        "target_used_duration_seconds": clip["target_used_duration_seconds"],
        "completed_at_utc": now_utc(),
    }


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Veo image-to-video clips from cleaned seed images."
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only write metadata/veo_generation_manifest.json and exit.",
    )
    parser.add_argument(
        "--shot",
        type=int,
        choices=TARGET_SHOTS,
        help="Generate only one selected shot.",
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
            "Manifest only.\n"
            f"Output: {MANIFEST_FILE}\n"
        )
        print()
        print("Veo generation manifest created.")
        print(f"Manifest: {MANIFEST_FILE}")
        print()
        return

    if not OUTPUT_GCS_URI:
        raise RuntimeError(
            "VEO_OUTPUT_GCS_URI is not set.\n"
            "Example:\n"
            "export VEO_OUTPUT_GCS_URI=gs://sachin-kayaking-video-test/book_reels/veo/"
        )

    clips = manifest["clips"]
    if args.shot is not None:
        clips = [clip for clip in clips if int(clip["shot_sequence"]) == args.shot]

    if not clips:
        raise RuntimeError("No clips selected.")

    write_status(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Clips: {len(clips)}\n"
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
        "generation_mode": "IMAGE_TO_VIDEO",
        "output_gcs_uri": OUTPUT_GCS_URI,
        "clips": [],
    }

    total = len(clips)

    for index, clip in enumerate(clips, start=1):
        print()
        print(f"Generating {index}/{total}: {clip['clip_id']}")
        write_status(
            "RUNNING\n"
            f"Model: {MODEL}\n"
            f"Clip {index}/{total}\n"
            f"Clip ID: {clip['clip_id']}\n"
        )

        result = generate_one_clip(client, clip)
        results["clips"].append(result)
        save_json(RESULTS_FILE, results)

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Results: {RESULTS_FILE}\n"
    )

    print()
    print("Veo generation completed.")
    print(f"Manifest: {MANIFEST_FILE}")
    print(f"Results: {RESULTS_FILE}")
    for clip in results["clips"]:
        for uri in clip["generated_video_uris"]:
            print(f"- {uri}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text("FAILED\n\n" + error, encoding="utf-8")

        print()
        print("Veo generation failed.")
        print(f"See: {STATUS_FILE}")
        print()

        sys.exit(1)
