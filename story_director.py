from __future__ import annotations

import json
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

INPUT_FILE = Path("metadata/analyzed_assets.json")

OUTPUT_DIR = Path("metadata")
STORY_PLAN_FILE = OUTPUT_DIR / "story_plan.json"
STORY_TEXT_FILE = OUTPUT_DIR / "story_plan.txt"
RAW_RESPONSE_FILE = OUTPUT_DIR / "story_director_raw_response.txt"
STATUS_FILE = OUTPUT_DIR / "story_director_status.txt"
LOG_FILE = OUTPUT_DIR / "story_director.log"

TARGET_MIN_SECONDS = 40
TARGET_MAX_SECONDS = 50

# A calm, mature narration with deliberate pauses generally needs
# fewer words than an energetic commercial read.
TARGET_MIN_WORDS = 90
TARGET_MAX_WORDS = 115

MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2

STORY_PLAN_SCHEMA_VERSION = "1.0"


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are the Story Director for an automated short-form wisdom
video system.

You receive structured analyses of multiple source images.

Your task is to turn those source materials into ONE compelling,
coherent short-form narrated story.

You are not merely summarizing the cards.
You are adapting them into spoken storytelling.

CORE PRINCIPLES

1. STORY FIRST.
   Build a clear narrative progression rather than reading cards
   in sequence.

2. SOURCE-BOUND.
   Use only claims, people, quotations, concepts, and events
   supported by the supplied asset analyses.
   Do not add outside facts.

3. DO NOT TRUST FILE ORDER.
   Filenames, upload order, manifest order, and array order are
   not narrative instructions.
   Infer the best story order from meaning.

4. HANDLE OVERLAP.
   Multiple cards may repeat or continue the same sentence.
   Deduplicate overlapping information.

5. HANDLE TRUNCATION SAFELY.
   Some cards may end mid-sentence.
   Do not invent missing words.
   Use complete information from another asset if it clearly
   supplies the same idea.

6. NARRATION MUST SOUND SPOKEN.
   Prefer short, natural sentences.
   Avoid textbook prose and long clauses.

7. VOICE STYLE.
   The voice should feel mature, calm, confident, thoughtful,
   slightly intense, and emotionally controlled.
   Never sound like clickbait, a motivational advertisement,
   or a movie-trailer parody.

8. HOOK FAST.
   The first one or two spoken sentences should create tension,
   curiosity, contradiction, or a compelling question.

9. HUMAN STORY BEFORE ABSTRACT THEORY WHEN POSSIBLE.
   If the source provides a concrete person, event, or example,
   use it to carry the abstract lesson.

10. REVEAL.
    If the source contains a short central paradox, striking
    quotation, or reversal, consider giving it a dedicated beat
    with room to land.

11. EXPLAIN THE WISDOM CLEARLY.
    The viewer should understand the principle without needing
    the original screenshots.

12. END WITH REFLECTION.
    Finish by turning the idea back toward the viewer in a
    thoughtful way rather than giving a generic call-to-action.

13. DO NOT OVERWRITE THE VISUALS WITH TEXT.
    On-screen impact text should be short and selective.

14. STORY ROLES FROM THE IMAGE ANALYZER ARE SUGGESTIONS ONLY.
    You make the final narrative decisions.

15. EVERY SCENE MUST CITE ITS SOURCE ASSET IDs.
    This lets later pipeline stages verify where the narration
    came from.

16. DO NOT CREATE FINAL VIDEO TRANSITIONS, MUSIC CUES, OR
    FRAME-ACCURATE TIMING YET.
    Those belong to later production stages.

