from __future__ import annotations

import json
import re
import sys
import time
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ID = "gen-lang-client-0240752803"
LOCATION = "global"
MODEL = "gemini-3.1-pro-preview"

ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
STORY_PLAN_FILE = Path("metadata/final_story_plan.json")
VOICE_METADATA_FILE = Path("metadata/voice_generation.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")

OUTPUT_JSON = Path("metadata/visual_plan.json")
OUTPUT_TEXT = Path("metadata/visual_plan.txt")
STATUS_FILE = Path("metadata/visual_plan_status.txt")

MAX_API_RETRIES = 3
RETRY_BASE_SECONDS = 2

PIPELINE_SCHEMA_VERSION = "1.0"


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are the Visual Director for a premium short-form vertical video.

You receive:
- the final approved story plan
- the final subtitle cues with real timestamps
- analyzed source assets extracted from the original image set
- optional voice-generation timing metadata

Your job is to create a grounded shot-by-shot visual edit plan for the
actual narration duration.

GOAL
Create a compelling 9:16 short-form reel with a mature, reflective tone.
Use the existing source assets intelligently. Prefer subtle motion,
crop/recompose, texture, and typography over gratuitous AI video.

IMPORTANT RULES

1. Ground visuals in the supplied source assets.
2. Do not invent unsupported source facts.
3. Respect the locked narration and subtitle timing.
4. Design for the ACTUAL audio timeline, not an old estimated duration.
5. Prefer one shot to span 1-3 subtitle cues when it feels natural.
6. Every shot must be contiguous in time with no gaps or overlaps.
7. The last shot must end exactly at the final subtitle end time.
8. Use the smallest useful number of shots. Aim for clear editorial rhythm,
   not hyperactive cutting.
9. Use AI_VIDEO_CANDIDATE sparingly and only when the source images are not
   enough for the emotional beat.
10. Explicitly identify which source asset(s) support each shot.
11. For every shot, explain the motion/edit treatment in concrete terms.
12. Give text-overlay guidance separately from subtitles. Subtitle text already
   exists; only recommend extra impact text when useful.
13. “Don't try.” is a major moment and deserves clear visual emphasis.
14. The closing reflection should feel open, spacious, and calm.

Use these treatment labels only:
- USE_EXISTING_ART
- CROP_AND_RECOMPOSE
- SUBTLE_ANIMATION
- TEXT_LED
- AI_VIDEO_CANDIDATE
- MIXED

