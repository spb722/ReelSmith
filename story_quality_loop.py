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

INITIAL_STORY_PLAN_FILE = Path("metadata/story_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")

OUTPUT_DIR = Path("metadata/story_quality_loop")
FINAL_PLAN_FILE = Path("metadata/final_story_plan.json")
FINAL_TEXT_FILE = Path("metadata/final_story_plan.txt")
STATUS_FILE = Path("metadata/story_quality_loop_status.txt")

MAX_ITERATIONS = 4
MAX_API_RETRIES = 3
MAX_REVIEW_VALIDATION_RETRIES = 3
MAX_REVISION_VALIDATION_RETRIES = 3
RETRY_BASE_SECONDS = 2

TARGET_MIN_SECONDS = 40
TARGET_MAX_SECONDS = 50
TARGET_MIN_WORDS = 90
TARGET_MAX_WORDS = 115

# Approval gates. These are enforced in Python, not just in the prompt.
MIN_APPROVAL_SCORE = 8
MIN_SOURCE_FIDELITY_SCORE = 9
MIN_INTERNAL_CONSISTENCY_SCORE = 9

PIPELINE_SCHEMA_VERSION = "2.0"


# ============================================================
# REVIEWER SYSTEM INSTRUCTION
# ============================================================

REVIEWER_SYSTEM_INSTRUCTION = """
You are an independent final editorial QA reviewer for an
automated short-form wisdom video pipeline.

You receive:
- the current story plan
- structured analyses of the original source images
- deterministic validation checks produced by Python
- prior review/revision history when available

Your job is to decide whether the CURRENT plan is genuinely ready
for voice generation.

SCORING SCALE

All scores MUST use a 1-10 scale:
1 = very poor
5 = mediocre / needs noticeable work
8 = strong and production-ready
9 = excellent
10 = exceptional

APPROVAL STANDARD

You may return APPROVE only when:
- ready_for_voice_generation = true
- issues is empty
- must_fix_before_voice is empty
- every score is at least 8
- source_fidelity is at least 9
- internal_consistency is at least 9
- confidence is between 0.0 and 1.0

If any of those conditions are not true, return REVISE.

IMPORTANT ISSUE-ID CONTRACT

- Every item in issues must have a short issue_id such as I01, I02, I03.
- must_fix_before_voice MUST contain only those issue_id strings exactly.
- Never place prose, recommendations, or descriptions inside
  must_fix_before_voice.
- suggestion.related_issue_ids must also contain only issue_id values
  that exist in issues.

REVIEW PRINCIPLES

1. SOURCE FIDELITY
   Every factual claim, quotation, person, event, and concept must
   be supported by the supplied source analyses.

2. NO OUTSIDE KNOWLEDGE
   Do not fill source gaps from general knowledge.

3. SOURCE UNCERTAINTY MATTERS
   Read the source "uncertainties" fields carefully.
   If the narration uses a person/entity whose exact identity is
   explicitly uncertain in the source analysis, check whether that
   reference is clear and source-safe. Flag ambiguous bare references.

4. REVIEW THE CURRENT RESULT
   Do not approve because a previous revision claims an issue was fixed.

5. STORY QUALITY
   Judge this as spoken short-form storytelling, not as an essay.

6. HOOK
   The opening should create curiosity or tension quickly.

7. SPOKEN NATURALNESS
   Flag stiff, summary-like, over-compressed, or awkward phrasing.

8. NARRATIVE ARC
   Prefer:
   hook -> concrete story/tension -> reversal/reveal ->
   explanation -> reflection.

9. CLARITY
   Flag unexplained names, vague references, or sudden perspective
   shifts that may confuse a viewer.

10. PACING
    The narration, scene durations, and voice pace should make sense
    together. Python arithmetic is authoritative for numeric checks.

11. SCENE STRUCTURE
    Flag scenes carrying too many conceptual or visual beats.

12. VISUAL SUPPORT
    Every scene should be supportable by cited source assets.

13. PRESERVE WHAT WORKS
    Do not recommend changes merely for novelty.

14. If REVISE, return at least one actionable suggestion.

15. Do not rewrite the full story yourself.

Return only JSON matching the requested schema.
""".strip()


