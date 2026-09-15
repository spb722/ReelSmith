from __future__ import annotations

import json
import mimetypes
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ID = "gen-lang-client-0240752803"
LOCATION = "global"
MODEL = "gemini-2.5-flash"

ASSETS_FILE = Path("metadata/assets.json")

ANALYSIS_DIR = Path("metadata/asset_analysis")
RAW_DIR = ANALYSIS_DIR / "raw"
ERROR_DIR = ANALYSIS_DIR / "errors"

MERGED_OUTPUT_FILE = Path("metadata/analyzed_assets.json")
STATUS_FILE = Path("metadata/asset_analysis_status.txt")
LOG_FILE = Path("metadata/asset_analysis.log")

# Set to an integer if you want to test only N assets.
# Example:
# MAX_ASSETS = 1
#
# Keep None to process every discovered image.
MAX_ASSETS = None

RETRIES_PER_ASSET = 3
RETRY_BASE_SECONDS = 2

ANALYSIS_SCHEMA_VERSION = "2.0"


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are the visual content analyst for an automated
short-form storytelling and video-generation system.

You receive exactly ONE source image at a time.

Your job is to extract factual, semantic, spatial, visual,
and production metadata from that ONE image.

You are NOT the storyteller yet.

IMPORTANT RULES:

1. Do NOT create final narration.

2. Do NOT decide the final story order.

3. Do NOT assume the filename has semantic meaning.

4. Do NOT assume upload order or manifest order has
   narrative meaning.

5. Separate STORY CONTENT from APPLICATION / INTERFACE CONTENT.

6. Transcribe visible story text as faithfully as possible,
   preserving wording, punctuation, and capitalization.

7. Do not rewrite, improve, shorten, summarize, or correct
   visible source text while transcribing it.

8. App interface text such as buttons, progress indicators,
   navigation controls, page numbers, menus, or labels must be
   separated from actual story text.

9. Analyze useful artwork independently from surrounding app UI.

10. Do not identify real people from appearance alone.

11. Named people, books, concepts, places, organizations, or
    works may be recorded only when explicitly stated in visible
    text or otherwise clearly supported by the image.

12. Do not invent details.

13. If something is uncertain, record that uncertainty.

14. possible_story_roles are only editorial possibilities.
    They are NOT the final story structure.

15. suggested_motion should be subtle and appropriate to what is
    actually present in the artwork.

16. visual_cleanup_needed should identify presentation elements
    that should probably not appear in the final Reel.

17. Spatial regions use normalized integer coordinates from 0 to
    1000 relative to the FULL source image:
       x_min=0 is the left edge
       y_min=0 is the top edge
       x_max=1000 is the right edge
       y_max=1000 is the bottom edge

18. Region estimates do not need to be pixel-perfect, but should
    be useful for later automated cropping and recomposition.

19. A "visual subject" may be a person, illustrated person,
    cartoon character, object, landmark, environment, symbol,
    or other meaningful visual element.

20. Keep factual extraction separate from later creative writing.

