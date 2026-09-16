from __future__ import annotations

import json
import math
import re
import sys
import traceback
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

WORD_TIMING_FILE = Path("metadata/word_timing.json")
STORY_PLAN_FILE = Path("metadata/final_story_plan.json")

OUTPUT_JSON = Path("metadata/subtitle_cues.json")
OUTPUT_SRT = Path("metadata/subtitles.srt")
OUTPUT_TEXT = Path("metadata/subtitle_cues.txt")
STATUS_FILE = Path("metadata/subtitle_generation_status.txt")

MIN_ALIGNMENT_RATIO = 0.98

MIN_WORDS_PER_CUE = 2
MAX_WORDS_PER_CUE = 6

IDEAL_WORDS_PER_CUE = 4
IDEAL_DURATION_SECONDS = 1.65

# The approved narration is intentionally slow and reflective.
SOFT_MAX_DURATION_SECONDS = 3.0

MEDIUM_PAUSE_SECONDS = 0.32
STRONG_PAUSE_SECONDS = 0.55


# ============================================================
# PHRASE GUIDANCE
# ============================================================

# Internal breaks inside these phrases are strongly discouraged.
PROTECTED_PHRASES = [
    "Don't try",
    "accepting who he was",
    "The Backwards Law",
    "good feelings",
    "obsessing over the outcome",
    "Accept discomfort",
    "loses its power",
    "let yourself feel it",
]

IMPACT_PHRASE = "Don't try"

REFLECTION_PHRASE = (
    "What if you just let yourself feel it"
)


# ============================================================
# HELPERS
# ============================================================

