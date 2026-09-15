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
MODEL = "gemini-3.1-pro-preview"

WORD_TIMING_FILE = Path("metadata/word_timing.json")
STORY_PLAN_FILE = Path("metadata/final_story_plan.json")

OUTPUT_JSON = Path("metadata/subtitle_cues.json")
OUTPUT_SRT = Path("metadata/subtitles.srt")
OUTPUT_TEXT = Path("metadata/subtitle_cues.txt")
RAW_RESPONSE_FILE = Path("metadata/subtitle_director_raw_response.txt")
STATUS_FILE = Path("metadata/subtitle_generation_status.txt")

MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2

MIN_WORDS_PER_CUE = 2
MAX_WORDS_PER_CUE = 6

# This is a soft editorial target, not a hard audio-duration limit.
TARGET_MAX_CUE_SECONDS = 2.6

MIN_ALIGNMENT_RATIO = 0.98


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are the Caption Director for a premium vertical short-form video.

You receive the final locked narration as an ordered list of words
with exact audio timestamps.

Your ONLY creative job is to group those numbered words into
natural, readable subtitle phrases.

The Python controller will construct the final subtitle text and
timestamps. You must not rewrite any words.

RULES

1. COVER EVERY WORD EXACTLY ONCE.
   Word indices must start at 1 and end at the final word.

2. CUES MUST BE CONTIGUOUS.
   No gaps, overlaps, reordering, duplication, or omitted indices.

3. DO NOT REWRITE THE NARRATION.
   Return only start_word_index and end_word_index for each cue,
   plus a short rationale/style classification.

4. Prefer 2-6 spoken words per cue.

5. Prefer semantic phrases over mechanical equal-sized chunks.

6. Avoid awkward splits such as:
   "He faced 30" / "years of rejection"
   when a more natural grouping is possible.

7. Respect punctuation, sentence boundaries, and audible pauses.

8. A cue should normally feel comfortable on screen for roughly
   0.7-2.6 seconds, but meaning is more important than forcing
   identical durations.

9. "Don't try." must be its own cue.

10. Do not combine words across a strong sentence boundary.

11. Keep important philosophical phrases intact where practical:
    - The Backwards Law
    - good feelings
    - accepting who he was
    - stop obsessing over the outcome
    - Accept discomfort
    - loses its power

12. The final reflective question should feel deliberate and readable.

13. style_hint must be one of:
    NORMAL
    EMPHASIS
    IMPACT
    REFLECTION

Use IMPACT sparingly for the central paradox or a major reveal.
Use REFLECTION for the final inward-looking question.
Use EMPHASIS for a particularly important phrase.
Everything else is NORMAL.