Return only JSON matching the required schema.
""".strip()


# ============================================================
# RESPONSE SCHEMA
# ============================================================

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "visual_concept": {
            "type": "object",
            "properties": {
                "overall_approach": {"type": "string"},
                "visual_tone": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "editing_style": {"type": "string"},
                "subtitle_strategy": {"type": "string"},
                "asset_usage_strategy": {"type": "string"},
                "ai_video_policy": {"type": "string"},
            },
            "required": [
                "overall_approach",
                "visual_tone",
                "editing_style",
                "subtitle_strategy",
                "asset_usage_strategy",
                "ai_video_policy",
            ],
        },
        "global_style_guide": {
            "type": "object",
            "properties": {
                "format": {"type": "string"},
                "camera_motion_language": {"type": "string"},
                "transition_language": {"type": "string"},
                "text_treatment": {"type": "string"},
                "background_and_texture_notes": {"type": "string"},
                "color_and_contrast_notes": {"type": "string"},
            },
            "required": [
                "format",
                "camera_motion_language",
                "transition_language",
                "text_treatment",
                "background_and_texture_notes",
                "color_and_contrast_notes",
            ],
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sequence": {"type": "integer"},
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "narrative_function": {
                        "type": "string",
                        "enum": [
                            "HOOK",
                            "SETUP",
                            "STORY",
                            "PIVOT",
                            "EXPLANATION",
                            "PRINCIPLE",
                            "REFLECTION",
                            "ENDING",
                        ],
                    },
                    "primary_subtitle_cue_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source_asset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "visual_treatment": {
                        "type": "string",
                        "enum": [
                            "USE_EXISTING_ART",
                            "CROP_AND_RECOMPOSE",
                            "SUBTLE_ANIMATION",
                            "TEXT_LED",
                            "AI_VIDEO_CANDIDATE",
                            "MIXED",
                        ],
                    },
                    "shot_goal": {"type": "string"},
                    "frame_composition": {"type": "string"},
                    "motion_plan": {"type": "string"},
                    "text_overlay": {"type": "string"},
                    "subtitle_emphasis_notes": {"type": "string"},
                    "transition_in": {"type": "string"},
                    "transition_out": {"type": "string"},
                    "source_support": {"type": "string"},
                },
                "required": [
                    "sequence",
                    "start_seconds",
                    "end_seconds",
                    "narrative_function",
                    "primary_subtitle_cue_ids",
                    "source_asset_ids",
                    "visual_treatment",
                    "shot_goal",
                    "frame_composition",
                    "motion_plan",
                    "text_overlay",
                    "subtitle_emphasis_notes",
                    "transition_in",
                    "transition_out",
                    "source_support",
                ],
            },
        },
        "asset_utilization": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "usage_summary": {"type": "string"},
                    "shot_sequences": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "priority": {
                        "type": "string",
                        "enum": [
                            "PRIMARY",
                            "SECONDARY",
                            "OPTIONAL",
                            "UNUSED",
                        ],
                    },
                },
                "required": [
                    "asset_id",
                    "usage_summary",
                    "shot_sequences",
                    "priority",
                ],
            },
        },
        "editor_notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "visual_concept",
        "global_style_guide",
        "shots",
        "asset_utilization",
        "editor_notes",
    ],
}


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


def make_source_bundle(analyzed_assets: dict) -> list[dict]:
    bundle = []

    for asset in analyzed_assets.get("assets", []):
        analysis = asset.get("analysis", {})
        source_text = analysis.get("source_text", {})
        semantic = analysis.get("semantic_summary", {})
        visual = analysis.get("visual", {})
        production = analysis.get("production", {})

        bundle.append(
            {
                "asset_id": asset.get("asset_id", ""),
                "source_text": {
                    "heading": source_text.get("heading", ""),
                    "body_text": source_text.get("body_text", ""),
                    "prominent_quote": source_text.get("prominent_quote", ""),
                    "other_story_text": source_text.get("other_story_text", []),
                },
                "semantic_summary": {
                    "core_idea": semantic.get("core_idea", ""),
                    "concepts": semantic.get("concepts", []),
                    "emotional_tone": semantic.get("emotional_tone", []),
                },
                "named_entities": analysis.get("named_entities", []),
                "visual": {
                    "description": visual.get("description", ""),
                    "important_actions": visual.get("important_actions", []),
                },
                "production": {
                    "story_art_description": production.get(
                        "story_art_description",
                        "",
                    ),
                },
                "uncertainties": analysis.get("uncertainties", []),
            }
        )

    return bundle


def total_video_duration(subtitle_cues: dict, voice_meta: dict | None) -> float:
    cues = subtitle_cues.get("cues", [])
    if cues:
        return round(float(cues[-1]["end_seconds"]), 3)

    if voice_meta and voice_meta.get("actual_audio_duration_seconds") is not None:
        return round(float(voice_meta["actual_audio_duration_seconds"]), 3)

    raise RuntimeError("Unable to determine final video duration.")


def call_model(client: genai.Client, prompt: str) -> dict:
    last_error = None

    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=types.Part.from_text(text=prompt),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    max_output_tokens=8192,
                ),
            )

            parsed = getattr(response, "parsed", None)
            if parsed is not None:
                if isinstance(parsed, dict):
                    return parsed
                if hasattr(parsed, "model_dump"):
                    return parsed.model_dump()

            if not response.text:
                raise RuntimeError("Visual Director returned an empty response.")

            return json.loads(response.text)

        except Exception as exc:
            last_error = exc
            if attempt < MAX_API_RETRIES:
                time.sleep(RETRY_BASE_SECONDS * (2 ** (attempt - 1)))

    raise RuntimeError(
        f"Visual Director failed after {MAX_API_RETRIES} attempts: {last_error}"
    )


# ============================================================
# VALIDATION
# ============================================================

def validate_shots(
    plan: dict,
    subtitle_cues: dict,
    valid_asset_ids: set[str],
    final_duration: float,
) -> None:
    shots = plan.get("shots", [])
    if not shots:
        raise RuntimeError("No shots were returned.")

    cue_ids = {cue["cue_id"] for cue in subtitle_cues.get("cues", [])}

    previous_end = None

    for expected_sequence, shot in enumerate(shots, start=1):
        if int(shot["sequence"]) != expected_sequence:
            raise RuntimeError(
                f"Shot sequence numbering is invalid at shot {expected_sequence}."
            )

        start = round(float(shot["start_seconds"]), 3)
        end = round(float(shot["end_seconds"]), 3)

        if end <= start:
            raise RuntimeError(f"Shot {expected_sequence} has end <= start.")

        if previous_end is None:
            if abs(start - 0.0) > 0.05:
                raise RuntimeError(
                    f"First shot must start at 0.0, got {start:.3f}."
                )
        else:
            if abs(start - previous_end) > 0.08:
                raise RuntimeError(
                    f"Shot {expected_sequence} is not contiguous. "
                    f"Expected start {previous_end:.3f}, got {start:.3f}."
                )

        if not shot.get("primary_subtitle_cue_ids"):
            raise RuntimeError(f"Shot {expected_sequence} has no subtitle cues.")

        unknown_cues = [
            cue_id
            for cue_id in shot["primary_subtitle_cue_ids"]
            if cue_id not in cue_ids
        ]
        if unknown_cues:
            raise RuntimeError(
                f"Shot {expected_sequence} references unknown cue IDs: {unknown_cues}"
            )

        if not shot.get("source_asset_ids"):
            raise RuntimeError(f"Shot {expected_sequence} has no source assets.")

        unknown_assets = [
            asset_id
            for asset_id in shot["source_asset_ids"]
            if asset_id not in valid_asset_ids
        ]
        if unknown_assets:
            raise RuntimeError(
                f"Shot {expected_sequence} references unknown assets: {unknown_assets}"
            )

        previous_end = end

    if abs(previous_end - final_duration) > 0.08:
        raise RuntimeError(
            f"Last shot must end at {final_duration:.3f}, got {previous_end:.3f}."
        )

    # Require cue coverage to be monotonic and complete across shots.
    cue_list = subtitle_cues.get("cues", [])
    ordered_cue_ids = [cue["cue_id"] for cue in cue_list]
    flattened: list[str] = []

    for shot in shots:
        flattened.extend(shot["primary_subtitle_cue_ids"])

    if flattened != ordered_cue_ids:
        raise RuntimeError(
            "Shot cue coverage must match subtitle cue order exactly.\n"
            f"Expected: {ordered_cue_ids}\n"
            f"Got: {flattened}"
        )

    # Asset utilization entries must reference real assets.
    for item in plan.get("asset_utilization", []):
        asset_id = item.get("asset_id")
        if asset_id not in valid_asset_ids:
            raise RuntimeError(
                f"asset_utilization contains unknown asset_id: {asset_id}"
            )


# ============================================================
# OUTPUT WRITER
# ============================================================

def write_readable_output(plan: dict) -> None:
    lines: list[str] = []

    concept = plan["visual_concept"]
    style = plan["global_style_guide"]

    lines.append("VISUAL PLAN")
    lines.append("")
    lines.append("OVERALL APPROACH")
    lines.append(concept["overall_approach"])
    lines.append("")
    lines.append("VISUAL TONE")
    lines.append(", ".join(concept["visual_tone"]))
    lines.append("")
    lines.append("EDITING STYLE")
    lines.append(concept["editing_style"])
    lines.append("")
    lines.append("STYLE GUIDE")
    lines.append(f"- Format: {style['format']}")
    lines.append(f"- Camera motion: {style['camera_motion_language']}")
    lines.append(f"- Transitions: {style['transition_language']}")
    lines.append(f"- Text treatment: {style['text_treatment']}")
    lines.append(f"- Background/texture: {style['background_and_texture_notes']}")
    lines.append(f"- Color/contrast: {style['color_and_contrast_notes']}")
    lines.append("")
    lines.append("SHOT PLAN")
    lines.append("")

    for shot in plan["shots"]:
        lines.append(
            f"{shot['sequence']:02d}. "
            f"{shot['start_seconds']:.2f}-{shot['end_seconds']:.2f}s "
            f"[{shot['narrative_function']}] "
            f"[{shot['visual_treatment']}]"
        )
        lines.append(f"    Cues: {', '.join(shot['primary_subtitle_cue_ids'])}")
        lines.append(f"    Assets: {', '.join(shot['source_asset_ids'])}")
        lines.append(f"    Goal: {shot['shot_goal']}")
        lines.append(f"    Composition: {shot['frame_composition']}")
        lines.append(f"    Motion: {shot['motion_plan']}")
        lines.append(f"    Overlay: {shot['text_overlay']}")
        lines.append(f"    Subtitle emphasis: {shot['subtitle_emphasis_notes']}")
        lines.append(f"    In: {shot['transition_in']}")
        lines.append(f"    Out: {shot['transition_out']}")
        lines.append(f"    Support: {shot['source_support']}")
        lines.append("")

    lines.append("ASSET UTILIZATION")
    for item in plan["asset_utilization"]:
        lines.append(
            f"- {item['asset_id']} [{item['priority']}] "
            f"shots {item['shot_sequences']}: {item['usage_summary']}"
        )

    lines.append("")
    lines.append("EDITOR NOTES")
    for note in plan["editor_notes"]:
        lines.append(f"- {note}")

    OUTPUT_TEXT.write_text("\n".join(lines), encoding="utf-8")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    for required in [
        ANALYZED_ASSETS_FILE,
        STORY_PLAN_FILE,
        SUBTITLE_CUES_FILE,
    ]:
        if not required.exists():
            raise FileNotFoundError(f"Missing required file: {required}")

    analyzed_assets = load_json(ANALYZED_ASSETS_FILE)
    story_plan = load_json(STORY_PLAN_FILE)
    subtitle_cues = load_json(SUBTITLE_CUES_FILE)

    voice_meta = None
    if VOICE_METADATA_FILE.exists():
        voice_meta = load_json(VOICE_METADATA_FILE)

    source_bundle = make_source_bundle(analyzed_assets)
    valid_asset_ids = {item["asset_id"] for item in source_bundle}
    final_duration = total_video_duration(subtitle_cues, voice_meta)

    prompt = f"""
