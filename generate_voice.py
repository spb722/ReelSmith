from __future__ import annotations

import json
import re
import sys
import traceback
import wave
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ID = "gen-lang-client-0240752803"
LOCATION = "global"

# Google's current Gemini TTS model for controllable narration.
MODEL = "gemini-3.1-flash-tts-preview"

# "Gacrux" is described by Google as a mature voice.
VOICE_NAME = "Gacrux"

# Neutral English baseline. Accent/style is further directed in prompt.
LANGUAGE_CODE = "en-US"

STORY_PLAN_FILE = Path("metadata/final_story_plan.json")

AUDIO_DIR = Path("audio")
OUTPUT_WAV = AUDIO_DIR / "narration.wav"

METADATA_DIR = Path("metadata")
VOICE_METADATA_FILE = METADATA_DIR / "voice_generation.json"
VOICE_PROMPT_FILE = METADATA_DIR / "voice_prompt.txt"
STATUS_FILE = METADATA_DIR / "voice_generation_status.txt"

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM


# ============================================================
# HELPERS
# ============================================================

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def count_words(text: str) -> int:
    return len(
        re.findall(
            r"\b[\w’'-]+\b",
            text,
            flags=re.UNICODE,
        )
    )


def write_wave(
    filename: Path,
    pcm: bytes,
    *,
    channels: int = CHANNELS,
    rate: int = SAMPLE_RATE,
    sample_width: int = SAMPLE_WIDTH,
) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(filename), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def get_wave_duration_seconds(filename: Path) -> float:
    with wave.open(str(filename), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()

    return round(frames / float(rate), 3)


# ============================================================
# AUDIO-DIRECTION PROMPT
# ============================================================

def apply_pause_tags(
    narration: str,
    pause_after_phrases: list[str],
) -> str:
    """
    Add Gemini 3.1 TTS audio tags after selected phrases.

    These square-bracket tags are delivery controls, not spoken text.
    We preserve the narration wording itself.
    """

    tagged = narration

    for raw_phrase in pause_after_phrases:
        phrase = raw_phrase.strip()

        if not phrase:
            continue

        # The final reflective question should not get an extra pause
        # after it because the audio naturally ends there.
        if phrase.endswith("?"):
            continue

        if phrase not in tagged:
            continue

        # Give the central paradox a longer beat.
        if "Don't try" in phrase or "Don’t try" in phrase:
            tag = " [long pause]"
        else:
            tag = " [short pause]"

        tagged = tagged.replace(
            phrase,
            phrase + tag,
            1,
        )

    return tagged


def build_tts_prompt(plan: dict) -> str:
    narration = plan["narration_script"].strip()

    voice_direction = plan.get(
        "voice_direction",
        {},
    )

    persona = voice_direction.get(
        "persona",
        "Mature, thoughtful guide",
    )

    tone = voice_direction.get(
        "tone",
        [],
    )

    pace_wpm = voice_direction.get(
        "pace_wpm",
        plan.get(
            "calculated_effective_wpm",
            133,
        ),
    )

    emphasis_phrases = voice_direction.get(
        "emphasis_phrases",
        [],
    )

    pause_after_phrases = voice_direction.get(
        "pause_after_phrases",
        [],
    )

    tagged_narration = apply_pause_tags(
        narration,
        pause_after_phrases,
    )

    # A light opening tag helps establish the performance without
    # over-directing every sentence.
    tagged_narration = (
        "[slow] "
        + tagged_narration
    )

    tone_text = ", ".join(tone)

    emphasis_text = "; ".join(
        emphasis_phrases
    )

    return f"""
Read the narration below exactly as written.
Do not add, omit, paraphrase, explain, or repeat any spoken words.
Square-bracketed audio tags are delivery instructions and must not be spoken.

PERFORMANCE

Voice character: {persona}.
Tone: {tone_text}.
Delivery: mature, grounded, intimate, calm, confident, reflective, and slightly intense.
Target pace: approximately {pace_wpm} words per minute.
Use a neutral international English delivery.
Avoid a commercial, trailer, motivational-speaker, or overly soothing sound.
Keep the performance restrained and natural.
Let important ideas land without melodrama.

The phrase "Don't try." is the central paradox.
Deliver it quietly and confidently, then allow the long pause after it.
Do not shout it or make it theatrical.

Give subtle vocal emphasis to:
{emphasis_text}

The final question should sound genuinely curious and reflective,
not like a call-to-action.

NARRATION

{tagged_narration}
""".strip()


# ============================================================
# RESPONSE EXTRACTION
# ============================================================

def extract_pcm(response) -> bytes:
    if not getattr(response, "candidates", None):
        raise RuntimeError(
            "Gemini TTS returned no candidates."
        )

    candidate = response.candidates[0]

    if (
        candidate.content is None
        or not candidate.content.parts
    ):
        raise RuntimeError(
            "Gemini TTS returned no content parts."
        )

    for part in candidate.content.parts:
        inline_data = getattr(
            part,
            "inline_data",
            None,
        )

        if (
            inline_data is not None
            and getattr(
                inline_data,
                "data",
                None,
            )
        ):
            data = inline_data.data

            if isinstance(data, bytes):
                return data

            # Defensive fallback if an SDK version returns bytearray.
            if isinstance(data, bytearray):
                return bytes(data)

    raise RuntimeError(
        "Gemini TTS response contained no audio data."
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    AUDIO_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    METADATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not STORY_PLAN_FILE.exists():
        raise FileNotFoundError(
            f"Missing locked story plan: "
            f"{STORY_PLAN_FILE}"
        )

    plan = load_json(
        STORY_PLAN_FILE
    )

    quality_loop = plan.get(
        "quality_loop",
        {},
    )

    if quality_loop.get(
        "status"
    ) != "APPROVED":
        raise RuntimeError(
            "final_story_plan.json is not marked "
            "APPROVED by the story quality loop."
        )

    narration = plan.get(
        "narration_script",
        "",
    ).strip()

    if not narration:
        raise RuntimeError(
            "The final story plan has no narration_script."
        )

    prompt = build_tts_prompt(
        plan
    )

    VOICE_PROMPT_FILE.write_text(
        prompt,
        encoding="utf-8",
    )

    STATUS_FILE.write_text(
        "RUNNING\n"
        f"Started: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Voice: {VOICE_NAME}\n",
        encoding="utf-8",
    )

    print()
    print("Generating narration...")
    print(f"Model: {MODEL}")
    print(f"Voice: {VOICE_NAME}")
    print()

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            speech_config=types.SpeechConfig(
                language_code=LANGUAGE_CODE,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=
                        types.PrebuiltVoiceConfig(
                            voice_name=VOICE_NAME,
                        )
                ),
            ),
        ),
    )

    pcm = extract_pcm(
        response
    )

    write_wave(
        OUTPUT_WAV,
        pcm,
    )

    duration_seconds = (
        get_wave_duration_seconds(
            OUTPUT_WAV
        )
    )

    word_count = count_words(
        narration
    )

    actual_effective_wpm = round(
        word_count
        / duration_seconds
        * 60.0,
        1,
    )

    planned_duration = plan.get(
        "calculated_scene_duration_seconds"
    )

    planned_wpm = plan.get(
        "voice_direction",
        {},
    ).get(
        "pace_wpm"
    )

    metadata = {
        "status": "SUCCESS",
        "generated_at_utc": now_utc(),
        "source_story_plan": str(
            STORY_PLAN_FILE
        ),
        "model": MODEL,
        "voice_name": VOICE_NAME,
        "language_code": LANGUAGE_CODE,
        "output_file": str(
            OUTPUT_WAV
        ),
        "audio_format": {
            "container": "WAV",
            "codec": "PCM_S16LE",
            "sample_rate_hz": SAMPLE_RATE,
            "channels": CHANNELS,
            "sample_width_bytes": SAMPLE_WIDTH,
        },
        "narration_word_count": word_count,
        "planned_duration_seconds": planned_duration,
        "actual_audio_duration_seconds": (
            duration_seconds
        ),
        "planned_pace_wpm": planned_wpm,
        "actual_effective_wpm": (
            actual_effective_wpm
        ),
        "duration_delta_seconds": (
            round(
                duration_seconds
                - float(
                    planned_duration
                ),
                3,
            )
            if planned_duration is not None
            else None
        ),
        "prompt_file": str(
            VOICE_PROMPT_FILE
        ),
    }

    save_json(
        VOICE_METADATA_FILE,
        metadata,
    )

    STATUS_FILE.write_text(
        "SUCCESS\n"
        f"Finished: {now_utc()}\n"
        f"Model: {MODEL}\n"
        f"Voice: {VOICE_NAME}\n"
        f"Output: {OUTPUT_WAV}\n"
        f"Actual duration: "
        f"{duration_seconds:.3f}s\n"
        f"Actual effective WPM: "
        f"{actual_effective_wpm:.1f}\n",
        encoding="utf-8",
    )

    print("Voice generation completed.")
    print(f"Audio: {OUTPUT_WAV}")
    print(
        f"Duration: "
        f"{duration_seconds:.3f}s"
    )
    print(
        f"Effective WPM: "
        f"{actual_effective_wpm:.1f}"
    )
    print(
        f"Metadata: "
        f"{VOICE_METADATA_FILE}"
    )
    print()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception:
        METADATA_DIR.mkdir(
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
            "Voice generation failed."
        )
        print(
            f"See: {STATUS_FILE}"
        )
        print()

        sys.exit(1)