Return only JSON matching the requested schema.
""".strip()


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "cues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_word_index": {
                        "type": "integer",
                    },
                    "end_word_index": {
                        "type": "integer",
                    },
                    "style_hint": {
                        "type": "string",
                        "enum": [
                            "NORMAL",
                            "EMPHASIS",
                            "IMPACT",
                            "REFLECTION",
                        ],
                    },
                    "reason": {
                        "type": "string",
                    },
                },
                "required": [
                    "start_word_index",
                    "end_word_index",
                    "style_hint",
                    "reason",
                ],
            },
        }
    },
    "required": [
        "cues",
    ],
}


# ============================================================
# HELPERS
# ============================================================

WORD_RE = re.compile(
    r"\b[\w’'-]+\b",
    flags=re.UNICODE,
)


def now_utc() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


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


def normalize_word(
    text: str,
) -> str:

    return re.sub(
        r"[^\w]+",
        "",
        text
        .replace("’", "'")
        .lower(),
        flags=re.UNICODE,
    )


def format_srt_time(
    seconds: float,
) -> str:

    milliseconds = int(
        round(
            seconds
            * 1000
        )
    )

    hours = (
        milliseconds
        // 3_600_000
    )

    milliseconds %= (
        3_600_000
    )

    minutes = (
        milliseconds
        // 60_000
    )

    milliseconds %= (
        60_000
    )

    secs = (
        milliseconds
        // 1000
    )

    millis = (
        milliseconds
        % 1000
    )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{millis:03d}"
    )


# ============================================================
# SOURCE-TEXT MAPPING
# ============================================================

def build_narration_word_spans(
    narration: str,
    timed_words: list[dict],
) -> list[dict]:

    matches = list(
        WORD_RE.finditer(
            narration
        )
    )

    if len(matches) != len(
        timed_words
    ):
        raise RuntimeError(
            "Narration token count does not "
            "match word_timing.json.\n"
            f"Narration tokens: {len(matches)}\n"
            f"Timed words: {len(timed_words)}"
        )

    spans = []

    for index, (
        match,
        timed,
    ) in enumerate(
        zip(
            matches,
            timed_words,
        ),
        start=1,
    ):

        narration_word = (
            match.group()
        )

        timed_word = timed[
            "canonical_word"
        ]

        if normalize_word(
            narration_word
        ) != normalize_word(
            timed_word
        ):
            raise RuntimeError(
                "Locked narration and timed "
                "word sequence diverged at "
                f"word {index}: "
                f"{narration_word!r} vs "
                f"{timed_word!r}"
            )

        spans.append(
            {
                "index": index,
                "word": narration_word,
                "char_start": match.start(),
                "char_end": match.end(),
                "start_seconds": float(
                    timed[
                        "start_seconds"
                    ]
                ),
                "end_seconds": float(
                    timed[
                        "end_seconds"
                    ]
                ),
            }
        )

    return spans


def display_text_for_range(
    narration: str,
    spans: list[dict],
    start_index: int,
    end_index: int,
) -> str:

    start = spans[
        start_index - 1
    ]

    end = spans[
        end_index - 1
    ]

    raw = narration[
        start["char_start"]:
        end["char_end"]
    ]

    # Include punctuation immediately following the final word,
    # stopping before the next spoken word.
    if end_index < len(
        spans
    ):

        trailing = narration[
            end["char_end"]:
            spans[
                end_index
            ][
                "char_start"
            ]
        ]

    else:
        trailing = narration[
            end["char_end"]:
        ]

    punctuation = "".join(
        character
        for character in trailing
        if character
        in ".,!?;:'\"”’"
    )

    # If this cue starts inside a quotation, preserve only the
    # opening quote, not punctuation belonging to the prior phrase.
    if start_index > 1:

        between = narration[
            spans[
                start_index - 2
            ][
                "char_end"
            ]:
            start[
                "char_start"
            ]
        ]

        quote_candidates = [
            character
            for character
            in between
            if character
            in "'\"“‘"
        ]

        if quote_candidates:
            raw = (
                quote_candidates[-1]
                + raw
            )

    # Avoid carrying a colon or comma into a cue merely because
    # the next word begins after it. Keep punctuation only when
    # this range actually ends at that punctuation.
    if punctuation:

        terminal = punctuation

        # A cue ending just before an opening quote may see both
        # a colon and quote. Keep the colon but not the opening quote.
        if (
            terminal.endswith(
                ("'", '"', "“", "‘")
            )
            and not terminal.startswith(
                (".", "!", "?")
            )
        ):
            terminal = terminal[:-1]

        raw += terminal

    return re.sub(
        r"\s+",
        " ",
        raw,
    ).strip()


# ============================================================
# MODEL PROMPT
# ============================================================

def build_prompt(
    narration: str,
    spans: list[dict],
    voice_direction: dict,
    correction: str = "",
) -> str:

    word_lines = []

    for item in spans:

        word_lines.append(
            f"{item['index']:03d} | "
            f"{item['start_seconds']:.2f}-"
            f"{item['end_seconds']:.2f} | "
            f"{item['word']}"
        )

    correction_block = ""

    if correction:

        correction_block = f"""
Your previous grouping failed deterministic validation:

{correction}

Correct the grouping while preserving every word exactly once.
""".strip()

    emphasis = voice_direction.get(
        "emphasis_phrases",
        [],
    )

    pauses = voice_direction.get(
        "pause_after_phrases",
        [],
    )

    return f"""
Create subtitle phrase groupings for this locked narration.

