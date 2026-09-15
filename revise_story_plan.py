from __future__ import annotations

import json
import re
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

STORY_PLAN_FILE = Path("metadata/story_plan.json")
STORY_REVIEW_FILE = Path("metadata/story_review.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")

OUTPUT_DIR = Path("metadata")

REVISED_PLAN_FILE = OUTPUT_DIR / "revised_story_plan.json"
REVISED_TEXT_FILE = OUTPUT_DIR / "revised_story_plan.txt"
RAW_RESPONSE_FILE = OUTPUT_DIR / "story_revision_raw_response.txt"
STATUS_FILE = OUTPUT_DIR / "story_revision_status.txt"
LOG_FILE = OUTPUT_DIR / "story_revision.log"

TARGET_MIN_SECONDS = 40
TARGET_MAX_SECONDS = 50

TARGET_MIN_WORDS = 90
TARGET_MAX_WORDS = 115

MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2

REVISION_SCHEMA_VERSION = "1.0"


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are the Revision Agent in an automated short-form wisdom
video pipeline.

A Story Director created a story plan.
A separate Review Agent evaluated it and produced structured issues
and suggestions.

Your job is to revise the story plan so the important review issues
are resolved while preserving what already works.

You are allowed to rewrite narration, scene structure, timing,
voice direction, and visual intent where needed.

IMPORTANT RULES

1. SOURCE FIDELITY OVERRIDES REVIEWER SUGGESTIONS.
   If a reviewer suggestion asks for a detail that is not explicitly
   supported by the provided source analyses, do NOT invent it.
   Solve the underlying issue in a source-safe way instead.

2. DO NOT USE OUTSIDE KNOWLEDGE.
   Use only the supplied source analyses.

3. DO NOT TRUST FILE ORDER.
   Asset order is not story order.

4. APPLY THE REVIEW INTELLIGENTLY.
   Fix the underlying problem, not merely the wording of the suggestion.

5. PRESERVE STRONG ELEMENTS.
   Keep strong hooks, quotes, story beats, and source-grounded ideas
   unless changing them is necessary to fix a higher-priority issue.

6. SPOKEN STORYTELLING.
   The narration should sound natural when spoken aloud.
   Avoid essay-like, overly compressed, or summary-style prose.

7. VOICE.
   Mature, calm, confident, reflective, slightly intense.
   Avoid clickbait and generic motivational language.

8. TARGET LENGTH.
   Aim for 40-50 seconds and roughly 90-115 spoken words.

9. PACING.
   Keep WPM, word count, and target duration internally consistent.
   A deliberate mature read will usually be slower than a commercial
   or energetic social-media read.

10. SCENE STRUCTURE.
    Use enough scenes to give distinct beats room to land.
    Avoid packing multiple major ideas and visual changes into one
    oversized scene.

11. SOURCE TRACEABILITY.
    Every scene must cite one or more valid source_asset_ids.

12. HANDLE TRUNCATED SOURCE TEXT SAFELY.
    Never invent missing words from a cut-off card.

13. REVIEW RESOLUTION.
    Every issue listed under must_fix_before_next_step must appear in
    review_resolution with a clear explanation of how it was resolved.

14. DO NOT CLAIM AN ISSUE IS RESOLVED IF IT IS NOT ACTUALLY FIXED.

15. RETURN A COMPLETE REVISED STORY PLAN.
    The result must be usable by later voice and video stages.

