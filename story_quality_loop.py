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
MODEL = "gemini-2.5-flash"

INITIAL_STORY_PLAN_FILE = Path("metadata/story_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")

OUTPUT_DIR = Path("metadata/story_quality_loop")
FINAL_PLAN_FILE = Path("metadata/final_story_plan.json")
FINAL_TEXT_FILE = Path("metadata/final_story_plan.txt")
STATUS_FILE = Path("metadata/story_quality_loop_status.txt")

# Maximum number of REVIEW cycles.
# Each failed review may trigger one revision before the next cycle.
MAX_ITERATIONS = 4

# API-level retries for transient failures.
MAX_API_RETRIES = 3
RETRY_BASE_SECONDS = 2

TARGET_MIN_SECONDS = 40
TARGET_MAX_SECONDS = 50
TARGET_MIN_WORDS = 90
TARGET_MAX_WORDS = 115

PIPELINE_SCHEMA_VERSION = "1.0"


# ============================================================
# REVIEWER SYSTEM INSTRUCTION
# ============================================================

REVIEWER_SYSTEM_INSTRUCTION = """
You are the independent final editorial QA reviewer for an
automated short-form wisdom video pipeline.

You receive:
- the current story plan
- structured analyses of the original source images
- deterministic validation checks produced by Python
- the history of previous review/revision cycles, when available

Your job is to decide whether the CURRENT plan is genuinely ready
for voice generation.

IMPORTANT RULES

1. SOURCE FIDELITY
   Every factual claim, quotation, person, event, and concept must
   be supported by the supplied source analyses.

2. NO OUTSIDE KNOWLEDGE
   Do not fill source gaps from general knowledge.

3. REVIEW THE CURRENT RESULT
   Do not approve merely because a previous revision claims that
   an issue was fixed.

4. STORY QUALITY
   Judge this as spoken short-form storytelling, not as an essay.

5. HOOK
   The opening should create curiosity or tension quickly.

6. SPOKEN NATURALNESS
   Flag stiff, summary-like, over-compressed, or awkward phrasing.

7. NARRATIVE ARC
   Prefer a clear progression:
   hook -> concrete story/tension -> reversal/reveal ->
   explanation -> reflection.

8. CLARITY
   Flag names or references that may confuse a viewer.

9. PACING
   The narration, scene durations, and voice pace should make sense
   together. Python arithmetic is the ground truth for numeric checks.

10. SCENE STRUCTURE
    Flag scenes that carry too many distinct conceptual or visual
    beats.

11. VISUAL SUPPORT
    Every scene should be supportable by the cited source assets.

12. PRESERVE WHAT WORKS
    Do not recommend changes merely for novelty.

13. APPROVE only if there are no meaningful fixes required before
    voice generation.

14. If APPROVE:
    ready_for_voice_generation must be true.

15. If REVISE:
    ready_for_voice_generation must be false, and return actionable
    suggestions for the Revision Agent.

16. Do not rewrite the full story yourself.

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
the review while preserving the story's strongest elements.

IMPORTANT RULES

1. SOURCE FIDELITY IS NON-NEGOTIABLE.
   Do not add facts unsupported by the source analyses.

2. NO OUTSIDE KNOWLEDGE.

3. FIX THE UNDERLYING ISSUE.
   Do not blindly copy reviewer wording.

4. PRESERVE STRONG MATERIAL.
   Keep strong hooks, source-supported quotations, clear story beats,
   and reflective endings unless changing them is necessary.

5. SPOKEN STORYTELLING.
   The narration must sound natural aloud.

6. DO NOT PAD THE SCRIPT JUST TO HIT A NUMBER.
   If narration is already within the allowed word range, prefer
   structural or metadata fixes over filler.

7. SCENE GRANULARITY.
   You may split an overloaded scene into smaller beats.

8. NARRATION AND SCENES MUST MATCH.
   Concatenating scene narration in order must reconstruct the full
   narration_script.

9. TARGET:
   roughly 40-50 seconds and 90-115 spoken words.

10. VOICE:
    mature, calm, confident, reflective, slightly intense.

11. EVERY SCENE MUST CITE VALID SOURCE ASSET IDS.

12. Resolve every issue in must_fix_before_voice.

13. Do not claim exact calculated word counts or WPM in prose.
    Python will calculate those deterministically after your response.

14. Return a COMPLETE revised story plan.

Return only JSON matching the requested schema.
""".strip()


