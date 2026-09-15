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

REVISED_PLAN_FILE = Path("metadata/revised_story_plan.json")
REVISED_TEXT_FILE = Path("metadata/revised_story_plan.txt")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
PRIOR_REVIEW_FILE = Path("metadata/story_review.json")

OUTPUT_DIR = Path("metadata")

OUTPUT_REVIEW_FILE = OUTPUT_DIR / "revised_story_review.json"
OUTPUT_REVIEW_TEXT_FILE = OUTPUT_DIR / "revised_story_review.txt"
RAW_RESPONSE_FILE = OUTPUT_DIR / "revised_story_review_raw_response.txt"
STATUS_FILE = OUTPUT_DIR / "revised_story_review_status.txt"
LOG_FILE = OUTPUT_DIR / "revised_story_review.log"

MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2

REVIEW_SCHEMA_VERSION = "2.0"


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are the final editorial QA reviewer for an automated
short-form wisdom video pipeline.

A Story Director produced an initial plan.
A Review Agent identified issues.
A Revision Agent produced a revised plan.

Your task is to independently verify the REVISED plan.

You must decide whether it is genuinely ready for voice generation.

IMPORTANT RULES

1. REVIEW THE REVISED RESULT, NOT THE INTENT.
   Do not approve merely because the revision agent says an issue
   was resolved.

2. SOURCE FIDELITY.
   Every factual claim, quotation, named person, event, and concept
   must be supported by the supplied source analyses.

3. NO OUTSIDE KNOWLEDGE.
   Do not silently fill gaps from general knowledge.

4. INTERNAL CONSISTENCY.
   Check narration, scene narration, target word count, calculated
   word count, duration, WPM, change summaries, and review-resolution
   claims for contradictions.

5. SPOKEN NATURALNESS.
   Judge whether the narration sounds natural when spoken aloud.

6. CLARITY.
   Flag vague references such as "the author", "he", "they", or
   unexplained names if the intended referent may be unclear in a
   fast short-form video.

7. PACING.
   The story should feel deliberate rather than rushed.
   Timing fields are planning estimates, but they should not make
   contradictory claims.

8. SCENE STRUCTURE.
   Distinct narrative beats should have enough room to land.
   Flag a scene that still carries too many separate conceptual or
   visual shifts.

9. PRESERVE WHAT WORKS.
   Do not recommend changes solely for novelty.

10. FINAL VERDICT.
    APPROVE only when there are no meaningful issues that should be
    fixed before voice generation.
    Otherwise return REVISE.

