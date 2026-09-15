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
STORY_TEXT_FILE = Path("metadata/story_plan.txt")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")

OUTPUT_DIR = Path("metadata")

REVIEW_FILE = OUTPUT_DIR / "story_review.json"
REVIEW_TEXT_FILE = OUTPUT_DIR / "story_review.txt"

# This is a COPY of story_plan.json with an agent_review field added.
# The original story_plan.json is never modified.
REVIEWED_PLAN_FILE = OUTPUT_DIR / "story_plan_reviewed.json"

RAW_RESPONSE_FILE = OUTPUT_DIR / "story_review_raw_response.txt"
STATUS_FILE = OUTPUT_DIR / "story_review_status.txt"
LOG_FILE = OUTPUT_DIR / "story_review.log"

MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2

REVIEW_SCHEMA_VERSION = "1.0"


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are an independent editorial QA reviewer for an automated
short-form wisdom video pipeline.

Another agent has already created a story plan.

Your job is to REVIEW that plan.

You are NOT allowed to rewrite the full story plan in this stage.

You must identify whether the story is ready for the next production
step or whether revisions are needed.

You will receive:

1. story_plan.json
2. story_plan.txt
3. compact factual analyses of the original source images
4. deterministic validation checks produced by code

REVIEW PRINCIPLES

1. SOURCE FIDELITY
   Every factual claim, quotation, person, event, and concept should
   be supported by the supplied source analyses.
   Do not use outside knowledge to "fix" the story.

2. STORY QUALITY
   Judge the narration as spoken short-form storytelling, not as an
   essay or summary.

3. HOOK QUALITY
   The first one or two spoken lines should create immediate curiosity,
   contradiction, emotional tension, or a strong question.

4. SPOKEN NATURALNESS
   The narration should sound natural when spoken aloud.
   Flag stiff, compressed, awkward, overly academic, or summary-like
   phrasing.

5. NARRATIVE ARC
   Look for a clear progression such as:
   hook -> concrete story/tension -> reversal/reveal -> explanation
   -> viewer reflection.

6. CLARITY
   Flag late-introduced names, concepts, or references that may confuse
   a viewer in a 40-50 second Reel.

7. PACING
   Check whether the amount of narration and scene timing reasonably
   fit the intended duration and mature delivery style.

8. VOICE ALIGNMENT
   The requested voice is mature, calm, confident, reflective, and
   slightly intense. It should not sound like clickbait or generic
   motivational content.

9. SCENE GRANULARITY
   A scene should represent a meaningful beat. Flag scenes that contain
   too many distinct narrative ideas or too many visual changes.

10. VISUAL SUPPORT
    Every scene should have source visuals that can plausibly support
    what is being narrated.

11. REDUNDANCY
    Flag repeated ideas unless repetition is intentionally used for
    emphasis.

12. PRESERVE WHAT WORKS
    Do not recommend changing strong material merely to be different.

13. DO NOT WRITE A REVISED NARRATION.
    This stage only produces actionable review suggestions.
    Another agent will apply the suggestions later.

14. BE DECISIVE
    Use APPROVE only when there are no major changes required before
    voice generation.
    Use REVISE when at least one important editorial or source-integrity
    issue should be fixed first.