Return only JSON matching the requested schema.
""".strip()


# ============================================================
# RESPONSE SCHEMA
# ============================================================

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {

        "story_title": {
            "type": "string",
        },

        "core_thesis": {
            "type": "string",
        },

        "narrative_strategy": {
            "type": "string",
        },

        "target_duration_seconds": {
            "type": "number",
        },

        "target_word_count": {
            "type": "integer",
        },

        "hook": {
            "type": "string",
        },

        "narration_script": {
            "type": "string",
        },

        "voice_direction": {
            "type": "object",
            "properties": {

                "persona": {
                    "type": "string",
                },

                "tone": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },

                "pace_wpm": {
                    "type": "integer",
                },

                "delivery_notes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },

                "emphasis_phrases": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },

                "pause_after_phrases": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },

            "required": [
                "persona",
                "tone",
                "pace_wpm",
                "delivery_notes",
                "emphasis_phrases",
                "pause_after_phrases",
            ],
        },

        "story_arc": {
            "type": "object",
            "properties": {

                "opening_tension": {
                    "type": "string",
                },

                "human_or_concrete_story": {
                    "type": "string",
                },

                "reversal_or_reveal": {
                    "type": "string",
                },

                "principle_explanation": {
                    "type": "string",
                },

                "viewer_reflection": {
                    "type": "string",
                },
            },

            "required": [
                "opening_tension",
                "human_or_concrete_story",
                "reversal_or_reveal",
                "principle_explanation",
                "viewer_reflection",
            ],
        },

        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {

                    "sequence": {
                        "type": "integer",
                    },

                    "role": {
                        "type": "string",
                        "enum": [
                            "HOOK",
                            "SETUP",
                            "CONTEXT",
                            "HUMAN_STORY",
                            "BUILDUP",
                            "REVERSAL",
                            "REVEAL",
                            "EXPLANATION",
                            "REFLECTION",
                            "ENDING",
                        ],
                    },

                    "estimated_duration_seconds": {
                        "type": "number",
                    },

                    "narration": {
                        "type": "string",
                    },

                    "source_asset_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },

                    "source_support": {
                        "type": "string",
                    },

                    "visual_intent": {
                        "type": "string",
                    },

                    "suggested_visual_treatment": {
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

                    "impact_text": {
                        "type": "string",
                    },

                    "emotional_goal": {
                        "type": "string",
                    },
                },

                "required": [
                    "sequence",
                    "role",
                    "estimated_duration_seconds",
                    "narration",
                    "source_asset_ids",
                    "source_support",
                    "visual_intent",
                    "suggested_visual_treatment",
                    "impact_text",
                    "emotional_goal",
                ],
            },
        },

        "unused_assets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {

                    "asset_id": {
                        "type": "string",
                    },

                    "reason": {
                        "type": "string",
                    },
                },

                "required": [
                    "asset_id",
                    "reason",
                ],
            },
        },

        "source_integrity_notes": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },

        "review_resolution": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {

                    "issue_id": {
                        "type": "string",
                    },

                    "status": {
                        "type": "string",
                        "enum": [
                            "RESOLVED",
                            "PARTIALLY_RESOLVED",
                            "NOT_APPLICABLE",
                        ],
                    },

                    "resolution": {
                        "type": "string",
                        "description": (
                            "What was changed to address the issue."
                        ),
                    },

                    "source_safety_note": {
                        "type": "string",
                        "description": (
                            "Explain any source-fidelity constraint that "
                            "affected how the reviewer suggestion was applied."
                        ),
                    },
                },

                "required": [
                    "issue_id",
                    "status",
                    "resolution",
                    "source_safety_note",
                ],
            },
        },

        "change_summary": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },

    "required": [
        "story_title",
        "core_thesis",
        "narrative_strategy",
        "target_duration_seconds",
        "target_word_count",
        "hook",
        "narration_script",
        "voice_direction",
        "story_arc",
        "scenes",
        "unused_assets",
        "source_integrity_notes",
        "review_resolution",
        "change_summary",
    ],
}


# ============================================================
# HELPERS
# ============================================================

def now_utc() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


def load_json(
    path: Path,
) -> dict:

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

    temporary.replace(
        path
    )


def write_status(
    text: str,
) -> None:

    ensure_output_dir()

    STATUS_FILE.write_text(
        text,
        encoding="utf-8",
    )


def log(
    message: str,
) -> None:

    ensure_output_dir()

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            f"[{now_utc()}] {message}\n"
        )


def count_words(
    text: str,
) -> int:

    return len(
        re.findall(
            r"\b[\w’'-]+\b",
            text,
            flags=re.UNICODE,
        )
    )


def normalize_text(
    text: str,
) -> str:

    text = (
        text
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# PREPARE SOURCE BUNDLE
# ============================================================

def prepare_source_bundle(
    analyzed_assets: dict,
) -> list[dict]:

    bundle = []

    for asset in analyzed_assets.get(
        "assets",
        []
    ):

        analysis = asset.get(
            "analysis",
            {}
        )

        source_text = analysis.get(
            "source_text",
            {}
        )

        semantic = analysis.get(
            "semantic_summary",
            {}
        )

        visual = analysis.get(
            "visual",
            {}
        )

        production = analysis.get(
            "production",
            {}
        )

        bundle.append(
            {
                "asset_id": asset.get(
                    "asset_id",
                    ""
                ),

                "source_text": {
                    "heading": source_text.get(
                        "heading",
                        ""
                    ),
                    "body_text": source_text.get(
                        "body_text",
                        ""
                    ),
                    "prominent_quote": source_text.get(
                        "prominent_quote",
                        ""
                    ),
                    "other_story_text": source_text.get(
                        "other_story_text",
                        []
                    ),
                },

                "semantic_summary": {
                    "core_idea": semantic.get(
                        "core_idea",
                        ""
                    ),
                    "concepts": semantic.get(
                        "concepts",
                        []
                    ),
                    "emotional_tone": semantic.get(
                        "emotional_tone",
                        []
                    ),
                },

                "named_entities": analysis.get(
                    "named_entities",
                    []
                ),

                "visual": {
                    "description": visual.get(
                        "description",
                        ""
                    ),
                    "important_actions": visual.get(
                        "important_actions",
                        []
                    ),
                },

                "production": {
                    "story_art_description":
                        production.get(
                            "story_art_description",
                            ""
                        ),
                    "suggested_motion":
                        production.get(
                            "suggested_motion",
                            []
                        ),
                },

                "uncertainties": analysis.get(
                    "uncertainties",
                    []
                ),
            }
        )

    return bundle


# ============================================================
# PROMPT
# ============================================================

def build_revision_prompt(
    story_plan: dict,
    review: dict,
    source_bundle: list[dict],
    correction: str = "",
) -> str:

    correction_section = ""

    if correction:
        correction_section = f"""