# ============================================================
# REVISION SYSTEM INSTRUCTION
# ============================================================

REVISION_SYSTEM_INSTRUCTION = """
You are the Revision Agent in an automated short-form wisdom
video pipeline.

You receive:
- the current story plan
- the latest independent QA review
- structured source analyses

Your job is to make the smallest useful changes needed to resolve
the review while preserving the strongest material.

RULES

1. SOURCE FIDELITY IS NON-NEGOTIABLE.
   Do not add facts unsupported by supplied source analyses.

2. NO OUTSIDE KNOWLEDGE.

3. FIX THE UNDERLYING ISSUE.
   Do not blindly copy reviewer wording.

4. PRESERVE STRONG MATERIAL.
   Keep strong hooks, supported quotations, clear story beats, and
   reflective endings unless changing them is necessary.

5. SPOKEN STORYTELLING.
   Narration must sound natural aloud.

6. DO NOT PAD JUST TO HIT A NUMBER.
   If narration is already in range, prefer structural or metadata
   fixes over filler.

7. SCENE GRANULARITY.
   You may split an overloaded scene into smaller beats.

8. NARRATION AND SCENES MUST MATCH.
   Concatenating scene narration in order must reconstruct the
   narration_script.

9. TARGET:
   40-50 seconds and 90-115 spoken words.

10. VOICE:
    mature, calm, confident, reflective, slightly intense.

11. EVERY SCENE MUST CITE VALID SOURCE ASSET IDS.

12. Resolve every issue ID in must_fix_before_voice.
    must_fix_before_voice contains issue IDs such as I01, never prose.
    For every such ID, return a review_resolution entry with the exact
    same issue_id.

13. Do not claim exact calculated word counts or WPM in prose.
    Python computes those after your response.

14. Return a COMPLETE revised story plan.

Return only JSON matching the requested schema.
""".strip()


# ============================================================
# SCHEMAS
# ============================================================

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["APPROVE", "REVISE"],
        },
        "confidence": {"type": "number"},
        "review_summary": {"type": "string"},
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
                    "issue_id": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["BLOCKER", "MAJOR", "MINOR"],
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
                            "INTERNAL_CONSISTENCY",
                            "OTHER",
                        ],
                    },
                    "target": {"type": "string"},
                    "description": {"type": "string"},
                    "evidence": {"type": "string"},
                    "source_asset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "recommendation": {"type": "string"},
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
                    "suggestion_id": {"type": "string"},
                    "priority": {"type": "integer"},
                    "target": {"type": "string"},
                    "action": {"type": "string"},
                    "reason": {"type": "string"},
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
        "source_integrity_assessment": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["PASS", "WARN", "FAIL"],
                },
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["status", "notes"],
        },
        "ready_for_voice_generation": {"type": "boolean"},
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
        "source_integrity_assessment",
        "ready_for_voice_generation",
    ],
}


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "story_title": {"type": "string"},
        "core_thesis": {"type": "string"},
        "narrative_strategy": {"type": "string"},
        "target_duration_seconds": {"type": "number"},
        "hook": {"type": "string"},
        "narration_script": {"type": "string"},
        "voice_direction": {
            "type": "object",
            "properties": {
                "persona": {"type": "string"},
                "tone": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "delivery_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "emphasis_phrases": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "pause_after_phrases": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "persona",
                "tone",
                "delivery_notes",
                "emphasis_phrases",
                "pause_after_phrases",
            ],
        },
        "story_arc": {
            "type": "object",
            "properties": {
                "opening_tension": {"type": "string"},
                "human_or_concrete_story": {"type": "string"},
                "reversal_or_reveal": {"type": "string"},
                "principle_explanation": {"type": "string"},
                "viewer_reflection": {"type": "string"},
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
                    "sequence": {"type": "integer"},
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
                    "estimated_duration_seconds": {"type": "number"},
                    "narration": {"type": "string"},
                    "source_asset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source_support": {"type": "string"},
                    "visual_intent": {"type": "string"},
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
                    "impact_text": {"type": "string"},
                    "emotional_goal": {"type": "string"},
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
                    "asset_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["asset_id", "reason"],
            },
        },
        "source_integrity_notes": {
            "type": "array",
            "items": {"type": "string"},
        },
        "review_resolution": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "RESOLVED",
                            "PARTIALLY_RESOLVED",
                            "NOT_APPLICABLE",
                        ],
                    },
                    "resolution": {"type": "string"},
                    "source_safety_note": {"type": "string"},
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
            "items": {"type": "string"},
        },
    },
    "required": [
        "story_title",
        "core_thesis",
        "narrative_strategy",
        "target_duration_seconds",
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
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )

    tmp.replace(path)


