from __future__ import annotations

import base64
import json
import re
import sys
import traceback
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ID = "gen-lang-client-0240752803"

# Chirp 3 is available in the "us" multi-region.
REGION = "us"
MODEL = "chirp_3"
LANGUAGE_CODE = "en-US"

AUDIO_FILE = Path("audio/narration.wav")
STORY_PLAN_FILE = Path("metadata/final_story_plan.json")

OUTPUT_FILE = Path("metadata/word_timing.json")
RAW_RESPONSE_FILE = Path("metadata/word_timing_raw_response.json")
STATUS_FILE = Path("metadata/word_timing_status.txt")

# We expect very high agreement because the audio was generated
# directly from the locked narration.
MIN_EXACT_MATCH_RATIO = 0.85


# ============================================================
# HELPERS
# ============================================================

WORD_RE = re.compile(r"\b[\w’'-]+\b", flags=re.UNICODE)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    with temp.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )

    temp.replace(path)


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text)


def normalize_word(word: str) -> str:
    word = (
        word
        .replace("’", "'")
        .replace("‘", "'")
        .lower()
    )

    return re.sub(
        r"(^[^\w]+|[^\w]+$)",
        "",
        word,
        flags=re.UNICODE,
    )


def parse_offset(value: str | None) -> float:
    if not value:
        return 0.0

    value = value.strip()

    if value.endswith("s"):
        value = value[:-1]

    return float(value or 0.0)


# ============================================================
# SPEECH-TO-TEXT
# ============================================================

def transcribe_with_word_offsets() -> dict:
    audio_bytes = AUDIO_FILE.read_bytes()

    credentials, _ = google.auth.default(
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
        ]
    )

    session = AuthorizedSession(credentials)

    endpoint = (
        f"https://{REGION}-speech.googleapis.com/v2/"
        f"projects/{PROJECT_ID}/locations/{REGION}/"
        f"recognizers/_:recognize"
    )

    request_body = {
        "config": {
            "autoDecodingConfig": {},
            "languageCodes": [
                LANGUAGE_CODE,
            ],
            "model": MODEL,
            "features": {
                "enableWordTimeOffsets": True,
                "enableAutomaticPunctuation": True,
            },
        },
        "content": base64.b64encode(
            audio_bytes
        ).decode("ascii"),
    }

    response = session.post(
        endpoint,
        json=request_body,
        timeout=120,
    )

    if not response.ok:
        raise RuntimeError(
            "Speech-to-Text request failed.\n"
            f"HTTP {response.status_code}\n"
            f"{response.text}"
        )

    return response.json()


# ============================================================
# RESPONSE EXTRACTION
# ============================================================

def extract_recognition(
    response: dict,
) -> tuple[str, list[dict]]:

    transcript_parts: list[str] = []
    words: list[dict] = []

    for result in response.get(
        "results",
        [],
    ):

        alternatives = result.get(
            "alternatives",
            [],
        )

        if not alternatives:
            continue

        alternative = alternatives[0]

        transcript = alternative.get(
            "transcript",
            "",
        ).strip()

        if transcript:
            transcript_parts.append(
                transcript
            )

        for item in alternative.get(
            "words",
            [],
        ):

            word = item.get(
                "word",
                "",
            ).strip()

            if not word:
                continue

            words.append(
                {
                    "word": word,
                    "start_seconds": parse_offset(
                        item.get(
                            "startOffset"
                        )
                    ),
                    "end_seconds": parse_offset(
                        item.get(
                            "endOffset"
                        )
                    ),
                }
            )

    return (
        " ".join(
            transcript_parts
        ).strip(),
        words,
    )


# ============================================================
# SEQUENCE ALIGNMENT
# ============================================================