WORD_RE = re.compile(
    r"\b[\w’'-]+\b",
    flags=re.UNICODE,
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


def normalize_word(text: str) -> str:
    return re.sub(
        r"[^\w]+",
        "",
        text
        .replace("’", "'")
        .replace("‘", "'")
        .lower(),
        flags=re.UNICODE,
    )


def format_srt_time(
    seconds: float,
) -> str:
    milliseconds = int(
        round(
            seconds * 1000
        )
    )

    hours = (
        milliseconds
        // 3_600_000
    )

    milliseconds %= 3_600_000

    minutes = (
        milliseconds
        // 60_000
    )

    milliseconds %= 60_000

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
# WORD SPANS
# ============================================================

def build_word_spans(
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
            f"Narration tokens: "
            f"{len(matches)}\n"
            f"Timed words: "
            f"{len(timed_words)}"
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
                "Narration and timing diverged "
                f"at word {index}: "
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


def interstitial_after_word(
    narration: str,
    spans: list[dict],
    word_index: int,
) -> str:
    current = spans[
        word_index - 1
    ]

    if word_index < len(
        spans
    ):
        next_start = spans[
            word_index
        ][
            "char_start"
        ]
    else:
        next_start = len(
            narration
        )

    return narration[
        current[
            "char_end"
        ]:
        next_start
    ]


def pause_after_word(
    spans: list[dict],
    word_index: int,
) -> float:
    if word_index >= len(
        spans
    ):
        return 0.0

    current = spans[
        word_index - 1
    ]

    next_word = spans[
        word_index
    ]

    return max(
        0.0,
        next_word[
            "start_seconds"
        ]
        - current[
            "end_seconds"
        ],
    )


# ============================================================
# QUOTE/PUNCTUATION OWNERSHIP
# ============================================================

def split_interstitial(
    raw: str,
) -> tuple[str, str]:
    """
    Split punctuation between adjacent spoken words into:

        suffix_for_previous_word,
        prefix_for_next_word

    Important cases:

        ": '"   -> (":", "'")   opening quotation
        ".'"    -> (".'", "")   closing quotation
        ",'"    -> (",'", "")   closing quotation
        " '"    -> ("", "'")    opening quotation
        ". "    -> (".", "")
        ", "    -> (",", "")

    This prevents a closing quote from leaking onto the next cue.
    """

    compact = "".join(
        character
        for character in raw
        if not character.isspace()
    )

    if not compact:
        return (
            "",
            "",
        )

    quote_chars = {
        "'",
        '"',
        "“",
        "”",
        "‘",
        "’",
    }

    quote_positions = [
        index
        for index, character
        in enumerate(
            compact
        )
        if character
        in quote_chars
    ]

    if not quote_positions:
        return (
            compact,
            "",
        )

    # We only expect one quote boundary in this narration, but
    # process the rightmost quote defensively.
    quote_index = (
        quote_positions[-1]
    )

    before_quote = compact[
        :quote_index
    ]

    quote = compact[
        quote_index
    ]

    after_quote = compact[
        quote_index + 1:
    ]

    # If anything follows the quote, treat the quote as part of
    # the previous phrase.
    if after_quote:
        return (
            compact,
            "",
        )

    # Quote after . , ! ? ; is a closing quote.
    if (
        before_quote
        and before_quote[-1]
        in ".,!?;"
    ):
        return (
            compact,
            "",
        )

    # Quote after ":" introduces the next quoted phrase.
    if (
        before_quote
        and before_quote[-1]
        == ":"
    ):
        return (
            before_quote,
            quote,
        )

    # Bare quote between words is treated as opening.
    if not before_quote:
        return (
            "",
            quote,
        )

    # Conservative fallback: punctuation belongs to previous
    # phrase, quote opens the next phrase.
    return (
        before_quote,
        quote,
    )


def text_for_range(
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

    prefix = ""

    if start_index > 1:
        before = interstitial_after_word(
            narration,
            spans,
            start_index - 1,
        )

        _previous_suffix, prefix = (
            split_interstitial(
                before
            )
        )

    after = interstitial_after_word(
        narration,
        spans,
        end_index,
    )

    suffix, _next_prefix = (
        split_interstitial(
            after
        )
    )

    core = narration[
        start[
            "char_start"
        ]:
        end[
            "char_end"
        ]
    ]

    text = (
        prefix
        + core
        + suffix
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


# ============================================================
# PHRASE RANGES
# ============================================================

def find_phrase_range(
    spans: list[dict],
    phrase: str,
) -> tuple[int, int] | None:
    phrase_words = [
        normalize_word(
            item
        )
        for item
        in WORD_RE.findall(
            phrase
        )
    ]

    words = [
        normalize_word(
            item[
                "word"
            ]
        )
        for item
        in spans
    ]

    if not phrase_words:
        return None

    size = len(
        phrase_words
    )

    for start in range(
        0,
        len(
            words
        )
        - size
        + 1,
    ):
        if (
            words[
                start:
                start + size
            ]
            == phrase_words
        ):
            return (
                start + 1,
                start + size,
            )

    return None


# ============================================================
# MANDATORY BOUNDARIES
# ============================================================

def sentence_boundary_indices(
    narration: str,
    spans: list[dict],
) -> set[int]:
    boundaries: set[int] = set()

    for index in range(
        1,
        len(
            spans
        )
        + 1,
    ):
        raw = interstitial_after_word(
            narration,
            spans,
            index,
        )

        previous_suffix, _ = (
            split_interstitial(
                raw
            )
        )

        if re.search(
            r"[.!?][\"'”’]?$",
            previous_suffix,
        ):
            boundaries.add(
                index
            )

    boundaries.add(
        len(
            spans
        )
    )

    return boundaries


def build_mandatory_boundaries(
    narration: str,
    spans: list[dict],
) -> set[int]:
    mandatory = sentence_boundary_indices(
        narration,
        spans,
    )

    impact = find_phrase_range(
        spans,
        IMPACT_PHRASE,
    )

    if impact is not None:
        start, end = impact

        if start > 1:
            mandatory.add(
                start - 1
            )

        mandatory.add(
            end
        )

    return mandatory


# ============================================================
# PROTECTED BOUNDARIES
# ============================================================

def protected_internal_boundaries(
    spans: list[dict],
) -> set[int]:
    protected: set[int] = set()

    for phrase in PROTECTED_PHRASES:
        found = find_phrase_range(
            spans,
            phrase,
        )

        if found is None:
            continue

        start, end = found

        for boundary in range(
            start,
            end,
        ):
            protected.add(
                boundary
            )

    return protected


# ============================================================
# SEGMENT COST
# ============================================================

def boundary_reward(
    narration: str,
    spans: list[dict],
    end_index: int,
) -> float:
    reward = 0.0

    raw = interstitial_after_word(
        narration,
        spans,
        end_index,
    )

    previous_suffix, _ = (
        split_interstitial(
            raw
        )
    )

    pause = pause_after_word(
        spans,
        end_index,
    )

    if re.search(
        r"[.!?][\"'”’]?$",
        previous_suffix,
    ):
        reward += 6.0

    elif ":" in previous_suffix:
        reward += 3.0

    elif "," in previous_suffix:
        reward += 2.0

    if (
        pause
        >= STRONG_PAUSE_SECONDS
    ):
        reward += 3.0

    elif (
        pause
        >= MEDIUM_PAUSE_SECONDS
    ):
        reward += 1.5

    return reward


def semantic_boundary_penalty(
    spans: list[dict],
    end_index: int,
) -> float:
    """
    Small deterministic penalties for endings that usually feel
    unfinished in captions.
    """

    word = normalize_word(
        spans[
            end_index - 1
        ][
            "word"
        ]
    )

    dangling_words = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "from",
        "of",
        "to",
        "when",
        "we",
        "you",
        "his",
        "her",
        "their",
    }

    if word in dangling_words:
        return 2.5

    return 0.0


def segment_cost(
    narration: str,
    spans: list[dict],
    start_index: int,
    end_index: int,
    protected_boundaries: set[int],
) -> float:
    word_count = (
        end_index
        - start_index
        + 1
    )

    start_time = spans[
        start_index - 1
    ][
        "start_seconds"
    ]

    end_time = spans[
        end_index - 1
    ][
        "end_seconds"
    ]

    duration = max(
        0.01,
        end_time
        - start_time,
    )

    word_penalty = (
        abs(
            word_count
            - IDEAL_WORDS_PER_CUE
        )
        * 0.8
    )

    duration_penalty = (
        abs(
            duration
            - IDEAL_DURATION_SECONDS
        )
        * 0.9
    )

    if (
        duration
        > SOFT_MAX_DURATION_SECONDS
    ):
        duration_penalty += (
            duration
            - SOFT_MAX_DURATION_SECONDS
        ) * 3.0

    protected_penalty = (
        50.0
        if end_index
        in protected_boundaries
        else 0.0
    )

    return (
        word_penalty
        + duration_penalty
        + protected_penalty
        + semantic_boundary_penalty(
            spans,
            end_index,
        )
        - boundary_reward(
            narration,
            spans,
            end_index,
        )
    )


# ============================================================
# BLOCK SEGMENTATION
# ============================================================

def segment_block(
    narration: str,
    spans: list[dict],
    block_start: int,
    block_end: int,
    protected_boundaries: set[int],
) -> list[tuple[int, int]]:
    length = (
        block_end
        - block_start
        + 1
    )

    if (
        MIN_WORDS_PER_CUE
        <= length
        <= MAX_WORDS_PER_CUE
    ):
        return [
            (
                block_start,
                block_end,
            )
        ]

    dp: dict[
        int,
        tuple[
            float,
            int | None,
        ],
    ] = {
        block_start - 1: (
            0.0,
            None,
        )
    }

    for end_index in range(
        block_start,
        block_end + 1,
    ):
        best = None

        for size in range(
            MIN_WORDS_PER_CUE,
            MAX_WORDS_PER_CUE + 1,
        ):
            start_index = (
                end_index
                - size
                + 1
            )

            previous = (
                start_index
                - 1
            )

            if (
                start_index
                < block_start
            ):
                continue

            if previous not in dp:
                continue

            cost = (
                dp[
                    previous
                ][0]
                + segment_cost(
                    narration,
                    spans,
                    start_index,
                    end_index,
                    protected_boundaries,
                )
            )

            if (
                best is None
                or cost
                < best[0]
            ):
                best = (
                    cost,
                    previous,
                )

        if best is not None:
            dp[
                end_index
            ] = best

    if block_end not in dp:
        raise RuntimeError(
            "Unable to segment subtitle "
            f"block {block_start}-"
            f"{block_end}."
        )

    reversed_ranges = []

    current = block_end

    while (
        current
        >= block_start
    ):
        _, previous = dp[
            current
        ]

        if previous is None:
            raise RuntimeError(
                "Subtitle segmentation "
                "backtracking failed."
            )

        start_index = (
            previous
            + 1
        )

        reversed_ranges.append(
            (
                start_index,
                current,
            )
        )

        current = previous

    return list(
        reversed(
            reversed_ranges
        )
    )


# ============================================================
# FULL SEGMENTATION
# ============================================================

def build_ranges(
    narration: str,
    spans: list[dict],
) -> list[tuple[int, int]]:
    mandatory = sorted(
        build_mandatory_boundaries(
            narration,
            spans,
        )
    )

    protected = (
        protected_internal_boundaries(
            spans
        )
    )

    ranges: list[
        tuple[int, int]
    ] = []

    block_start = 1

    for block_end in mandatory:
        if (
            block_end
            < block_start
        ):
            continue

        ranges.extend(
            segment_block(
                narration,
                spans,
                block_start,
                block_end,
                protected,
            )
        )

        block_start = (
            block_end
            + 1
        )

    if (
        block_start
        <= len(
            spans
        )
    ):
        ranges.extend(
            segment_block(
                narration,
                spans,
                block_start,
                len(
                    spans
                ),
                protected,
            )
        )

    expected = 1

    for start, end in ranges:
        if start != expected:
            raise RuntimeError(
                "Subtitle coverage is not "
                "contiguous. "
                f"Expected {expected}, "
                f"got {start}."
            )

        expected = (
            end
            + 1
        )

    if expected != (
        len(
            spans
        )
        + 1
    ):
        raise RuntimeError(
            "Subtitle coverage does not "
            "reach final narration word."
        )

    impact = find_phrase_range(
        spans,
        IMPACT_PHRASE,
    )

    if (
        impact is not None
        and impact
        not in ranges
    ):
        raise RuntimeError(
            "\"Don't try.\" was not "
            "isolated as its own cue."
        )

    return ranges


# ============================================================
# STYLE ASSIGNMENT
# ============================================================

def determine_style(
    cue_range: tuple[int, int],
    spans: list[dict],
    story_plan: dict,
) -> str:
    impact = find_phrase_range(
        spans,
        IMPACT_PHRASE,
    )

    if (
        impact is not None
        and cue_range
        == impact
    ):
        return "IMPACT"

    reflection = find_phrase_range(
        spans,
        REFLECTION_PHRASE,
    )

    if (
        reflection is not None
        and cue_range[0]
        >= reflection[0]
    ):
        return "REFLECTION"

    emphasis_ranges = []

    for phrase in story_plan.get(
        "voice_direction",
        {},
    ).get(
        "emphasis_phrases",
        [],
    ):
        found = find_phrase_range(
            spans,
            phrase,
        )

        if found is not None:
            emphasis_ranges.append(
                found
            )

    cue_start, cue_end = (
        cue_range
    )

    for start, end in emphasis_ranges:
        overlap = max(
            0,
            min(
                cue_end,
                end,
            )
            - max(
                cue_start,
                start,
            )
            + 1,
        )

        phrase_size = (
            end
            - start
            + 1
        )

        if overlap >= max(
            2,
            math.ceil(
                phrase_size
                * 0.6
            ),
        ):
            return "EMPHASIS"

    return "NORMAL"


# ============================================================
# FINAL CUES
# ============================================================

def build_cues(
    narration: str,
    spans: list[dict],
    ranges: list[tuple[int, int]],
    story_plan: dict,
) -> list[dict]:
    cues = []

    for cue_number, (
        start_index,
        end_index,
    ) in enumerate(
        ranges,
        start=1,
    ):
        first_word = spans[
            start_index - 1
        ]

        last_word = spans[
            end_index - 1
        ]

        start_seconds = max(
            0.0,
            first_word[
                "start_seconds"
            ]
            - 0.04,
        )

        if (
            cue_number
            < len(
                ranges
            )
        ):
            next_start_index = ranges[
                cue_number
            ][0]

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
                + 0.25,

                next_word_start
                - 0.05,
            )

        else:
            end_seconds = (
                last_word[
                    "end_seconds"
                ]
                + 0.16
            )

        if (
            end_seconds
            <= start_seconds
        ):
            end_seconds = (
                last_word[
                    "end_seconds"
                ]
            )

        text = text_for_range(
            narration,
            spans,
            start_index,
            end_index,
        )

        cue_range = (
            start_index,
            end_index,
        )

        style = determine_style(
            cue_range,
            spans,
            story_plan,
        )

        nested_words = []

        for item in spans[
            start_index - 1:
            end_index
        ]:
            nested_words.append(
                {
                    "index":
                        item[
                            "index"
                        ],

                    "word":
                        item[
                            "word"
                        ],

                    "start_seconds":
                        round(
                            item[
                                "start_seconds"
                            ],
                            3,
                        ),

                    "end_seconds":
                        round(
                            item[
                                "end_seconds"
                            ],
                            3,
                        ),
                }
            )

        duration = (
            end_seconds
            - start_seconds
        )

        cues.append(
            {
                "cue_id":
                    f"cue_"
                    f"{cue_number:03d}",

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
                    start_index,

                "end_word_index":
                    end_index,

                "word_count":
                    end_index
                    - start_index
                    + 1,

                "text":
                    text,

                "style_hint":
                    style,

                "duration_warning":
                    (
                        duration
                        > SOFT_MAX_DURATION_SECONDS
                    ),

                "words":
                    nested_words,
            }
        )

    return cues


# ============================================================
# TEXT QA
# ============================================================

def validate_rendered_text(
    cues: list[dict],
) -> None:
    for cue in cues:
        text = cue[
            "text"
        ]

        if text.startswith(
            ("' ", '" ')
        ):
            raise RuntimeError(
                "Subtitle text contains a "
                "stray leading quote: "
                f"{cue['cue_id']} "
                f"{text!r}"
            )

    impact = next(
        (
            cue
            for cue in cues
            if cue[
                "style_hint"
            ]
            == "IMPACT"
        ),
        None,
    )

    if impact is None:
        raise RuntimeError(
            "No IMPACT subtitle cue "
            "was generated."
        )

    if normalize_word(
        " ".join(
            word[
                "word"
            ]
            for word
            in impact[
                "words"
            ]
        )
    ) != normalize_word(
        "Don't try"
    ):
        raise RuntimeError(
            "The IMPACT cue does not "
            "contain exactly 'Don't try'."
        )


# ============================================================
# WRITERS
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
            "  [LONG]"
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
    for required in [
        WORD_TIMING_FILE,
        STORY_PLAN_FILE,
    ]:
        if not required.exists():
            raise FileNotFoundError(
                f"Missing required file: "
                f"{required}"
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
            "word_timing.json alignment "
            "is not accurate enough.\n"
            f"Exact match ratio: "
            f"{alignment_ratio:.3f}"
        )

    narration = word_timing[
        "locked_narration"
    ].strip()

    story_narration = story_plan[
        "narration_script"
    ].strip()

    if re.sub(
        r"\s+",
        " ",
        narration,
    ) != re.sub(
        r"\s+",
        " ",
        story_narration,
    ):
        raise RuntimeError(
            "word_timing.json and "
            "final_story_plan.json "
            "contain different narration."
        )

    spans = build_word_spans(
        narration,
        word_timing[
            "words"
        ],
    )

    STATUS_FILE.write_text(
        "RUNNING\n",
        encoding="utf-8",
    )

    ranges = build_ranges(
        narration,
        spans,
    )

    cues = build_cues(
        narration,
        spans,
        ranges,
        story_plan,
    )

    validate_rendered_text(
        cues
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

        "source_word_timing":
            str(
                WORD_TIMING_FILE
            ),

        "source_story_plan":
            str(
                STORY_PLAN_FILE
            ),

        "strategy":
            (
                "Deterministic dynamic-programming "
                "subtitle segmentation using exact "
                "word timestamps, punctuation, pauses, "
                "protected phrases, semantic ending "
                "penalties, and cue-length constraints."
            ),

        "alignment_ratio":
            alignment_ratio,

        "cue_count":
            len(
                cues
            ),

        "settings": {
            "min_words_per_cue":
                MIN_WORDS_PER_CUE,

            "max_words_per_cue":
                MAX_WORDS_PER_CUE,

            "ideal_words_per_cue":
                IDEAL_WORDS_PER_CUE,

            "ideal_duration_seconds":
                IDEAL_DURATION_SECONDS,

            "soft_max_duration_seconds":
                SOFT_MAX_DURATION_SECONDS,
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
        f"Cues: {len(cues)}\n"
        f"Long cue warnings: "
        f"{len(long_cues)}\n"
        f"Output: "
        f"{OUTPUT_JSON}\n",
        encoding="utf-8",
    )

    print()
    print(
        "Subtitle cue generation completed."
    )

    print(
        f"Cues: "
        f"{len(cues)}"
    )

    print(
        f"JSON: "
        f"{OUTPUT_JSON}"
    )

    print(
        f"SRT: "
        f"{OUTPUT_SRT}"
    )

    print(
        f"Readable: "
        f"{OUTPUT_TEXT}"
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
        STATUS_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        error = traceback.format_exc()

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
            f"See: "
            f"{STATUS_FILE}"
        )

        print()

        sys.exit(1)