# ============================================================
# SHARED SCHEMAS
# ============================================================

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["APPROVE", "REVISE"],
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
                    "estimated_duration_seconds": {
                        "type": "number",
                    },
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
# BASIC HELPERS
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

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def write_status(text: str) -> None:
    STATUS_FILE.write_text(
        text,
        encoding="utf-8",
    )


# ============================================================
# SOURCE BUNDLE
# ============================================================

def prepare_source_bundle(
    analyzed_assets: dict,
) -> list[dict]:

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

                "named_entities": analysis.get(
                    "named_entities",
                    [],
                ),

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

                "uncertainties": analysis.get(
                    "uncertainties",
                    [],
                ),
            }
        )

    return result


# ============================================================
# DETERMINISTIC NORMALIZATION
# ============================================================

def normalize_plan_metrics(plan: dict) -> dict:
    """
    Arithmetic belongs in Python, not in the LLM.
    """

    plan = deepcopy(plan)

    narration = plan.get("narration_script", "").strip()

    if not narration:
        raise RuntimeError("Plan has empty narration_script.")

    scenes = plan.get("scenes", [])

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

    # Normalize the plan to the reality of the current script.
    plan["target_word_count"] = word_count
    plan["calculated_word_count"] = word_count
    plan["target_duration_seconds"] = total_duration
    plan["calculated_scene_duration_seconds"] = total_duration
    plan["calculated_effective_wpm"] = effective_wpm

    plan.setdefault("voice_direction", {})
    plan["voice_direction"]["pace_wpm"] = int(
        round(effective_wpm)
    )

    # Remove stale numerical claims from summaries.
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
# DETERMINISTIC QA CHECKS
# ============================================================

def deterministic_checks(
    plan: dict,
    valid_asset_ids: set[str],
) -> dict:

    checks = []

    narration = plan.get("narration_script", "")
    word_count = count_words(narration)

    scenes = plan.get("scenes", [])

    scene_narration = " ".join(
        scene.get("narration", "").strip()
        for scene in scenes
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
        }
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

    checks.append(
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
        }
    )

    checks.append(
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
        }
    )

    checks.append(
        {
            "check": "effective_wpm",
            "status": "PASS",
            "details": f"{effective_wpm:.1f}",
        }
    )

    unknown_ids = []

    for scene in scenes:
        for asset_id in scene.get(
            "source_asset_ids",
            [],
        ):
            if asset_id not in valid_asset_ids:
                unknown_ids.append(asset_id)

    checks.append(
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
        }
    )

    return {
        "word_count": word_count,
        "duration_seconds": total_duration,
        "effective_wpm": effective_wpm,
        "checks": checks,
    }