A previous revision attempt failed validation:

{correction}

Correct that issue in this new response.
""".strip()

    return f"""
Revise the story plan using the independent review.

TARGET

- Duration: {TARGET_MIN_SECONDS}-{TARGET_MAX_SECONDS} seconds
- Narration: {TARGET_MIN_WORDS}-{TARGET_MAX_WORDS} words
- Mature, calm, confident, reflective delivery
- Strong spoken storytelling
- Source-grounded only

CRITICAL SOURCE-SAFETY RULE

Do not blindly follow a reviewer suggestion if it would require an
unsupported fact.

For example, if the source only says "Manson" and the source analysis
explicitly says the exact identity is uncertain, do NOT expand the name
using outside knowledge. Resolve the clarity problem in another way,
such as removing the unnecessary reference or restructuring the line.

The revised plan should fix all IDs listed under
must_fix_before_next_step unless there is a genuine source-safe reason
to mark an item NOT_APPLICABLE.

{correction_section}

============================================================
ORIGINAL STORY PLAN
============================================================

{json.dumps(
    story_plan,
    indent=2,
    ensure_ascii=False,
)}

============================================================
REVIEW
============================================================

{json.dumps(
    review,
    indent=2,
    ensure_ascii=False,
)}

============================================================
SOURCE GROUND TRUTH
============================================================

{json.dumps(
    source_bundle,
    indent=2,
    ensure_ascii=False,
)}