Return only JSON matching the requested response schema.
""".strip()


# ============================================================
# RESPONSE SCHEMA
# ============================================================

REGION_SCHEMA = {
    "type": "object",
    "properties": {
        "x_min": {"type": "integer"},
        "y_min": {"type": "integer"},
        "x_max": {"type": "integer"},
        "y_max": {"type": "integer"},
    },
    "required": [
        "x_min",
        "y_min",
        "x_max",
        "y_max",
    ],
}


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {

        # ----------------------------------------------------
        # SOURCE TYPE
        # ----------------------------------------------------

        "content_type": {
            "type": "string",
            "description": (
                "General source type, for example "
                "illustrated_story_card, screenshot, photograph, "
                "quote_card, infographic, book_page, or other."
            ),
        },

        # ----------------------------------------------------
        # SOURCE TEXT
        # ----------------------------------------------------

        "source_text": {
            "type": "object",
            "properties": {

                "heading": {
                    "type": "string",
                    "description": (
                        "Main visible story heading. Empty string if none."
                    ),
                },

                "body_text": {
                    "type": "string",
                    "description": (
                        "Main visible story paragraph(s), transcribed "
                        "faithfully. Empty string if absent."
                    ),
                },

                "prominent_quote": {
                    "type": "string",
                    "description": (
                        "Prominent quotation/emphasized phrase. "
                        "Empty string if absent."
                    ),
                },

                "other_story_text": {
                    "type": "array",
                    "items": {"type": "string"},
                },

                "ui_text": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Visible app/interface text rather than story text."
                    ),
                },

                "verbatim_blocks": {
                    "type": "array",
                    "description": (
                        "Visible text blocks in reading order. Keep text "
                        "verbatim and give an approximate normalized region."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "reading_order": {"type": "integer"},
                            "block_type": {
                                "type": "string",
                                "enum": [
                                    "HEADING",
                                    "BODY",
                                    "QUOTE",
                                    "STORY_LABEL",
                                    "UI",
                                    "OTHER",
                                ],
                            },
                            "text": {"type": "string"},
                            "region": REGION_SCHEMA,
                        },
                        "required": [
                            "reading_order",
                            "block_type",
                            "text",
                            "region",
                        ],
                    },
                },

                "transcription_confidence": {
                    "type": "number",
                    "description": (
                        "Overall confidence from 0.0 to 1.0 in the "
                        "story-text transcription."
                    ),
                },
            },

            "required": [
                "heading",
                "body_text",
                "prominent_quote",
                "other_story_text",
                "ui_text",
                "verbatim_blocks",
                "transcription_confidence",
            ],
        },

        # ----------------------------------------------------
        # VISUAL CONTENT
        # ----------------------------------------------------

        "visual": {
            "type": "object",
            "properties": {

                "description": {
                    "type": "string",
                    "description": (
                        "Objective description of the useful artwork "
                        "or photograph."
                    ),
                },

                "main_elements": {
                    "type": "array",
                    "items": {"type": "string"},
                },

                "visual_subjects": {
                    "type": "array",
                    "description": (
                        "Important visible subjects with approximate regions."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject_type": {
                                "type": "string",
                                "enum": [
                                    "REAL_PERSON",
                                    "ILLUSTRATED_PERSON",
                                    "CARTOON_CHARACTER",
                                    "OBJECT",
                                    "LANDMARK",
                                    "ENVIRONMENT",
                                    "SYMBOL",
                                    "OTHER",
                                ],
                            },
                            "description": {"type": "string"},
                            "region": REGION_SCHEMA,
                        },
                        "required": [
                            "subject_type",
                            "description",
                            "region",
                        ],
                    },
                },

                "real_people_visible": {
                    "type": "boolean",
                    "description": (
                        "True only for photographic/real human figures, "
                        "not cartoon or illustrated characters."
                    ),
                },

                "illustrated_or_cartoon_figures_visible": {
                    "type": "boolean",
                },

                "figure_descriptions": {
                    "type": "array",
                    "items": {"type": "string"},
                },

                "environment": {
                    "type": "string",
                    "description": (
                        "Setting/environment shown. Empty string if unclear."
                    ),
                },

                "important_actions": {
                    "type": "array",
                    "items": {"type": "string"},
                },

                "composition_notes": {
                    "type": "string",
                },
            },

            "required": [
                "description",
                "main_elements",
                "visual_subjects",
                "real_people_visible",
                "illustrated_or_cartoon_figures_visible",
                "figure_descriptions",
                "environment",
                "important_actions",
                "composition_notes",
            ],
        },

        # ----------------------------------------------------
        # SEMANTICS
        # ----------------------------------------------------

        "semantic_summary": {
            "type": "object",
            "properties": {
                "core_idea": {
                    "type": "string",
                    "description": (
                        "A concise factual statement of what this ONE "
                        "asset is communicating. This is not narration."
                    ),
                },
                "concepts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "emotional_tone": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "requires_external_context": {
                    "type": "boolean",
                },
                "context_needed": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "core_idea",
                "concepts",
                "emotional_tone",
                "requires_external_context",
                "context_needed",
            ],
        },

        # ----------------------------------------------------
        # NAMED ENTITIES
        # ----------------------------------------------------

        "named_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entity_type": {
                        "type": "string",
                        "description": (
                            "Examples: person, book, philosophy, place, "
                            "organization, work, concept."
                        ),
                    },
                    "evidence": {
                        "type": "string",
                    },
                },
                "required": [
                    "name",
                    "entity_type",
                    "evidence",
                ],
            },
        },

        # ----------------------------------------------------
        # POSSIBLE STORY ROLES
        # ----------------------------------------------------

        "possible_story_roles": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "HOOK",
                    "SETUP",
                    "CONTEXT",
                    "EXAMPLE",
                    "CONFLICT",
                    "BUILDUP",
                    "REVERSAL",
                    "REVEAL",
                    "EXPLANATION",
                    "PAYOFF",
                    "REFLECTION",
                    "ENDING",
                    "OTHER",
                ],
            },
        },

        # ----------------------------------------------------
        # PRODUCTION INFORMATION
        # ----------------------------------------------------

        "production": {
            "type": "object",
            "properties": {

                "contains_app_ui": {
                    "type": "boolean",
                },

                "story_art_region_present": {
                    "type": "boolean",
                },

                "story_art_region": REGION_SCHEMA,

                "story_text_region": REGION_SCHEMA,

                "ui_regions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "region": REGION_SCHEMA,
                        },
                        "required": [
                            "description",
                            "region",
                        ],
                    },
                },

                "story_art_description": {
                    "type": "string",
                },

                "vertical_video_suitability": {
                    "type": "string",
                    "enum": [
                        "high",
                        "medium",
                        "low",
                    ],
                },

                "recommended_crop_strategy": {
                    "type": "string",
                    "enum": [
                        "USE_FULL_FRAME",
                        "CROP_TO_ART",
                        "CROP_TO_TEXT_AND_ART",
                        "RECOMPOSE",
                        "NEEDS_GENERATION",
                    ],
                },

                "safe_to_crop_ui_without_losing_story": {
                    "type": "boolean",
                },

                "suggested_motion": {
                    "type": "array",
                    "items": {"type": "string"},
                },

                "visual_cleanup_needed": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },

            "required": [
                "contains_app_ui",
                "story_art_region_present",
                "story_art_region",
                "story_text_region",
                "ui_regions",
                "story_art_description",
                "vertical_video_suitability",
                "recommended_crop_strategy",
                "safe_to_crop_ui_without_losing_story",
                "suggested_motion",
                "visual_cleanup_needed",
            ],
        },

        # ----------------------------------------------------
        # UNCERTAINTIES
        # ----------------------------------------------------

        "uncertainties": {
            "type": "array",
            "items": {"type": "string"},
        },
    },

    "required": [
        "content_type",
        "source_text",
        "visual",
        "semantic_summary",
        "named_entities",
        "possible_story_roles",
        "production",
        "uncertainties",
    ],
}


# ============================================================
# HELPERS
# ============================================================

def now_utc() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def ensure_directories() -> None:
    ANALYSIS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    RAW_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    ERROR_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            f"[{now_utc()}] {message}\n"
        )


def write_status(text: str) -> None:
    STATUS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATUS_FILE.write_text(
        text,
        encoding="utf-8",
    )


def load_json(path: Path) -> dict:
    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def save_json(
    path: Path,
    data: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )

    temporary.replace(path)


def determine_mime_type(
    image_path: Path,
) -> str:

    mime_type, _ = mimetypes.guess_type(
        image_path.name
    )

    if mime_type:
        return mime_type

    fallback = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }

    extension = image_path.suffix.lower()

    if extension in fallback:
        return fallback[extension]

    raise RuntimeError(
        f"Could not determine MIME type for: {image_path}"
    )


def clamp_int(
    value,
    low: int,
    high: int,
) -> int:

    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low

    return max(
        low,
        min(
            high,
            number,
        ),
    )


def normalize_region(
    region: dict | None,
) -> dict:

    if not isinstance(
        region,
        dict,
    ):
        region = {}

    x_min = clamp_int(
        region.get("x_min", 0),
        0,
        1000,
    )

    y_min = clamp_int(
        region.get("y_min", 0),
        0,
        1000,
    )

    x_max = clamp_int(
        region.get("x_max", 1000),
        0,
        1000,
    )

    y_max = clamp_int(
        region.get("y_max", 1000),
        0,
        1000,
    )

    if x_max < x_min:
        x_min, x_max = x_max, x_min

    if y_max < y_min:
        y_min, y_max = y_max, y_min

    return {
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
    }


def normalize_analysis(
    analysis: dict,
) -> dict:
    """
    Clamp Gemini's approximate normalized regions into a
    predictable 0..1000 range.
    """

    source_text = analysis.get(
        "source_text",
        {},
    )

    for block in source_text.get(
        "verbatim_blocks",
        [],
    ):
        block["region"] = normalize_region(
            block.get("region")
        )

    visual = analysis.get(
        "visual",
        {},
    )

    for subject in visual.get(
        "visual_subjects",
        [],
    ):
        subject["region"] = normalize_region(
            subject.get("region")
        )

    production = analysis.get(
        "production",
        {},
    )

    production["story_art_region"] = (
        normalize_region(
            production.get(
                "story_art_region"
            )
        )
    )

    production["story_text_region"] = (
        normalize_region(
            production.get(
                "story_text_region"
            )
        )
    )

    for ui_region in production.get(
        "ui_regions",
        [],
    ):
        ui_region["region"] = normalize_region(
            ui_region.get("region")
        )

    confidence = source_text.get(
        "transcription_confidence",
        0.0,
    )

    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    source_text[
        "transcription_confidence"
    ] = max(
        0.0,
        min(
            1.0,
            confidence,
        ),
    )

    return analysis


# ============================================================
# ASSET ANALYSIS
# ============================================================

def analyze_asset_once(
    client: genai.Client,
    asset: dict,
) -> dict:

    asset_id = asset["asset_id"]

    image_path = Path(
        asset["source_path"]
    )

    if not image_path.exists():
        raise FileNotFoundError(
            f"Image file does not exist: "
            f"{image_path.resolve()}"
        )

    mime_type = determine_mime_type(
        image_path
    )

    image_bytes = image_path.read_bytes()

    user_prompt = f"""