Return only JSON matching the requested schema.
""".strip()


# ============================================================
# RESPONSE SCHEMA
# ============================================================

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {

        "verdict": {
            "type": "string",
            "enum": [
                "APPROVE",
                "REVISE",
            ],
        },

        "confidence": {
            "type": "number",
            "description": "Reviewer confidence from 0.0 to 1.0.",
        },

        "review_summary": {
            "type": "string",
        },

        "strengths": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },

        "scores": {
            "type": "object",
            "properties": {
                "source_fidelity": {
                    "type": "integer",
                },
                "hook_strength": {
                    "type": "integer",
                },
                "spoken_naturalness": {
                    "type": "integer",
                },
                "narrative_coherence": {
                    "type": "integer",
                },
                "voice_alignment": {
                    "type": "integer",
                },
                "pacing": {
                    "type": "integer",
                },
                "scene_structure": {
                    "type": "integer",
                },
                "visual_support": {
                    "type": "integer",
                },
            },
            "required": [
                "source_fidelity",
                "hook_strength",
                "spoken_naturalness",
                "narrative_coherence",
                "voice_alignment",
                "pacing",
                "scene_structure",
                "visual_support",
            ],
        },

        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {

                    "issue_id": {
                        "type": "string",
                        "description": "Short stable ID such as I01.",
                    },

                    "severity": {
                        "type": "string",
                        "enum": [
                            "BLOCKER",
                            "MAJOR",
                            "MINOR",
                        ],
                    },

                    "category": {
                        "type": "string",
                        "enum": [
                            "SOURCE_FIDELITY",
                            "HOOK",
                            "SPOKEN_NATURALNESS",
                            "NARRATIVE_ARC",
                            "CLARITY",
                            "PACING",
                            "VOICE",
                            "SCENE_STRUCTURE",
                            "VISUAL_SUPPORT",
                            "REDUNDANCY",
                            "JSON_TEXT_CONSISTENCY",
                            "OTHER",
                        ],
                    },

                    "target": {
                        "type": "string",
                        "description": (
                            "Where the issue occurs, for example "
                            "narration_script, hook, scene_2, "
                            "voice_direction, or story_plan.txt."
                        ),
                    },

                    "description": {
                        "type": "string",
                    },

                    "evidence": {
                        "type": "string",
                        "description": (
                            "Brief evidence from the supplied material. "
                            "Do not reproduce long passages."
                        ),
                    },

                    "source_asset_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },

                    "recommendation": {
                        "type": "string",
                        "description": (
                            "Specific editorial action to take later. "
                            "Do not write the full replacement story."
                        ),
                    },
                },

                "required": [
                    "issue_id",
                    "severity",
                    "category",
                    "target",
                    "description",
                    "evidence",
                    "source_asset_ids",
                    "recommendation",
                ],
            },
        },

        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {

                    "suggestion_id": {
                        "type": "string",
                        "description": "Short stable ID such as S01.",
                    },

                    "priority": {
                        "type": "integer",
                        "description": (
                            "1 is highest priority. Use consecutive "
                            "priorities when possible."
                        ),
                    },

                    "target": {
                        "type": "string",
                    },

                    "action": {
                        "type": "string",
                        "description": (
                            "A concrete instruction that a later revision "
                            "agent can apply."
                        ),
                    },

                    "reason": {
                        "type": "string",
                    },

                    "preserve": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                        "description": (
                            "Strong elements that should survive the edit."
                        ),
                    },

                    "related_issue_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },
                },

                "required": [
                    "suggestion_id",
                    "priority",
                    "target",
                    "action",
                    "reason",
                    "preserve",
                    "related_issue_ids",
                ],
            },
        },

        "must_fix_before_next_step": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },

        "optional_improvements": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },

        "source_integrity_assessment": {
            "type": "object",
            "properties": {

                "status": {
                    "type": "string",
                    "enum": [
                        "PASS",
                        "WARN",
                        "FAIL",
                    ],
                },

                "notes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },

            "required": [
                "status",
                "notes",
            ],
        },

        "json_text_consistency_assessment": {
            "type": "object",
            "properties": {

                "status": {
                    "type": "string",
                    "enum": [
                        "PASS",
                        "WARN",
                        "FAIL",
                    ],
                },

                "notes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },

            "required": [
                "status",
                "notes",
            ],
        },
    },

    "required": [
        "verdict",
        "confidence",
        "review_summary",
        "strengths",
        "scores",
        "issues",
        "suggestions",
        "must_fix_before_next_step",
        "optional_improvements",
        "source_integrity_assessment",
        "json_text_consistency_assessment",
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
# DETERMINISTIC CHECKS
# ============================================================

def run_deterministic_checks(
    story_plan: dict,
    story_text: str,
    analyzed_assets: dict,
) -> dict:

    checks = []

    narration = story_plan.get(
        "narration_script",
        "",
    )

    actual_word_count = count_words(
        narration
    )

    stored_word_count = story_plan.get(
        "calculated_word_count"
    )

    checks.append(
        {
            "check": "word_count",
            "status": (
                "PASS"
                if stored_word_count == actual_word_count
                else "WARN"
            ),
            "details": (
                f"Actual={actual_word_count}; "
                f"stored={stored_word_count}"
            ),
        }
    )

    scene_duration_total = round(
        sum(
            float(
                scene.get(
                    "estimated_duration_seconds",
                    0,
                )
            )
            for scene
            in story_plan.get(
                "scenes",
                [],
            )
        ),
        2,
    )

    stored_duration = story_plan.get(
        "calculated_scene_duration_seconds"
    )

    checks.append(
        {
            "check": "scene_duration_total",
            "status": (
                "PASS"
                if stored_duration == scene_duration_total
                else "WARN"
            ),
            "details": (
                f"Actual={scene_duration_total}; "
                f"stored={stored_duration}"
            ),
        }
    )

    scene_narration = " ".join(
        scene.get(
            "narration",
            ""
        ).strip()
        for scene
        in story_plan.get(
            "scenes",
            []
        )
    )

    narration_matches_scenes = (
        normalize_text(
            scene_narration
        )
        == normalize_text(
            narration
        )
    )

    checks.append(
        {
            "check": "scene_narration_matches_full_script",
            "status": (
                "PASS"
                if narration_matches_scenes
                else "WARN"
            ),
            "details": (
                "Scene narration reconstructs full narration."
                if narration_matches_scenes
                else
                "Scene narration does not exactly reconstruct "
                "the full narration."
            ),
        }
    )

    narration_in_text_file = (
        normalize_text(
            narration
        )
        in normalize_text(
            story_text
        )
    )

    checks.append(
        {
            "check": "json_narration_present_in_story_plan_txt",
            "status": (
                "PASS"
                if narration_in_text_file
                else "WARN"
            ),
            "details": (
                "The JSON narration appears in story_plan.txt."
                if narration_in_text_file
                else
                "The JSON narration could not be found exactly "
                "inside story_plan.txt."
            ),
        }
    )

    valid_asset_ids = {
        asset.get(
            "asset_id",
            ""
        )
        for asset
        in analyzed_assets.get(
            "assets",
            []
        )
    }

    unknown_asset_ids = []

    for scene in story_plan.get(
        "scenes",
        []
    ):
        for asset_id in scene.get(
            "source_asset_ids",
            []
        ):
            if asset_id not in valid_asset_ids:
                unknown_asset_ids.append(
                    asset_id
                )

    checks.append(
        {
            "check": "scene_source_asset_ids",
            "status": (
                "PASS"
                if not unknown_asset_ids
                else "FAIL"
            ),
            "details": (
                "All scene source asset IDs exist."
                if not unknown_asset_ids
                else
                f"Unknown IDs: "
                f"{sorted(set(unknown_asset_ids))}"
            ),
        }
    )

    return {
        "actual_word_count": actual_word_count,
        "scene_duration_total_seconds": scene_duration_total,
        "checks": checks,
    }


# ============================================================
# COMPACT SOURCE BUNDLE
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

def build_review_prompt(
    story_plan: dict,
    story_text: str,
    source_bundle: list[dict],
    deterministic_checks: dict,
) -> str:

    return f"""