def count_words(text: str) -> int:
    return len(
        re.findall(
            r"\b[\w’'-]+\b",
            text,
            flags=re.UNICODE,
        )
    )


def normalize_text(text: str) -> str:
    text = (
        text
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )

    return re.sub(r"\s+", " ", text).strip()


def write_status(text: str) -> None:
    STATUS_FILE.write_text(text, encoding="utf-8")


# ============================================================
# SOURCE BUNDLE
# ============================================================

def prepare_source_bundle(analyzed_assets: dict) -> list[dict]:
    result = []

    for asset in analyzed_assets.get("assets", []):
        analysis = asset.get("analysis", {})
        source_text = analysis.get("source_text", {})
        semantic = analysis.get("semantic_summary", {})
        visual = analysis.get("visual", {})
        production = analysis.get("production", {})

        result.append(
            {
                "asset_id": asset.get("asset_id", ""),
                "source_text": {
                    "heading": source_text.get("heading", ""),
                    "body_text": source_text.get("body_text", ""),
                    "prominent_quote": source_text.get(
                        "prominent_quote",
                        "",
                    ),
                    "other_story_text": source_text.get(
                        "other_story_text",
                        [],
                    ),
                },
                "semantic_summary": {
                    "core_idea": semantic.get("core_idea", ""),
                    "concepts": semantic.get("concepts", []),
                    "emotional_tone": semantic.get(
                        "emotional_tone",
                        [],
                    ),
                },
                "named_entities": analysis.get("named_entities", []),
                "visual": {
                    "description": visual.get("description", ""),
                    "important_actions": visual.get(
                        "important_actions",
                        [],
                    ),
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

    return result


# ============================================================
# DETERMINISTIC NORMALIZATION
# ============================================================

def normalize_plan_metrics(plan: dict) -> dict:
    plan = deepcopy(plan)

    narration = plan.get("narration_script", "").strip()
    scenes = plan.get("scenes", [])

    if not narration:
        raise RuntimeError("Plan has empty narration_script.")

    if not scenes:
        raise RuntimeError("Plan has no scenes.")

    word_count = count_words(narration)

    total_duration = round(
        sum(
            float(
                scene.get(
                    "estimated_duration_seconds",
                    0,
                )
            )
            for scene in scenes
        ),
        2,
    )

    if total_duration <= 0:
        raise RuntimeError(
            "Plan has invalid total scene duration."
        )

    effective_wpm = round(
        word_count / total_duration * 60.0,
        1,
    )

    plan["target_word_count"] = word_count
    plan["calculated_word_count"] = word_count
    plan["target_duration_seconds"] = total_duration
    plan["calculated_scene_duration_seconds"] = total_duration
    plan["calculated_effective_wpm"] = effective_wpm

    plan.setdefault("voice_direction", {})
    plan["voice_direction"]["pace_wpm"] = int(round(effective_wpm))

    # Remove stale numeric claims.
    cleaned_summary = []

    for item in plan.get("change_summary", []):
        if re.search(
            r"\b\d{2,3}\s+words?\b",
            item,
            flags=re.IGNORECASE,
        ):
            continue

        if re.search(
            r"\b\d{2,3}(?:\.\d+)?\s*WPM\b",
            item,
            flags=re.IGNORECASE,
        ):
            continue

        cleaned_summary.append(item)

    plan["change_summary"] = cleaned_summary

    return plan


# ============================================================
# DETERMINISTIC CHECKS
# ============================================================

def deterministic_checks(
    plan: dict,
    valid_asset_ids: set[str],
) -> dict:

    narration = plan.get("narration_script", "")
    word_count = count_words(narration)
    scenes = plan.get("scenes", [])

    joined = " ".join(
        scene.get("narration", "").strip()
        for scene in scenes
    )

    total_duration = round(
        sum(
            float(
                scene.get(
                    "estimated_duration_seconds",
                    0,
                )
            )
            for scene in scenes
        ),
        2,
    )

    effective_wpm = round(
        word_count / total_duration * 60.0,
        1,
    ) if total_duration > 0 else 0.0

    unknown_ids = []

    for scene in scenes:
        for asset_id in scene.get("source_asset_ids", []):
            if asset_id not in valid_asset_ids:
                unknown_ids.append(asset_id)

    checks = [
        {
            "check": "scene_narration_matches_script",
            "status": (
                "PASS"
                if normalize_text(joined)
                == normalize_text(narration)
                else "FAIL"
            ),
        },
        {
            "check": "word_count_range",
            "status": (
                "PASS"
                if TARGET_MIN_WORDS
                <= word_count
                <= TARGET_MAX_WORDS
                else "FAIL"
            ),
            "details": (
                f"{word_count} words; expected "
                f"{TARGET_MIN_WORDS}-{TARGET_MAX_WORDS}"
            ),
        },
        {
            "check": "duration_range",
            "status": (
                "PASS"
                if TARGET_MIN_SECONDS
                <= total_duration
                <= TARGET_MAX_SECONDS
                else "FAIL"
            ),
            "details": (
                f"{total_duration:.1f}s; expected "
                f"{TARGET_MIN_SECONDS}-{TARGET_MAX_SECONDS}s"
            ),
        },
        {
            "check": "effective_wpm",
            "status": "PASS",
            "details": f"{effective_wpm:.1f}",
        },
        {
            "check": "valid_source_asset_ids",
            "status": (
                "PASS"
                if not unknown_ids
                else "FAIL"
            ),
            "details": (
                "all valid"
                if not unknown_ids
                else str(sorted(set(unknown_ids)))
            ),
        },
    ]

    return {
        "word_count": word_count,
        "duration_seconds": total_duration,
        "effective_wpm": effective_wpm,
        "checks": checks,
    }


# ============================================================
# API WRAPPER
# ============================================================

def call_json_model(
    client: genai.Client,
    *,
    system_instruction: str,
    prompt: str,
    schema: dict,
    temperature: float,
) -> dict:

    last_error = None

    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=types.Part.from_text(text=prompt),
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=8192,
                ),
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            return json.loads(response.text)

        except Exception as exc:
            last_error = exc

            if attempt < MAX_API_RETRIES:
                time.sleep(
                    RETRY_BASE_SECONDS
                    * (2 ** (attempt - 1))
                )

    raise RuntimeError(
        "Gemini call failed after "
        f"{MAX_API_RETRIES} attempts: {last_error}"
    )


# ============================================================
# REVIEW VALIDATION
# ============================================================

def review_validation_error(review: dict) -> str | None:
    verdict = review.get("verdict")
    ready = review.get("ready_for_voice_generation")
    confidence = review.get("confidence")

    if not isinstance(confidence, (int, float)):
        return "confidence must be numeric."

    if not 0.0 <= float(confidence) <= 1.0:
        return (
            f"confidence must be between 0 and 1; "
            f"received {confidence}."
        )

    scores = review.get("scores", {})

    for name, value in scores.items():
        if not isinstance(value, int):
            return f"Score {name} must be an integer."

        if not 1 <= value <= 10:
            return (
                f"Score {name} must be between 1 and 10; "
                f"received {value}."
            )

    if verdict == "APPROVE":
        if ready is not True:
            return (
                "APPROVE requires "
                "ready_for_voice_generation=true."
            )

        if review.get("issues"):
            return "APPROVE requires issues to be empty."

        if review.get("must_fix_before_voice"):
            return (
                "APPROVE requires "
                "must_fix_before_voice to be empty."
            )

        below_threshold = {
            name: value
            for name, value in scores.items()
            if value < MIN_APPROVAL_SCORE
        }

        if below_threshold:
            return (
                "APPROVE requires every score >= "
                f"{MIN_APPROVAL_SCORE}. "
                f"Below threshold: {below_threshold}"
            )

        if scores.get(
            "source_fidelity",
            0
        ) < MIN_SOURCE_FIDELITY_SCORE:
            return (
                "APPROVE requires source_fidelity >= "
                f"{MIN_SOURCE_FIDELITY_SCORE}."
            )

        if scores.get(
            "internal_consistency",
            0
        ) < MIN_INTERNAL_CONSISTENCY_SCORE:
            return (
                "APPROVE requires internal_consistency >= "
                f"{MIN_INTERNAL_CONSISTENCY_SCORE}."
            )

    elif verdict == "REVISE":
        if ready is not False:
            return (
                "REVISE requires "
                "ready_for_voice_generation=false."
            )

        issues = review.get("issues", [])

        if not issues:
            return (
                "REVISE requires at least one issue."
            )

        issue_ids = [
            item.get("issue_id")
            for item in issues
        ]

        if any(
            not isinstance(issue_id, str)
            or not issue_id.strip()
            for issue_id in issue_ids
        ):
            return (
                "Every review issue must have a non-empty issue_id."
            )

        if len(issue_ids) != len(set(issue_ids)):
            return (
                "Review issue_id values must be unique."
            )

        known_issue_ids = set(issue_ids)
        must_fix = review.get("must_fix_before_voice", [])

        invalid_must_fix = [
            item
            for item in must_fix
            if item not in known_issue_ids
        ]

        if invalid_must_fix:
            return (
                "must_fix_before_voice must contain only exact issue_id "
                "values from issues, never prose. Invalid entries: "
                f"{invalid_must_fix}. Valid issue IDs are: "
                f"{sorted(known_issue_ids)}"
            )

        major_or_blocker_ids = {
            item.get("issue_id")
            for item in issues
            if item.get("severity") in {"MAJOR", "BLOCKER"}
        }

        missing_required = sorted(
            major_or_blocker_ids - set(must_fix)
        )

        if missing_required:
            return (
                "Every MAJOR or BLOCKER issue must appear in "
                "must_fix_before_voice. Missing: "
                f"{missing_required}"
            )

        suggestions = review.get("suggestions", [])

        if not suggestions:
            return (
                "REVISE requires at least one suggestion."
            )

        bad_related_ids = []

        for suggestion in suggestions:
            for related_id in suggestion.get("related_issue_ids", []):
                if related_id not in known_issue_ids:
                    bad_related_ids.append(related_id)

        if bad_related_ids:
            return (
                "suggestion.related_issue_ids must contain only exact "
                "issue_id values from issues. Invalid entries: "
                f"{sorted(set(bad_related_ids))}"
            )

    else:
        return f"Unexpected verdict: {verdict}"

    return None


def review_plan(
    client: genai.Client,
    plan: dict,
    source_bundle: list[dict],
    checks: dict,
    history: list[dict],
    iteration: int,
) -> dict:

    correction = ""

    for validation_attempt in range(
        1,
        MAX_REVIEW_VALIDATION_RETRIES + 1,
    ):
        correction_block = ""

        if correction:
            correction_block = f"""
Your previous review response violated the approval contract:

{correction}

Re-evaluate the CURRENT plan and return a logically consistent
review. Do not simply change the numbers to force approval.
""".strip()

        prompt = f"""
Review iteration {iteration} of the story-quality loop.

Determine whether the CURRENT plan is ready for voice generation.

Python has normalized numeric metadata.
Treat deterministic arithmetic as authoritative.

{correction_block}

============================================================
CURRENT PLAN
============================================================

{json.dumps(
    plan,
    indent=2,
    ensure_ascii=False,
)}

============================================================
DETERMINISTIC CHECKS
============================================================

{json.dumps(
    checks,
    indent=2,
    ensure_ascii=False,
)}

============================================================
PRIOR LOOP HISTORY
============================================================

{json.dumps(
    history,
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

Pay special attention to source uncertainties and ambiguous
named references in the narration.

Return only the requested review JSON.
""".strip()

        review = call_json_model(
            client,
            system_instruction=REVIEWER_SYSTEM_INSTRUCTION,
            prompt=prompt,
            schema=REVIEW_SCHEMA,
            temperature=0.15,
        )

        error = review_validation_error(review)

        failed_checks = [
            item.get("check")
            for item in checks.get("checks", [])
            if item.get("status") == "FAIL"
        ]

        if (
            error is None
            and review.get("verdict") == "APPROVE"
            and failed_checks
        ):
            error = (
                "APPROVE is not allowed while deterministic checks "
                f"are failing: {failed_checks}"
            )

        if error is None:
            review["iteration"] = iteration
            review["reviewed_at_utc"] = now_utc()
            review["model"] = MODEL
            review["deterministic_checks"] = checks
            return review

        correction = error

    raise RuntimeError(
        "Reviewer repeatedly violated the approval contract. "
        f"Last validation error: {correction}"
    )


# ============================================================
# REVISION
# ============================================================

def validate_revision(
    plan: dict,
    review: dict,
    valid_asset_ids: set[str],
) -> None:

    narration = plan.get("narration_script", "").strip()

    if not narration:
        raise RuntimeError(
            "Revision returned empty narration."
        )

    word_count = count_words(narration)

    if not (
        TARGET_MIN_WORDS
        <= word_count
        <= TARGET_MAX_WORDS
    ):
        raise RuntimeError(
            f"Revision has {word_count} words; "
            f"expected {TARGET_MIN_WORDS}-"
            f"{TARGET_MAX_WORDS}."
        )

    scenes = plan.get("scenes", [])

    if not scenes:
        raise RuntimeError(
            "Revision returned no scenes."
        )

    expected = list(range(1, len(scenes) + 1))
    actual = [
        int(scene.get("sequence", -1))
        for scene in scenes
    ]

    if actual != expected:
        raise RuntimeError(
            "Scene sequence numbering is invalid."
        )

    joined = " ".join(
        scene.get("narration", "").strip()
        for scene in scenes
    )

    if normalize_text(joined) != normalize_text(narration):
        raise RuntimeError(
            "Scene narration does not reconstruct "
            "narration_script."
        )

    total_duration = sum(
        float(
            scene.get(
                "estimated_duration_seconds",
                0,
            )
        )
        for scene in scenes
    )

    if not (
        TARGET_MIN_SECONDS
        <= total_duration
        <= TARGET_MAX_SECONDS
    ):
        raise RuntimeError(
            f"Revision duration is {total_duration:.1f}s."
        )

    for scene in scenes:
        asset_ids = scene.get("source_asset_ids", [])

        if not asset_ids:
            raise RuntimeError(
                f"Scene {scene.get('sequence')} "
                f"has no source assets."
            )

        unknown_ids = [
            asset_id
            for asset_id in asset_ids
            if asset_id not in valid_asset_ids
        ]

        if unknown_ids:
            raise RuntimeError(
                f"Unknown source asset IDs: {unknown_ids}"
            )

    required_ids = set(
        review.get(
            "must_fix_before_voice",
            [],
        )
    )

    resolution_map = {
        item.get("issue_id"): item
        for item in plan.get(
            "review_resolution",
            [],
        )
    }

    missing = [
        issue_id
        for issue_id in required_ids
        if issue_id not in resolution_map
    ]

    if missing:
        raise RuntimeError(
            f"Revision is missing resolutions "
            f"for: {missing}"
        )


def revise_plan(
    client: genai.Client,
    plan: dict,
    review: dict,
    source_bundle: list[dict],
    valid_asset_ids: set[str],
    iteration: int,
) -> dict:

    correction = ""
    last_error = ""

    for validation_attempt in range(
        1,
        MAX_REVISION_VALIDATION_RETRIES + 1,
    ):

        correction_block = ""

        if correction:
            correction_block = f"""
Your previous revision failed structural validation:

{correction}

Correct that exact problem. Do not ignore it, and do not remove
required review-resolution entries merely to make validation pass.
""".strip()

        prompt = f"""
This is revision cycle {iteration}.

Revise the CURRENT plan using the latest review.

Make the smallest useful changes required.

If narration is already within the allowed word range, do not add
filler merely to change the word count.

If scene density is the issue, prefer restructuring scenes rather
than unnecessarily changing strong narration.

must_fix_before_voice contains ISSUE IDs only. For each listed ID,
include a review_resolution object using that exact same issue_id.

{correction_block}

============================================================
CURRENT PLAN
============================================================

{json.dumps(
    plan,
    indent=2,
    ensure_ascii=False,
)}

============================================================
LATEST REVIEW
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

Return only the complete revised plan.
""".strip()

        try:
            revised = call_json_model(
                client,
                system_instruction=REVISION_SYSTEM_INSTRUCTION,
                prompt=prompt,
                schema=PLAN_SCHEMA,
                temperature=0.25,
            )

            validate_revision(
                revised,
                review,
                valid_asset_ids,
            )

            revised = normalize_plan_metrics(
                revised
            )

            revised["revision_iteration"] = iteration
            revised["revised_at_utc"] = now_utc()
            revised["model"] = MODEL

            return revised

        except RuntimeError as exc:
            correction = str(exc)
            last_error = str(exc)

    raise RuntimeError(
        "Revision Agent repeatedly returned an invalid plan after "
        f"{MAX_REVISION_VALIDATION_RETRIES} validation attempts. "
        f"Last issue: {last_error}"
    )


# ============================================================
# FINAL TEXT
# ============================================================

def write_final_text(
    plan: dict,
    review: dict,
    iterations_used: int,
) -> None:

    lines = [
        f"STORY: {plan['story_title']}",
        f"VERDICT: {review['verdict']}",
        f"QUALITY LOOP ITERATIONS: {iterations_used}",
        f"WORDS: {plan['calculated_word_count']}",
        (
            "PLANNED DURATION: "
            f"{plan['calculated_scene_duration_seconds']:.1f}s"
        ),
        (
            "EFFECTIVE WPM: "
            f"{plan['calculated_effective_wpm']:.1f}"
        ),
        "",
        "=" * 72,
        "",
        "FINAL NARRATION",
        "",
        plan["narration_script"],
        "",
        "=" * 72,
        "",
        "SCENES",
        "",
    ]

    for scene in plan["scenes"]:
        lines.extend(
            [
                (
                    f"{scene['sequence']:02d}. "
                    f"{scene['role']} "
                    f"({scene['estimated_duration_seconds']:.1f}s)"
                ),
                f"    Narration: {scene['narration']}",
                (
                    "    Assets: "
                    f"{', '.join(scene['source_asset_ids'])}"
                ),
                f"    Visual intent: {scene['visual_intent']}",
            ]
        )

        if scene["impact_text"].strip():
            lines.append(
                f"    Impact text: {scene['impact_text']}"
            )

        lines.append("")

    lines.extend(
        [
            "FINAL REVIEW SUMMARY",
            review["review_summary"],
            "",
            "FINAL REVIEW SCORES",
        ]
    )

    for key, value in review["scores"].items():
        lines.append(f"- {key}: {value}/10")

    FINAL_TEXT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main() -> None:

    ensure_dirs()

    if not INITIAL_STORY_PLAN_FILE.exists():
        raise FileNotFoundError(
            f"Missing: {INITIAL_STORY_PLAN_FILE}"
        )

    if not ANALYZED_ASSETS_FILE.exists():
        raise FileNotFoundError(
            f"Missing: {ANALYZED_ASSETS_FILE}"
        )

    analyzed_assets = load_json(
        ANALYZED_ASSETS_FILE
    )

    source_bundle = prepare_source_bundle(
        analyzed_assets
    )

    valid_asset_ids = {
        item["asset_id"]
        for item in source_bundle
    }

    current_plan = load_json(
        INITIAL_STORY_PLAN_FILE
    )

    current_plan.setdefault(
        "review_resolution",
        [],
    )

    current_plan.setdefault(
        "change_summary",
        [],
    )

    current_plan = normalize_plan_metrics(
        current_plan
    )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(
            api_version="v1"
        ),
    )

    history = []

    write_status(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Max iterations: {MAX_ITERATIONS}\n"
    )

    for iteration in range(
        1,
        MAX_ITERATIONS + 1,
    ):

        print()
        print(
            f"=== QUALITY LOOP "
            f"{iteration}/{MAX_ITERATIONS} ==="
        )

        candidate_file = (
            OUTPUT_DIR
            / f"iteration_{iteration:02d}_plan.json"
        )

        save_json(
            candidate_file,
            current_plan,
        )

        checks = deterministic_checks(
            current_plan,
            valid_asset_ids,
        )

        review = review_plan(
            client=client,
            plan=current_plan,
            source_bundle=source_bundle,
            checks=checks,
            history=history,
            iteration=iteration,
        )

        review_file = (
            OUTPUT_DIR
            / f"iteration_{iteration:02d}_review.json"
        )

        save_json(
            review_file,
            review,
        )

        print(
            f"Verdict: {review['verdict']}"
        )

        print(
            f"Confidence: {review['confidence']}"
        )

        print(
            f"Scores: {review['scores']}"
        )

        if (
            review["verdict"] == "APPROVE"
            and review[
                "ready_for_voice_generation"
            ] is True
        ):

            final_plan = deepcopy(
                current_plan
            )

            final_plan["quality_loop"] = {
                "status": "APPROVED",
                "iterations_used": iteration,
                "max_iterations": MAX_ITERATIONS,
                "approved_at_utc": now_utc(),
                "final_review": review,
            }

            final_plan[
                "pipeline_schema_version"
            ] = PIPELINE_SCHEMA_VERSION

            save_json(
                FINAL_PLAN_FILE,
                final_plan,
            )

            write_final_text(
                final_plan,
                review,
                iteration,
            )

            save_json(
                OUTPUT_DIR / "history.json",
                {
                    "status": "APPROVED",
                    "iterations_used": iteration,
                    "history": history
                    + [
                        {
                            "iteration": iteration,
                            "verdict": "APPROVE",
                            "review_file": str(
                                review_file
                            ),
                            "plan_file": str(
                                candidate_file
                            ),
                        }
                    ],
                },
            )

            write_status(
                "APPROVED\n"
                f"Finished: {now_utc()}\n"
                f"Iterations used: {iteration}\n"
                f"Final plan: {FINAL_PLAN_FILE}\n"
            )

            print()
            print(
                "Story quality loop APPROVED."
            )

            print(
                f"Final plan: {FINAL_PLAN_FILE}"
            )

            print(
                f"Readable: {FINAL_TEXT_FILE}"
            )

            return

        if iteration == MAX_ITERATIONS:

            best_effort = deepcopy(
                current_plan
            )

            best_effort["quality_loop"] = {
                "status": "NOT_APPROVED",
                "iterations_used": iteration,
                "max_iterations": MAX_ITERATIONS,
                "finished_at_utc": now_utc(),
                "final_review": review,
            }

            save_json(
                OUTPUT_DIR
                / "best_effort_story_plan.json",
                best_effort,
            )

            write_status(
                "NOT_APPROVED\n"
                f"Finished: {now_utc()}\n"
                f"Maximum iterations reached: "
                f"{MAX_ITERATIONS}\n"
            )

            print()
            print(
                "Maximum iterations reached "
                "without approval."
            )

            return

        history.append(
            {
                "iteration": iteration,
                "verdict": review["verdict"],
                "review_summary": review[
                    "review_summary"
                ],
                "must_fix_before_voice": review[
                    "must_fix_before_voice"
                ],
                "scores": review["scores"],
            }
        )

        current_plan = revise_plan(
            client=client,
            plan=current_plan,
            review=review,
            source_bundle=source_bundle,
            valid_asset_ids=valid_asset_ids,
            iteration=iteration,
        )

        save_json(
            OUTPUT_DIR
            / f"iteration_{iteration:02d}_revision.json",
            current_plan,
        )

    raise RuntimeError(
        "Quality loop ended unexpectedly."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception:

        ensure_dirs()

        error = traceback.format_exc()

        write_status(
            "FAILED\n\n"
            + error
        )

        print()
        print(
            "Story quality loop failed."
        )

        print(
            f"See: {STATUS_FILE}"
        )

        print()

        sys.exit(1)