def align_words(
    canonical_words: list[str],
    recognized_words: list[dict],
) -> tuple[list[dict], dict]:
    """
    Global sequence alignment between the locked narration and
    Speech-to-Text words.

    Exact matches cost 0.
    Substitutions, insertions and deletions cost 1.

    A substituted recognized word can still provide timing for the
    canonical word. Deleted canonical words are interpolated later.
    """

    canon = [
        normalize_word(word)
        for word in canonical_words
    ]

    recog = [
        normalize_word(
            item["word"]
        )
        for item in recognized_words
    ]

    n = len(canon)
    m = len(recog)

    dp = [
        [0] * (m + 1)
        for _ in range(n + 1)
    ]

    back = [
        [None] * (m + 1)
        for _ in range(n + 1)
    ]

    for i in range(1, n + 1):
        dp[i][0] = i
        back[i][0] = "delete"

    for j in range(1, m + 1):
        dp[0][j] = j
        back[0][j] = "insert"

    for i in range(1, n + 1):
        for j in range(1, m + 1):

            exact = (
                canon[i - 1]
                == recog[j - 1]
            )

            substitution_cost = (
                0 if exact else 1
            )

            diagonal = (
                dp[i - 1][j - 1]
                + substitution_cost
            )

            delete = (
                dp[i - 1][j]
                + 1
            )

            insert = (
                dp[i][j - 1]
                + 1
            )

            best = min(
                diagonal,
                delete,
                insert,
            )

            dp[i][j] = best

            # Prefer diagonal alignment when tied because it preserves
            # timing for a canonical word.
            if diagonal == best:
                back[i][j] = (
                    "exact"
                    if exact
                    else "substitute"
                )
            elif delete == best:
                back[i][j] = "delete"
            else:
                back[i][j] = "insert"

    aligned_reversed: list[dict] = []

    i = n
    j = m

    exact_matches = 0
    substitutions = 0
    deletions = 0
    insertions = 0

    while i > 0 or j > 0:

        op = back[i][j]

        if op in {
            "exact",
            "substitute",
        }:

            rec = recognized_words[
                j - 1
            ]

            if op == "exact":
                exact_matches += 1
            else:
                substitutions += 1

            aligned_reversed.append(
                {
                    "canonical_word":
                        canonical_words[
                            i - 1
                        ],

                    "recognized_word":
                        rec[
                            "word"
                        ],

                    "start_seconds":
                        rec[
                            "start_seconds"
                        ],

                    "end_seconds":
                        rec[
                            "end_seconds"
                        ],

                    "alignment":
                        op,
                }
            )

            i -= 1
            j -= 1

        elif op == "delete":

            deletions += 1

            aligned_reversed.append(
                {
                    "canonical_word":
                        canonical_words[
                            i - 1
                        ],

                    "recognized_word":
                        None,

                    "start_seconds":
                        None,

                    "end_seconds":
                        None,

                    "alignment":
                        "missing",
                }
            )

            i -= 1

        elif op == "insert":

            insertions += 1
            j -= 1

        else:
            raise RuntimeError(
                "Unexpected alignment state "
                f"at i={i}, j={j}."
            )

    aligned = list(
        reversed(
            aligned_reversed
        )
    )

    # Interpolate timing for canonical words that STT omitted.
    index = 0

    while index < len(aligned):

        if aligned[index][
            "start_seconds"
        ] is not None:
            index += 1
            continue

        run_start = index

        while (
            index < len(aligned)
            and aligned[index][
                "start_seconds"
            ] is None
        ):
            index += 1

        run_end = index - 1
        count = (
            run_end
            - run_start
            + 1
        )

        previous_end = (
            aligned[
                run_start - 1
            ][
                "end_seconds"
            ]
            if run_start > 0
            else 0.0
        )

        next_start = (
            aligned[
                index
            ][
                "start_seconds"
            ]
            if index < len(aligned)
            else previous_end
            + 0.35 * count
        )

        gap = max(
            next_start - previous_end,
            0.08 * count,
        )

        step = gap / count

        for offset in range(count):

            item = aligned[
                run_start
                + offset
            ]

            item[
                "start_seconds"
            ] = round(
                previous_end
                + step * offset,
                3,
            )

            item[
                "end_seconds"
            ] = round(
                previous_end
                + step * (
                    offset + 1
                ),
                3,
            )

            item[
                "alignment"
            ] = (
                "interpolated"
            )

    for idx, item in enumerate(
        aligned,
        start=1,
    ):
        item["index"] = idx

        item[
            "start_seconds"
        ] = round(
            float(
                item[
                    "start_seconds"
                ]
            ),
            3,
        )

        item[
            "end_seconds"
        ] = round(
            float(
                item[
                    "end_seconds"
                ]
            ),
            3,
        )

    stats = {
        "canonical_word_count":
            n,

        "recognized_word_count":
            m,

        "exact_matches":
            exact_matches,

        "substitutions":
            substitutions,

        "canonical_words_missing":
            deletions,

        "extra_recognized_words":
            insertions,

        "exact_match_ratio":
            round(
                exact_matches
                / n,
                4,
            )
            if n
            else 0.0,

        "edit_distance":
            dp[n][m],
    }

    return (
        aligned,
        stats,
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    if not AUDIO_FILE.exists():
        raise FileNotFoundError(
            f"Missing audio: "
            f"{AUDIO_FILE}"
        )

    if not STORY_PLAN_FILE.exists():
        raise FileNotFoundError(
            f"Missing story plan: "
            f"{STORY_PLAN_FILE}"
        )

    STATUS_FILE.write_text(
        "RUNNING\n"
        f"Audio: {AUDIO_FILE}\n"
        f"Model: {MODEL}\n",
        encoding="utf-8",
    )

    story_plan = load_json(
        STORY_PLAN_FILE
    )

    narration = story_plan.get(
        "narration_script",
        "",
    ).strip()

    if not narration:
        raise RuntimeError(
            "final_story_plan.json "
            "has no narration_script."
        )

    canonical_words = tokenize(
        narration
    )

    print()
    print(
        "Extracting real word timestamps..."
    )
    print(
        f"Speech model: {MODEL}"
    )
    print()

    raw_response = (
        transcribe_with_word_offsets()
    )

    save_json(
        RAW_RESPONSE_FILE,
        raw_response,
    )

    (
        recognized_transcript,
        recognized_words,
    ) = extract_recognition(
        raw_response
    )

    if not recognized_words:
        raise RuntimeError(
            "Speech-to-Text returned "
            "no word timestamps."
        )

    aligned_words, stats = align_words(
        canonical_words,
        recognized_words,
    )

    if (
        stats[
            "exact_match_ratio"
        ]
        < MIN_EXACT_MATCH_RATIO
    ):
        raise RuntimeError(
            "ASR alignment quality is too low "
            "to trust automatically.\n"
            f"Exact match ratio: "
            f"{stats['exact_match_ratio']:.3f}\n"
            f"Recognized transcript: "
            f"{recognized_transcript}"
        )

    output = {
        "status": "SUCCESS",
        "source_audio": str(
            AUDIO_FILE
        ),
        "source_story_plan": str(
            STORY_PLAN_FILE
        ),
        "speech_to_text": {
            "api": (
                "Google Cloud "
                "Speech-to-Text V2"
            ),
            "model": MODEL,
            "region": REGION,
            "language_code":
                LANGUAGE_CODE,
        },
        "locked_narration":
            narration,
        "recognized_transcript":
            recognized_transcript,
        "alignment_stats":
            stats,
        "words":
            aligned_words,
    }

    save_json(
        OUTPUT_FILE,
        output,
    )

    STATUS_FILE.write_text(
        "SUCCESS\n"
        f"Output: {OUTPUT_FILE}\n"
        f"Canonical words: "
        f"{stats['canonical_word_count']}\n"
        f"Recognized words: "
        f"{stats['recognized_word_count']}\n"
        f"Exact match ratio: "
        f"{stats['exact_match_ratio']:.3f}\n",
        encoding="utf-8",
    )

    print(
        "Word timing completed."
    )
    print(
        f"Output: {OUTPUT_FILE}"
    )
    print(
        "Exact match ratio: "
        f"{stats['exact_match_ratio']:.3f}"
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
            "Word timing failed."
        )
        print(
            f"See: {STATUS_FILE}"
        )
        print()

        sys.exit(1)