Review the following generated short-form story plan.

Do NOT rewrite the full story.
Do NOT generate a replacement narration.

Instead:

- decide APPROVE or REVISE
- identify strengths
- identify concrete issues
- produce actionable suggestions for a later revision agent
- verify source fidelity
- verify the JSON and human-readable text are consistent
- use only the supplied source bundle as factual ground truth

The next pipeline stage will read your structured suggestions
and decide how to revise the plan.

============================================================
STORY PLAN JSON
============================================================

{json.dumps(
    story_plan,
    indent=2,
    ensure_ascii=False,
)}

============================================================
HUMAN-READABLE STORY PLAN
============================================================

{story_text}

============================================================
DETERMINISTIC CODE CHECKS
============================================================

{json.dumps(
    deterministic_checks,
    indent=2,
    ensure_ascii=False,
)}

============================================================
SOURCE ASSET GROUND TRUTH
============================================================

{json.dumps(
    source_bundle,
    indent=2,
    ensure_ascii=False,
)}

Return only the requested structured JSON.
""".strip()


# ============================================================
# VALIDATE REVIEW
# ============================================================

def validate_review(
    review: dict,
) -> tuple[bool, str]:

    verdict = review.get(
        "verdict"
    )

    issues = review.get(
        "issues",
        []
    )

    suggestions = review.get(
        "suggestions",
        []
    )

    if verdict == "APPROVE":

        major_or_blocker = [
            issue
            for issue
            in issues
            if issue.get(
                "severity"
            )
            in {
                "BLOCKER",
                "MAJOR",
            }
        ]

        if major_or_blocker:
            return (
                False,
                "Review says APPROVE but also contains "
                "BLOCKER or MAJOR issues."
            )

    if verdict == "REVISE" and not suggestions:
        return (
            False,
            "Review says REVISE but returned no suggestions."
        )

    priorities = [
        suggestion.get(
            "priority"
        )
        for suggestion
        in suggestions
    ]

    if any(
        not isinstance(
            priority,
            int
        )
        or priority < 1
        for priority
        in priorities
    ):
        return (
            False,
            "Every suggestion must have a positive integer priority."
        )

    return (
        True,
        ""
    )


# ============================================================
# GEMINI REVIEW
# ============================================================

def call_reviewer(
    client: genai.Client,
    story_plan: dict,
    story_text: str,
    source_bundle: list[dict],
    deterministic_checks: dict,
) -> dict:

    last_error = ""

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        try:
            log(
                f"Reviewer attempt {attempt}."
            )

            response = client.models.generate_content(
                model=MODEL,

                contents=types.Part.from_text(
                    text=build_review_prompt(
                        story_plan=story_plan,
                        story_text=story_text,
                        source_bundle=source_bundle,
                        deterministic_checks=
                            deterministic_checks,
                    )
                ),

                config=types.GenerateContentConfig(
                    system_instruction=
                        SYSTEM_INSTRUCTION,

                    temperature=0.2,

                    response_mime_type=
                        "application/json",

                    response_schema=
                        RESPONSE_SCHEMA,

                    max_output_tokens=8192,
                ),
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini returned an empty review."
                )

            RAW_RESPONSE_FILE.write_text(
                response.text,
                encoding="utf-8",
            )

            review = json.loads(
                response.text
            )

            valid, validation_error = (
                validate_review(
                    review
                )
            )

            if not valid:
                raise RuntimeError(
                    validation_error
                )

            return review

        except Exception as exc:
            last_error = str(exc)

            log(
                f"Attempt {attempt} failed: {exc}"
            )

            if attempt < MAX_ATTEMPTS:
                time.sleep(
                    RETRY_BASE_SECONDS
                    * (2 ** (attempt - 1))
                )

    raise RuntimeError(
        "Story review failed after "
        f"{MAX_ATTEMPTS} attempts. "
        f"Last issue: {last_error}"
    )


# ============================================================
# HUMAN-READABLE REVIEW
# ============================================================

def write_review_text(
    review: dict,
    deterministic_checks: dict,
) -> None:

    lines = []

    lines.append(
        f"VERDICT: {review['verdict']}"
    )

    lines.append(
        f"CONFIDENCE: "
        f"{review['confidence']:.2f}"
    )

    lines.append("")

    lines.append(
        "SUMMARY"
    )

    lines.append(
        review[
            "review_summary"
        ]
    )

    lines.append("")

    lines.append(
        "SCORES (1-10)"
    )

    for key, value in review[
        "scores"
    ].items():

        lines.append(
            f"- {key}: {value}"
        )

    lines.append("")

    lines.append(
        "STRENGTHS"
    )

    for strength in review[
        "strengths"
    ]:

        lines.append(
            f"- {strength}"
        )

    lines.append("")

    lines.append(
        "ISSUES"
    )

    if not review["issues"]:
        lines.append(
            "- None"
        )

    for issue in review[
        "issues"
    ]:

        lines.append(
            f"- [{issue['severity']}] "
            f"{issue['issue_id']} / "
            f"{issue['category']} / "
            f"{issue['target']}"
        )

        lines.append(
            f"  Problem: "
            f"{issue['description']}"
        )

        lines.append(
            f"  Evidence: "
            f"{issue['evidence']}"
        )

        lines.append(
            f"  Recommendation: "
            f"{issue['recommendation']}"
        )

        if issue[
            "source_asset_ids"
        ]:

            lines.append(
                f"  Sources: "
                f"{', '.join(issue['source_asset_ids'])}"
            )

    lines.append("")

    lines.append(
        "SUGGESTIONS FOR REVISION AGENT"
    )

    if not review[
        "suggestions"
    ]:
        lines.append(
            "- None"
        )

    for suggestion in sorted(
        review[
            "suggestions"
        ],
        key=lambda item: item[
            "priority"
        ],
    ):

        lines.append(
            f"- P{suggestion['priority']} "
            f"{suggestion['suggestion_id']} "
            f"({suggestion['target']}): "
            f"{suggestion['action']}"
        )

        lines.append(
            f"  Why: "
            f"{suggestion['reason']}"
        )

        if suggestion[
            "preserve"
        ]:

            lines.append(
                f"  Preserve: "
                f"{'; '.join(suggestion['preserve'])}"
            )

    lines.append("")

    lines.append(
        "MUST FIX BEFORE NEXT STEP"
    )

    if not review[
        "must_fix_before_next_step"
    ]:
        lines.append(
            "- None"
        )

    for item in review[
        "must_fix_before_next_step"
    ]:

        lines.append(
            f"- {item}"
        )

    lines.append("")

    lines.append(
        "OPTIONAL IMPROVEMENTS"
    )

    if not review[
        "optional_improvements"
    ]:
        lines.append(
            "- None"
        )

    for item in review[
        "optional_improvements"
    ]:

        lines.append(
            f"- {item}"
        )

    lines.append("")

    lines.append(
        "DETERMINISTIC CHECKS"
    )

    for item in deterministic_checks[
        "checks"
    ]:

        lines.append(
            f"- [{item['status']}] "
            f"{item['check']}: "
            f"{item['details']}"
        )

    REVIEW_TEXT_FILE.write_text(
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
        STORY_TEXT_FILE,
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

    story_text = (
        STORY_TEXT_FILE
        .read_text(
            encoding="utf-8"
        )
    )

    analyzed_assets = load_json(
        ANALYZED_ASSETS_FILE
    )

    deterministic_checks = (
        run_deterministic_checks(
            story_plan=story_plan,
            story_text=story_text,
            analyzed_assets=
                analyzed_assets,
        )
    )

    source_bundle = prepare_source_bundle(
        analyzed_assets
    )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(
            api_version="v1"
        ),
    )

    review = call_reviewer(
        client=client,
        story_plan=story_plan,
        story_text=story_text,
        source_bundle=source_bundle,
        deterministic_checks=
            deterministic_checks,
    )

    review[
        "schema_version"
    ] = REVIEW_SCHEMA_VERSION

    review[
        "reviewed_at_utc"
    ] = now_utc()

    review[
        "model"
    ] = MODEL

    review[
        "input_story_plan"
    ] = str(
        STORY_PLAN_FILE
    )

    review[
        "input_story_text"
    ] = str(
        STORY_TEXT_FILE
    )

    review[
        "input_analyzed_assets"
    ] = str(
        ANALYZED_ASSETS_FILE
    )

    review[
        "deterministic_checks"
    ] = deterministic_checks

    save_json(
        REVIEW_FILE,
        review,
    )

    # Preserve the original story plan and attach the review to a COPY.
    reviewed_plan = dict(
        story_plan
    )

    reviewed_plan[
        "agent_review"
    ] = review

    save_json(
        REVIEWED_PLAN_FILE,
        reviewed_plan,
    )

    write_review_text(
        review=review,
        deterministic_checks=
            deterministic_checks,
    )

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Verdict: {review['verdict']}\n"
        f"Issues: {len(review['issues'])}\n"
        f"Suggestions: "
        f"{len(review['suggestions'])}\n"
        f"Review: {REVIEW_FILE}\n"
        f"Reviewed plan: {REVIEWED_PLAN_FILE}\n"
    )

    print()
    print(
        "Story review completed."
    )

    print(
        f"Verdict: "
        f"{review['verdict']}"
    )

    print(
        f"Review: "
        f"{REVIEW_FILE}"
    )

    print(
        f"Reviewed plan: "
        f"{REVIEWED_PLAN_FILE}"
    )

    print(
        f"Readable review: "
        f"{REVIEW_TEXT_FILE}"
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
            "Story review failed."
        )

        print(
            f"See: {STATUS_FILE}"
        )

        print()

        sys.exit(1)