The narration wording itself is immutable.

{correction_block}

============================================================
LOCKED NARRATION
============================================================

{narration}

============================================================
VOICE EMPHASIS
============================================================

{json.dumps(
    emphasis,
    indent=2,
    ensure_ascii=False,
)}

============================================================
PLANNED PAUSES
============================================================

{json.dumps(
    pauses,
    indent=2,
    ensure_ascii=False,
)}

============================================================
NUMBERED WORD TIMINGS
============================================================

{chr(10).join(word_lines)}

Return only cue index ranges.
""".strip()


# ============================================================
# VALIDATION
# ============================================================

def validate_groups(
    result: dict,
    word_count: int,
) -> tuple[
    bool,
    str,
]:

    cues = result.get(
        "cues",
        [],
    )

    if not cues:
        return (
            False,
            "No subtitle cues returned.",
        )

    expected_start = 1

    for cue_number, cue in enumerate(
        cues,
        start=1,
    ):

        start = int(
            cue[
                "start_word_index"
            ]
        )

        end = int(
            cue[
                "end_word_index"
            ]
        )

        if start != expected_start:

            return (
                False,
                "Cue coverage is not contiguous. "
                f"Cue {cue_number} starts at "
                f"{start}, expected "
                f"{expected_start}.",
            )

        if end < start:

            return (
                False,
                f"Cue {cue_number} has "
                f"end < start.",
            )

        size = (
            end
            - start
            + 1
        )

        if size > MAX_WORDS_PER_CUE:

            return (
                False,
                f"Cue {cue_number} contains "
                f"{size} words; maximum is "
                f"{MAX_WORDS_PER_CUE}.",
            )

        if (
            size < MIN_WORDS_PER_CUE
            and word_count > 1
        ):

            return (
                False,
                f"Cue {cue_number} contains "
                f"only {size} word. Prefer at "
                f"least {MIN_WORDS_PER_CUE}.",
            )

        expected_start = (
            end
            + 1
        )

    if expected_start != (
        word_count
        + 1
    ):

        return (
            False,
            "Cue coverage does not reach "
            f"the final word {word_count}.",
        )

    # The central paradox must be its own two-word cue.
    dont_try_ok = any(
        int(
            cue[
                "start_word_index"
            ]
        ) == 17
        and int(
            cue[
                "end_word_index"
            ]
        ) == 18
        for cue
        in cues
    )

    if not dont_try_ok:

        return (
            False,
            "\"Don't try.\" must be its own "
            "cue covering words 17-18.",
        )

    return (
        True,
        "",
    )


# ============================================================
# GEMINI CAPTION DIRECTOR
# ============================================================

def generate_groups(
    client: genai.Client,
    narration: str,
    spans: list[dict],
    voice_direction: dict,
) -> dict:

    correction = ""
    last_error = ""

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        try:

            response = (
                client.models.generate_content(
                    model=MODEL,

                    contents=
                        types.Part.from_text(
                            text=build_prompt(
                                narration,
                                spans,
                                voice_direction,
                                correction,
                            )
                        ),

                    config=
                        types.GenerateContentConfig(
                            system_instruction=
                                SYSTEM_INSTRUCTION,

                            temperature=
                                0.2,

                            response_mime_type=
                                "application/json",

                            response_schema=
                                RESPONSE_SCHEMA,

                            max_output_tokens=
                                4096,
                        ),
                )
            )

            if not response.text:

                raise RuntimeError(
                    "Caption Director returned "
                    "an empty response."
                )

            RAW_RESPONSE_FILE.write_text(
                response.text,
                encoding="utf-8",
            )

            result = json.loads(
                response.text
            )

            valid, error = validate_groups(
                result,
                len(
                    spans
                ),
            )

            if valid:
                return result

            correction = error
            last_error = error

        except Exception as exc:

            correction = str(
                exc
            )

            last_error = str(
                exc
            )

        if attempt < MAX_ATTEMPTS:

            time.sleep(
                RETRY_BASE_SECONDS
                * (
                    2
                    ** (
                        attempt
                        - 1
                    )
                )
            )

    raise RuntimeError(
        "Caption grouping failed after "
        f"{MAX_ATTEMPTS} attempts. "
        f"Last error: {last_error}"
    )


# ============================================================
# FINAL CUES
# ============================================================

def build_final_cues(
    groups: dict,
    narration: str,
    spans: list[dict],
) -> list[dict]:

    cues = groups[
        "cues"
    ]

    final = []

    for cue_index, cue in enumerate(
        cues,
        start=1,
    ):

        start_word_index = int(
            cue[
                "start_word_index"
            ]
        )

        end_word_index = int(
            cue[
                "end_word_index"
            ]
        )

        first_word = spans[
            start_word_index
            - 1
        ]

        last_word = spans[
            end_word_index
            - 1
        ]

        start_seconds = max(
            0.0,
            first_word[
                "start_seconds"
            ]
            - 0.04,
        )

        if cue_index < len(
            cues
        ):

            next_start_index = int(
                cues[
                    cue_index
                ][
                    "start_word_index"
                ]
            )

            next_word_start = spans[
                next_start_index
                - 1
            ][
                "start_seconds"
            ]

            end_seconds = min(
                last_word[
                    "end_seconds"
                ]
                + 0.28,

                next_word_start
                - 0.06,
            )

        else:

            end_seconds = (
                last_word[
                    "end_seconds"
                ]
                + 0.16
            )

        if end_seconds <= start_seconds:

            end_seconds = (
                last_word[
                    "end_seconds"
                ]
            )

        cue_words = []

        for word in spans[
            start_word_index
            - 1:
            end_word_index
        ]:

            cue_words.append(
                {
                    "index":
                        word[
                            "index"
                        ],

                    "word":
                        word[
                            "word"
                        ],

                    "start_seconds":
                        round(
                            word[
                                "start_seconds"
                            ],
                            3,
                        ),

                    "end_seconds":
                        round(
                            word[
                                "end_seconds"
                            ],
                            3,
                        ),
                }
            )

        text = display_text_for_range(
            narration,
            spans,
            start_word_index,
            end_word_index,
        )

        duration = (
            end_seconds
            - start_seconds
        )

        final.append(
            {
                "cue_id":
                    f"cue_{cue_index:03d}",

                "start_seconds":
                    round(
                        start_seconds,
                        3,
                    ),

                "end_seconds":
                    round(
                        end_seconds,
                        3,
                    ),

                "duration_seconds":
                    round(
                        duration,
                        3,
                    ),

                "start_word_index":
                    start_word_index,

                "end_word_index":
                    end_word_index,

                "word_count":
                    end_word_index
                    - start_word_index
                    + 1,

                "text":
                    text,

                "style_hint":
                    cue[
                        "style_hint"
                    ],

                "reason":
                    cue[
                        "reason"
                    ],

                "words":
                    cue_words,

                "duration_warning":
                    (
                        duration
                        > TARGET_MAX_CUE_SECONDS
                    ),
            }
        )

    return final


# ============================================================
# SRT + READABLE OUTPUT
# ============================================================

def write_srt(
    cues: list[dict],
) -> None:

    lines = []

    for number, cue in enumerate(
        cues,
        start=1,
    ):

        lines.append(
            str(
                number
            )
        )

        lines.append(
            f"{format_srt_time(cue['start_seconds'])}"
            " --> "
            f"{format_srt_time(cue['end_seconds'])}"
        )

        lines.append(
            cue[
                "text"
            ]
        )

        lines.append(
            ""
        )

    OUTPUT_SRT.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )


def write_readable(
    cues: list[dict],
) -> None:

    lines = []

    for cue in cues:

        warning = (
            "  ⚠ LONG"
            if cue[
                "duration_warning"
            ]
            else ""
        )

        lines.append(
            f"{cue['cue_id']}  "
            f"{cue['start_seconds']:.2f}-"
            f"{cue['end_seconds']:.2f}  "
            f"[{cue['style_hint']}]"
            f"{warning}"
        )

        lines.append(
            cue[
                "text"
            ]
        )

        lines.append(
            ""
        )

    OUTPUT_TEXT.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    for path in [
        WORD_TIMING_FILE,
        STORY_PLAN_FILE,
    ]:

        if not path.exists():

            raise FileNotFoundError(
                f"Missing required file: "
                f"{path}"
            )

    word_timing = load_json(
        WORD_TIMING_FILE
    )

    story_plan = load_json(
        STORY_PLAN_FILE
    )

    alignment_ratio = float(
        word_timing.get(
            "alignment_stats",
            {},
        ).get(
            "exact_match_ratio",
            0.0,
        )
    )

    if (
        alignment_ratio
        < MIN_ALIGNMENT_RATIO
    ):

        raise RuntimeError(
            "word_timing.json alignment is "
            "not accurate enough for automatic "
            "caption generation.\n"
            f"Exact match ratio: "
            f"{alignment_ratio:.3f}"
        )

    narration = word_timing[
        "locked_narration"
    ]

    timed_words = word_timing[
        "words"
    ]

    story_narration = story_plan[
        "narration_script"
    ].strip()

    if (
        re.sub(
            r"\s+",
            " ",
            narration,
        ).strip()
        != re.sub(
            r"\s+",
            " ",
            story_narration,
        ).strip()
    ):

        raise RuntimeError(
            "word_timing.json and "
            "final_story_plan.json do not "
            "contain the same locked narration."
        )

    spans = build_narration_word_spans(
        narration,
        timed_words,
    )

    STATUS_FILE.write_text(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n",
        encoding="utf-8",
    )

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=
            types.HttpOptions(
                api_version="v1"
            ),
    )

    groups = generate_groups(
        client,
        narration,
        spans,
        story_plan.get(
            "voice_direction",
            {},
        ),
    )

    cues = build_final_cues(
        groups,
        narration,
        spans,
    )

    long_cues = [
        cue[
            "cue_id"
        ]
        for cue
        in cues
        if cue[
            "duration_warning"
        ]
    ]

    output = {
        "status":
            "SUCCESS",

        "generated_at_utc":
            now_utc(),

        "source_word_timing":
            str(
                WORD_TIMING_FILE
            ),

        "source_story_plan":
            str(
                STORY_PLAN_FILE
            ),

        "caption_director_model":
            MODEL,

        "alignment_ratio":
            alignment_ratio,

        "cue_count":
            len(
                cues
            ),

        "settings": {
            "preferred_words_per_cue":
                (
                    f"{MIN_WORDS_PER_CUE}-"
                    f"{MAX_WORDS_PER_CUE}"
                ),

            "target_max_cue_seconds":
                TARGET_MAX_CUE_SECONDS,

            "render_strategy":
                (
                    "Use cue text as the subtitle phrase. "
                    "Use nested word timings later for "
                    "per-word emphasis/highlighting."
                ),
        },

        "long_cue_warnings":
            long_cues,

        "cues":
            cues,
    }

    save_json(
        OUTPUT_JSON,
        output,
    )

    write_srt(
        cues
    )

    write_readable(
        cues
    )

    STATUS_FILE.write_text(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Cues: {len(cues)}\n"
        f"Long cue warnings: "
        f"{len(long_cues)}\n"
        f"Output: {OUTPUT_JSON}\n",
        encoding="utf-8",
    )

    print()
    print(
        "Subtitle cue generation completed."
    )

    print(
        f"Cues: {len(cues)}"
    )

    print(
        f"JSON: {OUTPUT_JSON}"
    )

    print(
        f"SRT: {OUTPUT_SRT}"
    )

    print(
        f"Readable: {OUTPUT_TEXT}"
    )

    if long_cues:

        print(
            "Long cue warnings: "
            + ", ".join(
                long_cues
            )
        )

    print()


if __name__ == "__main__":

    try:
        main()

    except Exception:

        error = traceback.format_exc()

        STATUS_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        STATUS_FILE.write_text(
            "FAILED\n\n"
            + error,
            encoding="utf-8",
        )

        print()
        print(
            "Subtitle cue generation failed."
        )

        print(
            f"See: {STATUS_FILE}"
        )

        print()

        sys.exit(1)