Create the final visual edit plan for this reel.

Use the ACTUAL final duration and the locked subtitle cue timing.

============================================================
ACTUAL FINAL DURATION
============================================================

{final_duration}

============================================================
FINAL STORY PLAN
============================================================

{json.dumps(story_plan, indent=2, ensure_ascii=False)}

============================================================
SUBTITLE CUES
============================================================

{json.dumps(subtitle_cues, indent=2, ensure_ascii=False)}

============================================================
VOICE GENERATION METADATA
============================================================

{json.dumps(voice_meta or {}, indent=2, ensure_ascii=False)}

============================================================
SOURCE ASSET ANALYSIS
============================================================

{json.dumps(source_bundle, indent=2, ensure_ascii=False)}

Constraints:
- The ordered subtitle cue IDs across all shots must cover every cue exactly once,
  in the original cue order.
- The first shot starts at 0.0 seconds.
- Shot timings must be contiguous.
- The last shot ends at exactly {final_duration} seconds.
- Every shot must reference at least one valid source asset.
- Prefer grounded editorial planning using the source images.

Return only the requested JSON.
""".strip()

    write_status(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Duration: {final_duration}\n"
    )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(api_version="v1"),
    )

    plan = call_model(client, prompt)

    validate_shots(plan, subtitle_cues, valid_asset_ids, final_duration)

    enriched = deepcopy(plan)
    enriched["pipeline_schema_version"] = PIPELINE_SCHEMA_VERSION
    enriched["generated_at_utc"] = now_utc()
    enriched["model"] = MODEL
    enriched["source_files"] = {
        "analyzed_assets": str(ANALYZED_ASSETS_FILE),
        "final_story_plan": str(STORY_PLAN_FILE),
        "voice_generation": str(VOICE_METADATA_FILE) if VOICE_METADATA_FILE.exists() else None,
        "subtitle_cues": str(SUBTITLE_CUES_FILE),
    }
    enriched["final_duration_seconds"] = final_duration

    save_json(OUTPUT_JSON, enriched)
    write_readable_output(enriched)

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Output: {OUTPUT_JSON}\n"
    )

    print()
    print("Visual planning completed.")
    print(f"JSON: {OUTPUT_JSON}")
    print(f"Readable: {OUTPUT_TEXT}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()

        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text("FAILED\n\n" + error, encoding="utf-8")

        print()
        print("Visual planning failed.")
        print(f"See: {STATUS_FILE}")
        print()

        sys.exit(1)