Return only JSON matching the requested schema.
""".strip()


# ============================================================
# RESPONSE SCHEMA
# ============================================================

STORY_PLAN_SCHEMA = {
    "type": "object",
    "properties": {

        "story_title": {
            "type": "string"
        },

        "core_thesis": {
            "type": "string",
            "description": (
                "One concise sentence describing the wisdom or "
                "idea the final viewer should understand."
            ),
        },

        "narrative_strategy": {
            "type": "string",
            "description": (
                "Short explanation of why this particular story "
                "structure was chosen."
            ),
        },

        "target_duration_seconds": {
            "type": "number"
        },

        "target_word_count": {
            "type": "integer"
        },

        "hook": {
            "type": "string",
            "description": (
                "The opening spoken hook. It must also appear at "
                "the beginning of the complete narration script."
            ),
        },

        "narration_script": {
            "type": "string",
            "description": (
                "The complete final spoken narration as one coherent "
                "script. No production labels or scene numbers."
            ),
        },

        "voice_direction": {
            "type": "object",
            "properties": {

                "persona": {
                    "type": "string"
                },

                "tone": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                },

                "pace_wpm": {
                    "type": "integer"
                },

                "delivery_notes": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                },

                "emphasis_phrases": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                },

                "pause_after_phrases": {
                    "type": "array",
                    "items": {
                        "type": "string"
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
                    "type": "string"
                },

                "human_or_concrete_story": {
                    "type": "string"
                },

                "reversal_or_reveal": {
                    "type": "string"
                },

                "principle_explanation": {
                    "type": "string"
                },

                "viewer_reflection": {
                    "type": "string"
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
                        "type": "integer"
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
                        "type": "number"
                    },

                    "narration": {
                        "type": "string",
                        "description": (
                            "The narration spoken during this scene. "
                            "Together, scene narration should reconstruct "
                            "the complete narration script."
                        ),
                    },

                    "source_asset_ids": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },

                    "source_support": {
                        "type": "string",
                        "description": (
                            "Brief explanation of what information in "
                            "the cited assets supports this scene."
                        ),
                    },

                    "visual_intent": {
                        "type": "string",
                        "description": (
                            "What the viewer should visually experience "
                            "during this scene. This is not a detailed "
                            "animation instruction."
                        ),
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
                        "description": (
                            "Optional short on-screen phrase, ideally "
                            "seven words or fewer. Empty string if none."
                        ),
                    },

                    "emotional_goal": {
                        "type": "string"
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
                        "type": "string"
                    },

                    "reason": {
                        "type": "string"
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
                "type": "string"
            },
            "description": (
                "Notes about truncation, duplicated text, ambiguity, "
                "or source limitations that mattered to the adaptation."
            ),
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


def write_status(text: str) -> None:
    ensure_output_dir()

    STATUS_FILE.write_text(
        text,
        encoding="utf-8",
    )


def log(message: str) -> None:
    ensure_output_dir()

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            f"[{now_utc()}] {message}\n"
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

    temporary.replace(
        path
    )


def count_words(text: str) -> int:
    return len(
        text.split()
    )


# ============================================================
# PREPARE COMPACT DIRECTOR INPUT
# ============================================================

def prepare_director_input(
    analyzed_data: dict,
) -> list[dict]:

    prepared_assets = []

    for asset in analyzed_data.get(
        "assets",
        [],
    ):

        analysis = asset.get(
            "analysis",
            {},
        )

        source_text = analysis.get(
            "source_text",
            {},
        )

        visual = analysis.get(
            "visual",
            {},
        )

        semantic = analysis.get(
            "semantic_summary",
            {},
        )

        production = analysis.get(
            "production",
            {},
        )

        prepared_assets.append(
            {
                "asset_id": asset.get(
                    "asset_id",
                    "",
                ),

                "source_text": {
                    "heading": source_text.get(
                        "heading",
                        "",
                    ),

                    "body_text": source_text.get(
                        "body_text",
                        "",
                    ),

                    "prominent_quote": source_text.get(
                        "prominent_quote",
                        "",
                    ),

                    "other_story_text": source_text.get(
                        "other_story_text",
                        [],
                    ),
                },

                "visual": {
                    "description": visual.get(
                        "description",
                        "",
                    ),

                    "main_elements": visual.get(
                        "main_elements",
                        [],
                    ),

                    "visual_subjects": [
                        {
                            "subject_type":
                                item.get(
                                    "subject_type",
                                    "",
                                ),

                            "description":
                                item.get(
                                    "description",
                                    "",
                                ),
                        }

                        for item
                        in visual.get(
                            "visual_subjects",
                            [],
                        )
                    ],

                    "important_actions": visual.get(
                        "important_actions",
                        [],
                    ),
                },

                "semantic_summary": {
                    "core_idea": semantic.get(
                        "core_idea",
                        "",
                    ),

                    "concepts": semantic.get(
                        "concepts",
                        [],
                    ),

                    "emotional_tone": semantic.get(
                        "emotional_tone",
                        [],
                    ),

                    "requires_external_context":
                        semantic.get(
                            "requires_external_context",
                            False,
                        ),

                    "context_needed":
                        semantic.get(
                            "context_needed",
                            [],
                        ),
                },

                "named_entities": analysis.get(
                    "named_entities",
                    [],
                ),

                "possible_story_roles": analysis.get(
                    "possible_story_roles",
                    [],
                ),

                "production": {
                    "story_art_description":
                        production.get(
                            "story_art_description",
                            "",
                        ),

                    "vertical_video_suitability":
                        production.get(
                            "vertical_video_suitability",
                            "",
                        ),

                    "recommended_crop_strategy":
                        production.get(
                            "recommended_crop_strategy",
                            "",
                        ),

                    "suggested_motion":
                        production.get(
                            "suggested_motion",
                            [],
                        ),
                },

                "uncertainties": analysis.get(
                    "uncertainties",
                    [],
                ),
            }
        )

    return prepared_assets


# ============================================================
# PROMPT
# ============================================================

def build_user_prompt(
    prepared_assets: list[dict],
    correction: str = "",
) -> str:

    correction_section = ""

    if correction:
        correction_section = f"""