11. DO NOT REWRITE THE FULL STORY.
    Produce structured issues and revision instructions only.

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
        },

        "review_summary": {
            "type": "string",
        },

        "scores": {
            "type": "object",
            "properties": {
                "source_fidelity": {"type": "integer"},
                "hook_strength": {"type": "integer"},
                "spoken_naturalness": {"type": "integer"},
                "narrative_coherence": {"type": "integer"},
                "voice_alignment": {"type": "integer"},
                "pacing": {"type": "integer"},
                "scene_structure": {"type": "integer"},
                "visual_support": {"type": "integer"},
                "internal_consistency": {"type": "integer"},
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
                "internal_consistency",
            ],
        },

        "strengths": {
            "type": "array",
            "items": {"type": "string"},
        },

        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {

                    "issue_id": {
                        "type": "string",
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
                            "CLARITY",
                            "SPOKEN_NATURALNESS",
                            "NARRATIVE_ARC",
                            "PACING",
                            "SCENE_STRUCTURE",
                            "VISUAL_SUPPORT",
                            "INTERNAL_CONSISTENCY",
                            "REVIEW_RESOLUTION",
                            "OTHER",
                        ],
                    },

                    "target": {
                        "type": "string",
                    },

                    "description": {
                        "type": "string",
                    },

                    "evidence": {
                        "type": "string",
                    },

                    "source_asset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },

                    "recommendation": {
                        "type": "string",
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
                    },

                    "priority": {
                        "type": "integer",
                    },

                    "target": {
                        "type": "string",
                    },

                    "action": {
                        "type": "string",
                    },

                    "reason": {
                        "type": "string",
                    },

                    "preserve": {
                        "type": "array",
                        "items": {"type": "string"},
                    },

                    "related_issue_ids": {
                        "type": "array",
                        "items": {"type": "string"},
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

        "must_fix_before_voice": {
            "type": "array",
            "items": {"type": "string"},
        },

        "prior_review_resolution_assessment": {
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
                            "UNRESOLVED",
                            "NOT_APPLICABLE",
                        ],
                    },

                    "assessment": {
                        "type": "string",
                    },
                },

                "required": [
                    "issue_id",
                    "status",
                    "assessment",
                ],
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
                    "items": {"type": "string"},
                },
            },
            "required": [
                "status",
                "notes",
            ],
        },

        "ready_for_voice_generation": {
            "type": "boolean",
        },
    },

    "required": [
        "verdict",
        "confidence",
        "review_summary",
        "scores",
        "strengths",
        "issues",
        "suggestions",
        "must_fix_before_voice",
        "prior_review_resolution_assessment",
        "source_integrity_assessment",
        "ready_for_voice_generation",
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
    revised_plan: dict,
    revised_text: str,
    analyzed_assets: dict,
    prior_review: dict,
) -> dict:

    checks = []

    narration = revised_plan.get(
        "narration_script",
        ""
    )

    actual_word_count = count_words(
        narration
    )

    declared_calculated_word_count = revised_plan.get(
        "calculated_word_count"
    )

    target_word_count = revised_plan.get(
        "target_word_count"
    )

    checks.append(
        {
            "check": "calculated_word_count_matches_script",
            "status": (
                "PASS"
                if declared_calculated_word_count == actual_word_count
                else "FAIL"
            ),
            "details": (
                f"Actual script words={actual_word_count}; "
                f"calculated_word_count="
                f"{declared_calculated_word_count}"
            ),
        }
    )

    checks.append(
        {
            "check": "target_word_count_vs_actual",
            "status": (
                "PASS"
                if isinstance(target_word_count, int)
                and abs(target_word_count - actual_word_count) <= 5
                else "WARN"
            ),
            "details": (
                f"Target={target_word_count}; "
                f"actual={actual_word_count}"
            ),
        }
    )

    scene_narration = " ".join(
        scene.get(
            "narration",
            ""
        ).strip()
        for scene
        in revised_plan.get(
            "scenes",
            []
        )
    )

    checks.append(
        {
            "check": "scene_narration_matches_script",
            "status": (
                "PASS"
                if normalize_text(scene_narration)
                == normalize_text(narration)
                else "FAIL"
            ),
            "details": (
                "Scene narration reconstructs the full script."
                if normalize_text(scene_narration)
                == normalize_text(narration)
                else
                "Scene narration differs from the full script."
            ),
        }
    )

    total_duration = round(
        sum(
            float(
                scene.get(
                    "estimated_duration_seconds",
                    0
                )
            )
            for scene
            in revised_plan.get(
                "scenes",
                []
            )
        ),
        2,
    )

    declared_duration = revised_plan.get(
        "calculated_scene_duration_seconds"
    )

    checks.append(
        {
            "check": "scene_duration_total",
            "status": (
                "PASS"
                if declared_duration == total_duration
                else "FAIL"
            ),
            "details": (
                f"Actual={total_duration}; "
                f"declared={declared_duration}"
            ),
        }
    )

    effective_wpm = round(
        actual_word_count
        / total_duration
        * 60.0,
        1,
    ) if total_duration > 0 else 0.0

    declared_effective_wpm = revised_plan.get(
        "calculated_effective_wpm"
    )

    checks.append(
        {
            "check": "effective_wpm_math",
            "status": (
                "PASS"
                if declared_effective_wpm == effective_wpm
                else "FAIL"
            ),
            "details": (
                f"Actual effective WPM={effective_wpm}; "
                f"declared={declared_effective_wpm}"
            ),
        }
    )

    planned_wpm = revised_plan.get(
        "voice_direction",
        {}
    ).get(
        "pace_wpm"
    )

    pace_delta = (
        abs(
            float(planned_wpm)
            - effective_wpm
        )
        if planned_wpm is not None
        else None
    )

    checks.append(
        {
            "check": "planned_wpm_vs_effective_wpm",
            "status": (
                "PASS"
                if pace_delta is not None
                and pace_delta <= 7
                else "WARN"
            ),
            "details": (
                f"Planned WPM={planned_wpm}; "
                f"effective WPM={effective_wpm}; "
                f"delta={pace_delta}"
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

    for scene in revised_plan.get(
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

    narration_in_text = (
        normalize_text(narration)
        in normalize_text(revised_text)
    )

    checks.append(
        {
            "check": "json_narration_present_in_txt",
            "status": (
                "PASS"
                if narration_in_text
                else "FAIL"
            ),
            "details": (
                "JSON narration appears in revised_story_plan.txt."
                if narration_in_text
                else
                "JSON narration was not found in the text file."
            ),
        }
    )

    prior_required_issue_ids = set(
        prior_review.get(
            "must_fix_before_next_step",
            []
        )
    )

    resolution_map = {
        item.get(
            "issue_id"
        ): item
        for item
        in revised_plan.get(
            "review_resolution",
            []
        )
    }

    missing_resolutions = sorted(
        issue_id
        for issue_id
        in prior_required_issue_ids
        if issue_id not in resolution_map
    )

    checks.append(
        {
            "check": "required_review_resolutions_present",
            "status": (
                "PASS"
                if not missing_resolutions
                else "FAIL"
            ),
            "details": (
                "All required prior-review issues have resolution entries."
                if not missing_resolutions
                else
                f"Missing: {missing_resolutions}"
            ),
        }
    )

    # Catch numerical claims in change_summary/review_resolution that
    # contradict the actual script word count.
    metadata_text = json.dumps(
        {
            "review_resolution": revised_plan.get(
                "review_resolution",
                []
            ),
            "change_summary": revised_plan.get(
                "change_summary",
                []
            ),
        },
        ensure_ascii=False,
    )

    claimed_to_counts = [
        int(match)
        for match
        in re.findall(
            r"\bto\s+(\d{2,3})\s+words?\b",
            metadata_text,
            flags=re.IGNORECASE,
        )
    ]

    contradictory_counts = [
        count
        for count
        in claimed_to_counts
        if count != actual_word_count
    ]

    checks.append(
        {
            "check": "word_count_claims_in_revision_metadata",
            "status": (
                "PASS"
                if not contradictory_counts
                else "WARN"
            ),
            "details": (
                "No contradictory 'to N words' claims found."
                if not contradictory_counts
                else
                f"Metadata claims word counts "
                f"{contradictory_counts}, but actual is "
                f"{actual_word_count}."
            ),
        }
    )

    return {
        "actual_word_count": actual_word_count,
        "scene_duration_total_seconds": total_duration,
        "effective_wpm": effective_wpm,
        "checks": checks,
    }


# ============================================================
# SOURCE BUNDLE
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

def build_prompt(
    revised_plan: dict,
    revised_text: str,
    prior_review: dict,
    source_bundle: list[dict],
    deterministic_checks: dict,
) -> str:

    return f"""
Perform FINAL QA on the revised story plan before voice generation.

Do NOT rewrite the complete narration.

Independently decide whether the revision is actually ready.

Pay special attention to:

- whether prior required issues were truly fixed
- whether new awkwardness was introduced by the revision
- whether vague references are still confusing
- whether scene 3 remains too dense
- whether pacing claims are internally consistent
- whether review_resolution and change_summary contain factual
  numerical contradictions
- whether source fidelity remains intact

============================================================
REVISED STORY PLAN JSON
============================================================

{json.dumps(
    revised_plan,
    indent=2,
    ensure_ascii=False,
)}

============================================================
REVISED STORY PLAN TEXT
============================================================

{revised_text}

============================================================
PRIOR REVIEW
============================================================

{json.dumps(
    prior_review,
    indent=2,
    ensure_ascii=False,
)}

============================================================
DETERMINISTIC CHECKS
============================================================

{json.dumps(
    deterministic_checks,
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

Return only the requested structured JSON.
""".strip()


# ============================================================
# VALIDATE MODEL REVIEW
# ============================================================

def validate_model_review(
    review: dict,
) -> tuple[bool, str]:

    verdict = review.get(
        "verdict"
    )

    ready = review.get(
        "ready_for_voice_generation"
    )

    if verdict == "APPROVE" and ready is not True:
        return (
            False,
            "APPROVE requires ready_for_voice_generation=true.",
        )

    if verdict == "REVISE" and ready is not False:
        return (
            False,
            "REVISE requires ready_for_voice_generation=false.",
        )

    if verdict == "REVISE" and not review.get(
        "suggestions"
    ):
        return (
            False,
            "REVISE requires at least one suggestion.",
        )

    scores = review.get(
        "scores",
        {}
    )

    for key, value in scores.items():
        if not isinstance(value, int):
            return (
                False,
                f"Score {key} must be an integer.",
            )

        if value < 1 or value > 10:
            return (
                False,
                f"Score {key} must be between 1 and 10.",
            )

    return (
        True,
        ""
    )


# ============================================================
# GEMINI CALL
# ============================================================

def call_reviewer(
    client: genai.Client,
    revised_plan: dict,
    revised_text: str,
    prior_review: dict,
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
                f"Final QA attempt {attempt}."
            )

            response = client.models.generate_content(
                model=MODEL,

                contents=types.Part.from_text(
                    text=build_prompt(
                        revised_plan=revised_plan,
                        revised_text=revised_text,
                        prior_review=prior_review,
                        source_bundle=source_bundle,
                        deterministic_checks=
                            deterministic_checks,
                    )
                ),

                config=types.GenerateContentConfig(
                    system_instruction=
                        SYSTEM_INSTRUCTION,

                    temperature=0.15,

                    response_mime_type=
                        "application/json",

                    response_schema=
                        RESPONSE_SCHEMA,

                    max_output_tokens=8192,
                ),
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini returned an empty final review."
                )

            RAW_RESPONSE_FILE.write_text(
                response.text,
                encoding="utf-8",
            )

            review = json.loads(
                response.text
            )

            valid, validation_error = (
                validate_model_review(
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
        "Final QA failed after "
        f"{MAX_ATTEMPTS} attempts. "
        f"Last issue: {last_error}"
    )


# ============================================================
# HUMAN-READABLE OUTPUT
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
        f"READY FOR VOICE: "
        f"{review['ready_for_voice_generation']}"
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

    for item in review[
        "strengths"
    ]:
        lines.append(
            f"- {item}"
        )

    lines.append("")
    lines.append(
        "ISSUES"
    )

    if not review[
        "issues"
    ]:
        lines.append(
            "- None"
        )

    for issue in review[
        "issues"
    ]:

        lines.append(
            f"- [{issue['severity']}] "
            f"{issue['issue_id']} "
            f"{issue['category']} "
            f"({issue['target']})"
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

    lines.append("")
    lines.append(
        "SUGGESTIONS"
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

    lines.append("")
    lines.append(
        "PRIOR REVIEW RESOLUTION ASSESSMENT"
    )

    for item in review[
        "prior_review_resolution_assessment"
    ]:

        lines.append(
            f"- {item['issue_id']} "
            f"[{item['status']}]: "
            f"{item['assessment']}"
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

    OUTPUT_REVIEW_TEXT_FILE.write_text(
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

    required = [
        REVISED_PLAN_FILE,
        REVISED_TEXT_FILE,
        ANALYZED_ASSETS_FILE,
        PRIOR_REVIEW_FILE,
    ]

    missing = [
        str(path)
        for path
        in required
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

    revised_plan = load_json(
        REVISED_PLAN_FILE
    )

    revised_text = (
        REVISED_TEXT_FILE
        .read_text(
            encoding="utf-8"
        )
    )

    analyzed_assets = load_json(
        ANALYZED_ASSETS_FILE
    )

    prior_review = load_json(
        PRIOR_REVIEW_FILE
    )

    deterministic_checks = run_deterministic_checks(
        revised_plan=revised_plan,
        revised_text=revised_text,
        analyzed_assets=analyzed_assets,
        prior_review=prior_review,
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
        revised_plan=revised_plan,
        revised_text=revised_text,
        prior_review=prior_review,
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
        "input_revised_plan"
    ] = str(
        REVISED_PLAN_FILE
    )

    review[
        "input_prior_review"
    ] = str(
        PRIOR_REVIEW_FILE
    )

    review[
        "deterministic_checks"
    ] = deterministic_checks

    save_json(
        OUTPUT_REVIEW_FILE,
        review,
    )

    write_review_text(
        review=review,
        deterministic_checks=
            deterministic_checks,
    )

    write_status(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Verdict: {review['verdict']}\n"
        f"Ready for voice: "
        f"{review['ready_for_voice_generation']}\n"
        f"Issues: {len(review['issues'])}\n"
        f"Output: {OUTPUT_REVIEW_FILE}\n"
    )

    print()
    print(
        "Final revised-story QA completed."
    )

    print(
        f"Verdict: "
        f"{review['verdict']}"
    )

    print(
        f"Ready for voice: "
        f"{review['ready_for_voice_generation']}"
    )

    print(
        f"Review: "
        f"{OUTPUT_REVIEW_FILE}"
    )

    print(
        f"Readable: "
        f"{OUTPUT_REVIEW_TEXT_FILE}"
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
            "Final revised-story QA failed."
        )

        print(
            f"See: {STATUS_FILE}"
        )

        print()

        sys.exit(1)
