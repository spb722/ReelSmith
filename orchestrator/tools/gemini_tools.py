"""Gemini TTS narration generation, ported from `generate_voice.py`'s TTS
call (Code Map). `voice_agent` invokes this tool's handler directly -- no
Claude session, no reasoning (AD-1): it is a fixed Gemini call over the
already-validated `narration_script`, mirroring `ingest`'s direct-handler
pattern in `deterministic_tools.py`.

Only a generation/TTS tool lives here, never an "understand" tool (this
epic's directory-layout decision).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool
from google import genai
from google.genai import types

from orchestrator.tools.deterministic_tools import tokenize

# Ported verbatim from generate_voice.py -- config literals unchanged.
TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_VOICE_NAME = "Gacrux"
TTS_LANGUAGE_CODE = "en-US"

AUDIO_DIR = Path("audio")
NARRATION_WAV = AUDIO_DIR / "narration.wav"

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM


def count_words(text: str) -> int:
    # Shares deterministic_tools.py's own word tokenizer (WORD_RE via
    # `tokenize`) instead of a second copy of the same regex that could drift.
    return len(tokenize(text))


def apply_pause_tags(narration: str, pause_after_phrases: list[str]) -> str:
    """Add Gemini TTS audio tags after selected phrases (delivery controls,
    not spoken text). Ported verbatim from generate_voice.py.
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
        tagged = tagged.replace(phrase, phrase + tag, 1)
    return tagged


def build_tts_prompt(narration_script: str, voice_direction: dict) -> str:
    """Ported verbatim from generate_voice.py's `build_tts_prompt`, adapted
    to take the narration script and voice direction directly rather than
    a whole `final_story_plan.json` dict.
    """
    narration = narration_script.strip()
    persona = voice_direction.get("persona", "Mature, thoughtful guide")
    tone = voice_direction.get("tone", [])
    pace_wpm = voice_direction.get("pace_wpm", 133)
    emphasis_phrases = voice_direction.get("emphasis_phrases", [])
    pause_after_phrases = voice_direction.get("pause_after_phrases", [])

    tagged_narration = apply_pause_tags(narration, pause_after_phrases)
    # A light opening tag helps establish the performance without
    # over-directing every sentence.
    tagged_narration = "[slow] " + tagged_narration

    tone_text = ", ".join(tone)
    emphasis_text = "; ".join(emphasis_phrases)

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


def extract_pcm(response) -> bytes:
    """Ported verbatim from generate_voice.py's `extract_pcm`."""
    if not getattr(response, "candidates", None):
        raise RuntimeError("Gemini TTS returned no candidates.")

    candidate = response.candidates[0]
    if candidate.content is None or not candidate.content.parts:
        raise RuntimeError("Gemini TTS returned no content parts.")

    for part in candidate.content.parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None and getattr(inline_data, "data", None):
            data = inline_data.data
            if isinstance(data, bytes):
                return data
            # Defensive fallback if an SDK version returns bytearray.
            if isinstance(data, bytearray):
                return bytes(data)

    raise RuntimeError("Gemini TTS response contained no audio data.")


def write_wave(filename: Path, pcm: bytes) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(filename), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


def get_wave_duration_seconds(filename: Path) -> float:
    with wave.open(str(filename), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
    return round(frames / float(rate), 3)


@tool(
    "generate_narration_audio",
    "Generate narration audio via Gemini TTS from a locked narration script",
    {"narration_script": str, "voice_direction": dict, "project_id": str, "location": str},
)
async def generate_narration_audio(args: dict) -> dict:
    narration_script = args["narration_script"].strip()
    if not narration_script:
        raise ValueError("narration_script must not be empty")

    prompt = build_tts_prompt(narration_script, args.get("voice_direction", {}))

    client = genai.Client(vertexai=True, project=args["project_id"], location=args["location"])
    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            speech_config=types.SpeechConfig(
                language_code=TTS_LANGUAGE_CODE,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE_NAME),
                ),
            ),
        ),
    )

    pcm = extract_pcm(response)
    write_wave(NARRATION_WAV, pcm)
    duration_seconds = get_wave_duration_seconds(NARRATION_WAV)

    payload = {
        "audio_path": str(NARRATION_WAV),
        "duration_seconds": duration_seconds,
        "model": TTS_MODEL,
        "voice_name": TTS_VOICE_NAME,
        # The actual Gemini input is this whole prompt (instructional
        # preamble + narration), not just the bare narration_script -- the
        # caller's input-token cost estimate must use this, not word count
        # of the narration alone.
        "prompt_word_count": count_words(prompt),
    }
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


gemini_server = create_sdk_mcp_server(name="gemini", tools=[generate_narration_audio])