Analyze this ONE source image for the automated
short-form storytelling pipeline.

Internal asset ID:
{asset_id}

The internal asset ID exists only for tracking.
It carries no semantic or narrative meaning.

This stage is IMAGE UNDERSTANDING only.

Extract:

- visible story text faithfully
- visible UI text separately
- text blocks and approximate regions
- useful artwork and visual subjects
- approximate art/text/UI regions
- strongly supported semantic concepts
- explicitly supported named entities
- emotional tone
- possible narrative functions
- whether external context is required
- likely crop/recomposition strategy
- subtle motion ideas
- cleanup requirements
- genuine uncertainty

Do NOT:

- write final narration
- determine final story order
- assume filename meaning
- treat manifest order as narrative order
- invent unsupported facts

The later Story Director will receive ALL analyzed assets
together and make creative decisions.
""".strip()

    response = client.models.generate_content(
        model=MODEL,

        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type,
            ),

            types.Part.from_text(
                text=user_prompt,
            ),
        ],

        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            max_output_tokens=8192,
        ),
    )

    if not response.text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    raw_file = (
        RAW_DIR
        / f"{asset_id}.json"
    )

    raw_file.write_text(
        response.text,
        encoding="utf-8",
    )

    try:
        analysis = json.loads(
            response.text
        )

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Gemini response for {asset_id} "
            f"could not be parsed as JSON. "
            f"Raw response saved to {raw_file}."
        ) from exc

    analysis = normalize_analysis(
        analysis
    )

    return {
        "asset_id": asset_id,
        "source_path": str(image_path),
        "original_filename": asset.get(
            "original_filename",
            image_path.name,
        ),
        "model": MODEL,
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analyzed_at_utc": now_utc(),
        "analysis": analysis,
    }


def analyze_asset_with_retry(
    client: genai.Client,
    asset: dict,
) -> dict:

    last_error = None

    for attempt in range(
        1,
        RETRIES_PER_ASSET + 1,
    ):

        try:
            return analyze_asset_once(
                client=client,
                asset=asset,
            )

        except Exception as exc:
            last_error = exc

            asset_id = asset.get(
                "asset_id",
                "unknown",
            )

            log(
                f"Asset={asset_id} "
                f"attempt={attempt} failed: {exc}"
            )

            if attempt < RETRIES_PER_ASSET:
                wait_seconds = (
                    RETRY_BASE_SECONDS
                    * (2 ** (attempt - 1))
                )

                time.sleep(
                    wait_seconds
                )

    raise RuntimeError(
        f"Asset analysis failed after "
        f"{RETRIES_PER_ASSET} attempts. "
        f"Last error: {last_error}"
    )


# ============================================================
# MERGED OUTPUT
# ============================================================

def rebuild_merged_output(
    manifest: dict,
) -> dict:

    results = []

    for asset in manifest.get(
        "assets",
        [],
    ):

        asset_id = asset["asset_id"]

        result_file = (
            ANALYSIS_DIR
            / f"{asset_id}.json"
        )

        if not result_file.exists():
            continue

        results.append(
            load_json(
                result_file
            )
        )

    merged = {
        "generated_at_utc": now_utc(),
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "model": MODEL,
        "asset_count_in_manifest": len(
            manifest.get(
                "assets",
                [],
            )
        ),
        "analyzed_asset_count": len(
            results
        ),
        "important_note": (
            "Array order is processing order only and "
            "must not be treated as narrative order."
        ),
        "assets": results,
    }

    save_json(
        MERGED_OUTPUT_FILE,
        merged,
    )

    return merged


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    ensure_directories()

    if not ASSETS_FILE.exists():
        raise FileNotFoundError(
            f"Missing manifest: "
            f"{ASSETS_FILE.resolve()}"
        )

    manifest = load_json(
        ASSETS_FILE
    )

    assets = manifest.get(
        "assets",
        [],
    )

    if not assets:
        raise RuntimeError(
            "metadata/assets.json contains no assets."
        )

    if MAX_ASSETS is not None:
        assets = assets[
            :MAX_ASSETS
        ]

    write_status(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Assets scheduled: {len(assets)}\n"
    )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(
            api_version="v1"
        ),
    )

    completed = 0
    skipped = 0
    failed = 0

    for position, asset in enumerate(
        assets,
        start=1,
    ):

        asset_id = asset["asset_id"]

        result_file = (
            ANALYSIS_DIR
            / f"{asset_id}.json"
        )

        # Resume support:
        # If a result already exists using this schema version,
        # do not pay to analyze it again.
        if result_file.exists():

            try:
                existing = load_json(
                    result_file
                )

                if (
                    existing.get(
                        "schema_version"
                    )
                    == ANALYSIS_SCHEMA_VERSION
                ):
                    skipped += 1

                    print(
                        f"[{position}/{len(assets)}] "
                        f"Skipping {asset_id} "
                        f"(already analyzed)"
                    )

                    continue

            except Exception:
                pass

        write_status(
            "RUNNING\n"
            f"Updated: {now_utc()}\n"
            f"Model: {MODEL}\n"
            f"Current asset: {asset_id}\n"
            f"Position: {position}/{len(assets)}\n"
            f"Completed: {completed}\n"
            f"Skipped: {skipped}\n"
            f"Failed: {failed}\n"
        )

        print(
            f"[{position}/{len(assets)}] "
            f"Analyzing {asset_id}..."
        )

        try:
            result = analyze_asset_with_retry(
                client=client,
                asset=asset,
            )

            save_json(
                result_file,
                result,
            )

            completed += 1

            # Update the merged file immediately so successful
            # work is never lost if a later image fails.
            rebuild_merged_output(
                manifest
            )

        except Exception:

            failed += 1

            error_text = traceback.format_exc()

            error_file = (
                ERROR_DIR
                / f"{asset_id}.txt"
            )

            error_file.write_text(
                error_text,
                encoding="utf-8",
            )

            log(
                f"Asset={asset_id} failed permanently. "
                f"See {error_file}."
            )

            print(
                f"  FAILED. See {error_file}"
            )

    merged = rebuild_merged_output(
        manifest
    )

    final_status = (
        "SUCCESS"
        if failed == 0
        else "PARTIAL_SUCCESS"
    )

    write_status(
        f"{final_status}\n"
        f"Finished: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Newly analyzed: {completed}\n"
        f"Skipped existing: {skipped}\n"
        f"Failed: {failed}\n"
        f"Total available analyses: "
        f"{merged['analyzed_asset_count']}\n"
        f"Merged output: {MERGED_OUTPUT_FILE}\n"
    )

    print()
    print(
        f"{final_status}: "
        f"{merged['analyzed_asset_count']} "
        f"asset analysis file(s) available."
    )

    print(
        f"Merged output: "
        f"{MERGED_OUTPUT_FILE}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception:

        ensure_directories()

        error = traceback.format_exc()

        write_status(
            "FAILED\n\n"
            + error
        )

        log(
            "Fatal error:\n"
            + error
        )

        print()
        print(
            "Asset analysis failed."
        )
        print(
            f"See: {STATUS_FILE}"
        )

        sys.exit(1)