Return only the complete revised story plan using the requested schema.
""".strip()


# ============================================================
# VALIDATION
# ============================================================

def validate_revised_plan(
    revised_plan: dict,
    review: dict,
    valid_asset_ids: set[str],
) -> tuple[bool, str]:

    narration = revised_plan.get(
        "narration_script",
        ""
    ).strip()

    if not narration:
        return (
            False,
            "narration_script is empty.",
        )

    word_count = count_words(
        narration
    )

    if not (
        TARGET_MIN_WORDS
        <= word_count
        <= TARGET_MAX_WORDS
    ):
        return (
            False,
            f"Narration has {word_count} words; "
            f"target is {TARGET_MIN_WORDS}-"
            f"{TARGET_MAX_WORDS}.",
        )

    scenes = revised_plan.get(
        "scenes",
        []
    )

    if not scenes:
        return (
            False,
            "No scenes were returned.",
        )

    expected_sequences = list(
        range(
            1,
            len(scenes) + 1,
        )
    )

    actual_sequences = [
        int(
            scene.get(
                "sequence",
                -1
            )
        )
        for scene
        in scenes
    ]

    if actual_sequences != expected_sequences:
        return (
            False,
            "Scene sequence numbers must start at 1 "
            "and increase continuously.",
        )

    scene_narration = " ".join(
        scene.get(
            "narration",
            ""
        ).strip()
        for scene
        in scenes
    )

    if normalize_text(
        scene_narration
    ) != normalize_text(
        narration
    ):
        return (
            False,
            "Scene narration does not reconstruct "
            "the complete narration_script.",
        )

    total_duration = 0.0

    for scene in scenes:

        duration = float(
            scene.get(
                "estimated_duration_seconds",
                0
            )
        )

        if duration <= 0:
            return (
                False,
                f"Scene {scene.get('sequence')} "
                f"has non-positive duration.",
            )

        total_duration += duration

        asset_ids = scene.get(
            "source_asset_ids",
            []
        )

        if not asset_ids:
            return (
                False,
                f"Scene {scene.get('sequence')} "
                f"has no source_asset_ids.",
            )

        unknown_ids = [
            asset_id
            for asset_id
            in asset_ids
            if asset_id
            not in valid_asset_ids
        ]

        if unknown_ids:
            return (
                False,
                f"Scene {scene.get('sequence')} "
                f"references unknown asset IDs: "
                f"{unknown_ids}",
            )

    if not (
        TARGET_MIN_SECONDS
        <= total_duration
        <= TARGET_MAX_SECONDS
    ):
        return (
            False,
            f"Scene durations total "
            f"{total_duration:.1f}s; target is "
            f"{TARGET_MIN_SECONDS}-"
            f"{TARGET_MAX_SECONDS}s.",
        )

    pace_wpm = int(
        revised_plan.get(
            "voice_direction",
            {}
        ).get(
            "pace_wpm",
            0
        )
    )

    if pace_wpm <= 0:
        return (
            False,
            "voice_direction.pace_wpm must be positive.",
        )

    # Compare planned WPM with actual words / planned duration.
    effective_wpm = (
        word_count
        / total_duration
        * 60.0
    )

    # Give some room for intentional pauses, but catch obvious
    # contradictions like 150 WPM vs ~128 effective WPM.
    if abs(
        pace_wpm - effective_wpm
    ) > 12:
        return (
            False,
            f"Pacing is inconsistent: pace_wpm={pace_wpm}, "
            f"but {word_count} words over {total_duration:.1f}s "
            f"is about {effective_wpm:.1f} WPM.",
        )

    required_issue_ids = set(
        review.get(
            "must_fix_before_next_step",
            []
        )
    )

    resolutions = revised_plan.get(
        "review_resolution",
        []
    )

    resolution_map = {
        item.get(
            "issue_id"
        ): item
        for item
        in resolutions
    }

    missing_resolutions = [
        issue_id
        for issue_id
        in required_issue_ids
        if issue_id
        not in resolution_map
    ]

    if missing_resolutions:
        return (
            False,
            f"Missing review_resolution entries for "
            f"{missing_resolutions}.",
        )

    unresolved = [
        issue_id
        for issue_id
        in required_issue_ids
        if resolution_map[
            issue_id
        ].get(
            "status"
        )
        not in {
            "RESOLVED",
            "NOT_APPLICABLE",
        }
    ]

    if unresolved:
        return (
            False,
            f"Required issues are not fully resolved: "
            f"{unresolved}.",
        )

    return (
        True,
        "",
    )


# ============================================================
# GEMINI REVISION
# ============================================================

def call_revision_agent(
    client: genai.Client,
    story_plan: dict,
    review: dict,
    source_bundle: list[dict],
) -> dict:

    valid_asset_ids = {
        asset[
            "asset_id"
        ]
        for asset
        in source_bundle
    }

    correction = ""
    last_error = ""

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        try:
            log(
                f"Revision attempt {attempt}."
            )

            response = client.models.generate_content(
                model=MODEL,

                contents=types.Part.from_text(
                    text=build_revision_prompt(
                        story_plan=story_plan,
                        review=review,
                        source_bundle=source_bundle,
                        correction=correction,
                    )
                ),

                config=types.GenerateContentConfig(
                    system_instruction=
                        SYSTEM_INSTRUCTION,

                    temperature=0.35,

                    response_mime_type=
                        "application/json",

                    response_schema=
                        RESPONSE_SCHEMA,

                    max_output_tokens=8192,
                ),
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini returned an empty revision."
                )

            RAW_RESPONSE_FILE.write_text(
                response.text,
                encoding="utf-8",
            )

            revised_plan = json.loads(
                response.text
            )

            valid, validation_error = (
                validate_revised_plan(
                    revised_plan=revised_plan,
                    review=review,
                    valid_asset_ids=
                        valid_asset_ids,
                )
            )

            if valid:
                return revised_plan

            correction = validation_error
            last_error = validation_error

            log(
                f"Validation failed: "
                f"{validation_error}"
            )

        except Exception as exc:
            last_error = str(exc)

            correction = (
                "The previous revision attempt "
                f"failed with: {exc}"
            )

            log(
                f"Attempt {attempt} failed: {exc}"
            )

        if attempt < MAX_ATTEMPTS:
            time.sleep(
                RETRY_BASE_SECONDS
                * (2 ** (attempt - 1))
            )

    raise RuntimeError(
        "Revision Agent failed after "
        f"{MAX_ATTEMPTS} attempts. "
        f"Last issue: {last_error}"
    )


# ============================================================
# FINALIZE
# ============================================================

def finalize_revised_plan(
    revised_plan: dict,
) -> dict:

    narration = revised_plan[
        "narration_script"
    ]

    calculated_word_count = count_words(
        narration
    )

    calculated_duration = round(
        sum(
            float(
                scene[
                    "estimated_duration_seconds"
                ]
            )
            for scene
            in revised_plan[
                "scenes"
            ]
        ),
        2,
    )

    effective_wpm = round(
        calculated_word_count
        / calculated_duration
        * 60.0,
        1,
    )

    revised_plan[
        "schema_version"
    ] = REVISION_SCHEMA_VERSION

    revised_plan[
        "generated_at_utc"
    ] = now_utc()

    revised_plan[
        "model"
    ] = MODEL

    revised_plan[
        "calculated_word_count"
    ] = calculated_word_count

    revised_plan[
        "calculated_scene_duration_seconds"
    ] = calculated_duration

    revised_plan[
        "calculated_effective_wpm"
    ] = effective_wpm

    revised_plan[
        "source_story_plan"
    ] = str(
        STORY_PLAN_FILE
    )

    revised_plan[
        "source_review"
    ] = str(
        STORY_REVIEW_FILE
    )

    revised_plan[
        "source_analyzed_assets"
    ] = str(
        ANALYZED_ASSETS_FILE
    )

    return revised_plan


# ============================================================
# HUMAN-READABLE OUTPUT
# ============================================================

def write_revised_text(
    revised_plan: dict,
) -> None:

    lines = []

    lines.append(
        f"STORY: "
        f"{revised_plan['story_title']}"
    )

    lines.append(
        f"CORE THESIS: "
        f"{revised_plan['core_thesis']}"
    )

    lines.append(
        f"WORDS: "
        f"{revised_plan['calculated_word_count']}"
    )

    lines.append(
        f"ESTIMATED DURATION: "
        f"{revised_plan['calculated_scene_duration_seconds']:.1f}s"
    )

    lines.append(
        f"EFFECTIVE WPM: "
        f"{revised_plan['calculated_effective_wpm']:.1f}"
    )

    lines.append(
        f"PLANNED VOICE WPM: "
        f"{revised_plan['voice_direction']['pace_wpm']}"
    )

    lines.append("")
    lines.append(
        "NARRATIVE STRATEGY"
    )

    lines.append(
        revised_plan[
            "narrative_strategy"
        ]
    )

    lines.append("")
    lines.append(
        "=" * 72
    )
    lines.append("")
    lines.append(
        "FINAL REVISED NARRATION"
    )
    lines.append("")

    lines.append(
        revised_plan[
            "narration_script"
        ]
    )

    lines.append("")
    lines.append(
        "=" * 72
    )
    lines.append("")
    lines.append(
        "SCENE PLAN"
    )
    lines.append("")

    for scene in revised_plan[
        "scenes"
    ]:

        lines.append(
            f"{scene['sequence']:02d}. "
            f"{scene['role']} "
            f"({scene['estimated_duration_seconds']:.1f}s)"
        )

        lines.append(
            f"    Narration: "
            f"{scene['narration']}"
        )

        lines.append(
            f"    Assets: "
            f"{', '.join(scene['source_asset_ids'])}"
        )

        lines.append(
            f"    Source support: "
            f"{scene['source_support']}"
        )

        lines.append(
            f"    Visual intent: "
            f"{scene['visual_intent']}"
        )

        lines.append(
            f"    Visual treatment: "
            f"{scene['suggested_visual_treatment']}"
        )

        if scene[
            "impact_text"
        ].strip():

            lines.append(
                f"    Impact text: "
                f"{scene['impact_text']}"
            )

        lines.append(
            f"    Emotional goal: "
            f"{scene['emotional_goal']}"
        )

        lines.append("")

    lines.append(
        "REVIEW RESOLUTION"
    )

    for item in revised_plan[
        "review_resolution"
    ]:

        lines.append(
            f"- {item['issue_id']} "
            f"[{item['status']}]: "
            f"{item['resolution']}"
        )

        if item[
            "source_safety_note"
        ].strip():

            lines.append(
                f"  Source safety: "
                f"{item['source_safety_note']}"
            )

    lines.append("")
    lines.append(
        "CHANGE SUMMARY"
    )

    for item in revised_plan[
        "change_summary"
    ]:

        lines.append(
            f"- {item}"
        )

    REVISED_TEXT_FILE.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    ensure_output_dir()

    required_files = [
        STORY_PLAN_FILE,
        STORY_REVIEW_FILE,
        ANALYZED_ASSETS_FILE,
    ]

    missing = [
        str(path)
        for path
        in required_files
        if not path.exists()
    ]

    if missing:
        raise FileNotFoundError(
            "Missing required files:\n"
            + "\n".join(
                missing
            )
        )

    write_status(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n"
    )

    story_plan = load_json(
        STORY_PLAN_FILE
    )

    review = load_json(
        STORY_REVIEW_FILE
    )

    analyzed_assets = load_json(
        ANALYZED_ASSETS_FILE
    )

    if review.get(
        "verdict"
    ) == "APPROVE":

        print()
        print(
            "Review verdict is APPROVE."
        )
        print(
            "No revision is required."
        )
        print()

        write_status(
            "NO_REVISION_REQUIRED\n"
            f"Finished: {now_utc()}\n"
            f"Review verdict: APPROVE\n"
        )

        return

    source_bundle = prepare_source_bundle(
        analyzed_assets
    )

    if not source_bundle:
        raise RuntimeError(
            "No analyzed source assets were found."
        )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(
            api_version="v1"
        ),
    )

    revised_plan = call_revision_agent(
        client=client,
        story_plan=story_plan,
        review=review,
        source_bundle=source_bundle,
    )

    revised_plan = finalize_revised_plan(
        revised_plan
    )

    save_json(
        REVISED_PLAN_FILE,
        revised_plan,
    )

    write_revised_text(
        revised_plan
    )

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Words: "
        f"{revised_plan['calculated_word_count']}\n"
        f"Duration: "
        f"{revised_plan['calculated_scene_duration_seconds']:.1f}s\n"
        f"Effective WPM: "
        f"{revised_plan['calculated_effective_wpm']:.1f}\n"
        f"Output: {REVISED_PLAN_FILE}\n"
        f"Readable output: {REVISED_TEXT_FILE}\n"
    )

    print()
    print(
        "Story revision completed."
    )

    print(
        f"Saved: "
        f"{REVISED_PLAN_FILE}"
    )

    print(
        f"Readable: "
        f"{REVISED_TEXT_FILE}"
    )

    print()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception:

        ensure_output_dir()

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
            "Story revision failed."
        )

        print(
            f"See: {STATUS_FILE}"
        )

        print()

        sys.exit(1)