# ============================================================
# LLM CALL WRAPPER
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

    for attempt in range(
        1,
        MAX_API_RETRIES + 1,
    ):

        try:
            response = client.models.generate_content(
                model=MODEL,

                contents=types.Part.from_text(
                    text=prompt
                ),

                config=types.GenerateContentConfig(
                    system_instruction=
                        system_instruction,

                    temperature=temperature,

                    response_mime_type=
                        "application/json",

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
        f"{MAX_API_RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# REVIEW
# ============================================================

def validate_review(review: dict) -> None:

    verdict = review.get("verdict")
    ready = review.get(
        "ready_for_voice_generation"
    )

    if verdict == "APPROVE" and ready is not True:
        raise RuntimeError(
            "APPROVE requires "
            "ready_for_voice_generation=true."
        )

    if verdict == "REVISE" and ready is not False:
        raise RuntimeError(
            "REVISE requires "
            "ready_for_voice_generation=false."
        )

    if verdict == "REVISE" and not review.get(
        "suggestions"
    ):
        raise RuntimeError(
            "REVISE requires at least one suggestion."
        )

    for name, value in review.get(
        "scores",
        {}
    ).items():

        if not isinstance(value, int):
            raise RuntimeError(
                f"Score {name} is not an integer."
            )

        if not 1 <= value <= 10:
            raise RuntimeError(
                f"Score {name} must be 1-10."
            )


def review_plan(
    client: genai.Client,
    plan: dict,
    source_bundle: list[dict],
    checks: dict,
    history: list[dict],
    iteration: int,
) -> dict:

    prompt = f"""
Review iteration {iteration} of the story-quality loop.

Determine whether the CURRENT plan is ready for voice generation.

Python has already normalized the current numeric metadata.
Treat the deterministic checks as authoritative for arithmetic.

Do not rewrite the full story.

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

Return only the requested review JSON.
""".strip()

    review = call_json_model(
        client,
        system_instruction=
            REVIEWER_SYSTEM_INSTRUCTION,
        prompt=prompt,
        schema=REVIEW_SCHEMA,
        temperature=0.15,
    )

    validate_review(review)

    review["iteration"] = iteration
    review["reviewed_at_utc"] = now_utc()
    review["model"] = MODEL
    review["deterministic_checks"] = checks

    return review


# ============================================================
# REVISION
# ============================================================

def validate_revision(
    plan: dict,
    review: dict,
    valid_asset_ids: set[str],
) -> None:

    narration = plan.get(
        "narration_script",
        "",
    ).strip()

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

    expected_sequences = list(
        range(1, len(scenes) + 1)
    )

    actual_sequences = [
        int(scene.get("sequence", -1))
        for scene in scenes
    ]

    if actual_sequences != expected_sequences:
        raise RuntimeError(
            "Scene sequence numbering is invalid."
        )

    joined = " ".join(
        scene.get("narration", "").strip()
        for scene in scenes
    )

    if normalize_text(joined) != normalize_text(
        narration
    ):
        raise RuntimeError(
            "Scene narration does not reconstruct "
            "the narration_script."
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
            f"Revision duration is "
            f"{total_duration:.1f}s."
        )

    for scene in scenes:

        asset_ids = scene.get(
            "source_asset_ids",
            [],
        )

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
                f"Unknown source asset IDs: "
                f"{unknown_ids}"
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

    prompt = f"""
This is revision cycle {iteration}.

Revise the CURRENT plan using the latest review.

Make the smallest useful changes required.

If the current narration is already within the allowed word range,
do not add filler merely to change the word count.

If scene density is the issue, prefer restructuring scenes rather
than unnecessarily changing strong narration.

Resolve every ID in must_fix_before_voice.

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

    revised = call_json_model(
        client,
        system_instruction=
            REVISION_SYSTEM_INSTRUCTION,
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


# ============================================================
# HUMAN-READABLE FINAL OUTPUT
# ============================================================

def write_final_text(
    plan: dict,
    review: dict,
    iterations_used: int,
) -> None:

    lines = []

    lines.append(
        f"STORY: {plan['story_title']}"
    )

    lines.append(
        f"VERDICT: {review['verdict']}"
    )

    lines.append(
        f"QUALITY LOOP ITERATIONS: "
        f"{iterations_used}"
    )

    lines.append(
        f"WORDS: "
        f"{plan['calculated_word_count']}"
    )

    lines.append(
        f"PLANNED DURATION: "
        f"{plan['calculated_scene_duration_seconds']:.1f}s"
    )

    lines.append(
        f"EFFECTIVE WPM: "
        f"{plan['calculated_effective_wpm']:.1f}"
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
        plan[
            "narration_script"
        ]
    )

    lines.append("")
    lines.append(
        "=" * 72
    )
    lines.append("")
    lines.append(
        "SCENES"
    )
    lines.append("")

    for scene in plan["scenes"]:

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
            f"    Visual intent: "
            f"{scene['visual_intent']}"
        )

        if scene[
            "impact_text"
        ].strip():

            lines.append(
                f"    Impact text: "
                f"{scene['impact_text']}"
            )

        lines.append("")

    lines.append(
        "FINAL REVIEW SUMMARY"
    )

    lines.append(
        review[
            "review_summary"
        ]
    )

    FINAL_TEXT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


# ============================================================
# MAIN QUALITY LOOP
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

    # Add fields expected by the loop if the first Story Director
    # output did not contain them.
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

    final_review = None

    for iteration in range(
        1,
        MAX_ITERATIONS + 1,
    ):

        print()
        print(
            f"=== QUALITY LOOP "
            f"{iteration}/{MAX_ITERATIONS} ==="
        )

        # -----------------------------------------------
        # Save current candidate
        # -----------------------------------------------

        candidate_file = (
            OUTPUT_DIR
            / f"iteration_{iteration:02d}_plan.json"
        )

        save_json(
            candidate_file,
            current_plan,
        )

        # -----------------------------------------------
        # Deterministic QA
        # -----------------------------------------------

        checks = deterministic_checks(
            current_plan,
            valid_asset_ids,
        )

        # -----------------------------------------------
        # Gemini reviewer
        # -----------------------------------------------

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
            f"Ready for voice: "
            f"{review['ready_for_voice_generation']}"
        )

        # -----------------------------------------------
        # APPROVED -> FINAL OUTPUT
        # -----------------------------------------------

        if (
            review["verdict"] == "APPROVE"
            and review[
                "ready_for_voice_generation"
            ] is True
        ):

            final_review = review

            final_plan = deepcopy(
                current_plan
            )

            final_plan[
                "quality_loop"
            ] = {
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
                plan=final_plan,
                review=review,
                iterations_used=iteration,
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
                f"Final plan: "
                f"{FINAL_PLAN_FILE}"
            )

            print(
                f"Readable: "
                f"{FINAL_TEXT_FILE}"
            )

            return

        # -----------------------------------------------
        # MAX ITERATIONS REACHED
        # -----------------------------------------------

        if iteration == MAX_ITERATIONS:

            final_review = review

            best_effort = deepcopy(
                current_plan
            )

            best_effort[
                "quality_loop"
            ] = {
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

            save_json(
                OUTPUT_DIR / "history.json",
                {
                    "status": "NOT_APPROVED",
                    "iterations_used": iteration,
                    "history": history
                    + [
                        {
                            "iteration": iteration,
                            "verdict": review[
                                "verdict"
                            ],
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
                "NOT_APPROVED\n"
                f"Finished: {now_utc()}\n"
                f"Maximum iterations reached: "
                f"{MAX_ITERATIONS}\n"
            )

            print()
            print(
                "Maximum quality-loop iterations "
                "were reached without approval."
            )

            print(
                "Best-effort plan saved to:"
            )

            print(
                OUTPUT_DIR
                / "best_effort_story_plan.json"
            )

            return

        # -----------------------------------------------
        # REVISE
        # -----------------------------------------------

        history.append(
            {
                "iteration": iteration,
                "verdict": review[
                    "verdict"
                ],
                "review_summary": review[
                    "review_summary"
                ],
                "must_fix_before_voice":
                    review[
                        "must_fix_before_voice"
                    ],
            }
        )

        revised = revise_plan(
            client=client,
            plan=current_plan,
            review=review,
            source_bundle=source_bundle,
            valid_asset_ids=valid_asset_ids,
            iteration=iteration,
        )

        revision_file = (
            OUTPUT_DIR
            / f"iteration_{iteration:02d}_revision.json"
        )

        save_json(
            revision_file,
            revised,
        )

        current_plan = revised

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