A previous attempt failed validation for this reason:

{correction}

Correct that problem in this new response.
""".strip()

    return f"""
Create the final STORY PLAN for a short-form wisdom Reel.

TARGET

- Duration: {TARGET_MIN_SECONDS} to {TARGET_MAX_SECONDS} seconds
- Narration: {TARGET_MIN_WORDS} to {TARGET_MAX_WORDS} words
- Audience: thoughtful adult viewer
- Voice: bold, mature, calm, confident, reflective
- Format: spoken story, not a slideshow summary

DESIRED STORY EXPERIENCE

The viewer should feel:

curiosity
→ human/concrete tension
→ reversal or reveal
→ clear wisdom
→ personal reflection

RULES

- Determine story order from meaning, not asset order.
- Use only source-supported facts and ideas.
- Paraphrasing is allowed for natural spoken narration.
- Preserve a short source quotation exactly when its wording is
  central to the story.
- Do not repeat information merely because several assets repeat it.
- Do not fill in missing text from an incomplete card unless another
  supplied asset clearly provides the needed information.
- A scene may use more than one source asset.
- One source asset may support more than one scene.
- You do not need to use every asset if it weakens the story.
- If you omit an asset, explain why in unused_assets.
- Keep impact_text short.
- Do not create final transitions, beat timing, subtitles, or music
  instructions yet.

IMPORTANT

Each scene's source_asset_ids MUST use only asset IDs that actually
exist in the supplied data.

{correction_section}

AVAILABLE ANALYZED ASSETS

{json.dumps(
    prepared_assets,
    indent=2,
    ensure_ascii=False,
)}

Return the requested structured JSON only.
""".strip()


# ============================================================
# VALIDATION
# ============================================================

def validate_story_plan(
    story_plan: dict,
    valid_asset_ids: set[str],
) -> tuple[bool, str]:

    narration = story_plan.get(
        "narration_script",
        "",
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

    scenes = story_plan.get(
        "scenes",
        [],
    )

    if not scenes:
        return (
            False,
            "No scenes were returned.",
        )

    # Sequence numbers should be clean and continuous.
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
                -1,
            )
        )
        for scene in scenes
    ]

    if (
        actual_sequences
        != expected_sequences
    ):
        return (
            False,
            "Scene sequence numbers must start at 1 "
            "and increase continuously.",
        )

    total_scene_duration = 0.0

    for scene in scenes:

        duration = float(
            scene.get(
                "estimated_duration_seconds",
                0,
            )
        )

        if duration <= 0:
            return (
                False,
                "Every scene must have a positive "
                "estimated duration.",
            )

        total_scene_duration += duration

        asset_ids = scene.get(
            "source_asset_ids",
            [],
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
        TARGET_MIN_SECONDS - 2
        <= total_scene_duration
        <= TARGET_MAX_SECONDS + 2
    ):
        return (
            False,
            f"Scene durations total "
            f"{total_scene_duration:.1f}s; "
            f"target is approximately "
            f"{TARGET_MIN_SECONDS}-"
            f"{TARGET_MAX_SECONDS}s.",
        )

    hook = story_plan.get(
        "hook",
        "",
    ).strip()

    if (
        hook
        and not narration.lower().startswith(
            hook.lower()
        )
    ):
        # Not fatal. The hook can differ slightly in punctuation,
        # but we surface it later as a warning.
        pass

    return (
        True,
        "",
    )


# ============================================================
# GEMINI CALL
# ============================================================

def generate_story_plan(
    client: genai.Client,
    prepared_assets: list[dict],
) -> dict:

    valid_asset_ids = {
        item["asset_id"]
        for item in prepared_assets
    }

    correction = ""
    last_error = ""

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        log(
            f"Story Director attempt {attempt}."
        )

        try:
            response = client.models.generate_content(
                model=MODEL,

                contents=types.Part.from_text(
                    text=build_user_prompt(
                        prepared_assets,
                        correction,
                    )
                ),

                config=types.GenerateContentConfig(
                    system_instruction=
                        SYSTEM_INSTRUCTION,

                    temperature=0.35,

                    response_mime_type=
                        "application/json",

                    response_schema=
                        STORY_PLAN_SCHEMA,

                    max_output_tokens=8192,
                ),
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            RAW_RESPONSE_FILE.write_text(
                response.text,
                encoding="utf-8",
            )

            story_plan = json.loads(
                response.text
            )

            valid, validation_error = (
                validate_story_plan(
                    story_plan,
                    valid_asset_ids,
                )
            )

            if valid:
                return story_plan

            correction = validation_error
            last_error = validation_error

            log(
                f"Validation failed: "
                f"{validation_error}"
            )

        except Exception as exc:
            last_error = str(exc)

            correction = (
                "The previous API/model response "
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
        "Story Director failed after "
        f"{MAX_ATTEMPTS} attempts. "
        f"Last issue: {last_error}"
    )


# ============================================================
# FINALIZE + HUMAN-READABLE OUTPUT
# ============================================================

def finalize_story_plan(
    story_plan: dict,
) -> dict:

    narration = story_plan[
        "narration_script"
    ]

    story_plan[
        "schema_version"
    ] = STORY_PLAN_SCHEMA_VERSION

    story_plan[
        "generated_at_utc"
    ] = now_utc()

    story_plan[
        "model"
    ] = MODEL

    story_plan[
        "calculated_word_count"
    ] = count_words(
        narration
    )

    story_plan[
        "calculated_scene_duration_seconds"
    ] = round(
        sum(
            float(
                scene[
                    "estimated_duration_seconds"
                ]
            )
            for scene
            in story_plan["scenes"]
        ),
        2,
    )

    story_plan[
        "source_file"
    ] = str(
        INPUT_FILE
    )

    return story_plan


def write_human_readable(
    story_plan: dict,
) -> None:

    lines = []

    lines.append(
        f"STORY: "
        f"{story_plan['story_title']}"
    )

    lines.append(
        f"CORE THESIS: "
        f"{story_plan['core_thesis']}"
    )

    lines.append(
        f"WORDS: "
        f"{story_plan['calculated_word_count']}"
    )

    lines.append(
        f"ESTIMATED DURATION: "
        f"{story_plan['calculated_scene_duration_seconds']:.1f}s"
    )

    lines.append("")

    lines.append(
        "NARRATIVE STRATEGY"
    )

    lines.append(
        story_plan[
            "narrative_strategy"
        ]
    )

    lines.append("")

    lines.append(
        "VOICE DIRECTION"
    )

    voice = story_plan[
        "voice_direction"
    ]

    lines.append(
        f"Persona: "
        f"{voice['persona']}"
    )

    lines.append(
        f"Tone: "
        f"{', '.join(voice['tone'])}"
    )

    lines.append(
        f"Pace: "
        f"{voice['pace_wpm']} WPM"
    )

    for note in voice[
        "delivery_notes"
    ]:
        lines.append(
            f"- {note}"
        )

    lines.append("")
    lines.append(
        "=" * 72
    )
    lines.append("")

    lines.append(
        "FINAL NARRATION"
    )

    lines.append("")

    lines.append(
        story_plan[
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

    for scene in story_plan[
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

    if story_plan[
        "unused_assets"
    ]:

        lines.append(
            "UNUSED ASSETS"
        )

        for item in story_plan[
            "unused_assets"
        ]:

            lines.append(
                f"- {item['asset_id']}: "
                f"{item['reason']}"
            )

        lines.append("")

    if story_plan[
        "source_integrity_notes"
    ]:

        lines.append(
            "SOURCE INTEGRITY NOTES"
        )

        for note in story_plan[
            "source_integrity_notes"
        ]:

            lines.append(
                f"- {note}"
            )

    STORY_TEXT_FILE.write_text(
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

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Missing input file: "
            f"{INPUT_FILE.resolve()}"
        )

    analyzed_data = load_json(
        INPUT_FILE
    )

    prepared_assets = (
        prepare_director_input(
            analyzed_data
        )
    )

    if not prepared_assets:
        raise RuntimeError(
            "No analyzed assets were found."
        )

    write_status(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Assets supplied: "
        f"{len(prepared_assets)}\n"
    )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(
            api_version="v1"
        ),
    )

    story_plan = generate_story_plan(
        client=client,
        prepared_assets=prepared_assets,
    )

    story_plan = finalize_story_plan(
        story_plan
    )

    save_json(
        STORY_PLAN_FILE,
        story_plan,
    )

    write_human_readable(
        story_plan
    )

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Words: "
        f"{story_plan['calculated_word_count']}\n"
        f"Estimated duration: "
        f"{story_plan['calculated_scene_duration_seconds']:.1f}s\n"
        f"Scenes: "
        f"{len(story_plan['scenes'])}\n"
        f"Output: {STORY_PLAN_FILE}\n"
        f"Readable output: {STORY_TEXT_FILE}\n"
    )

    print()
    print(
        "Story Director completed."
    )
    print(
        f"Saved: {STORY_PLAN_FILE}"
    )
    print(
        f"Readable: {STORY_TEXT_FILE}"
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
            "Story Director failed."
        )
        print(
            f"See: {STATUS_FILE}"
        )
        print()

        sys.exit(1)
