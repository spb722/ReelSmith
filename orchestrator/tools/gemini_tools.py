"""Gemini TTS narration generation, ported from `generate_voice.py`'s TTS
call (Code Map). `voice_agent` invokes this tool's handler directly -- no
Claude session, no reasoning (AD-1): it is a fixed Gemini call over the
already-validated `narration_script`, mirroring `ingest`'s direct-handler
pattern in `deterministic_tools.py`.

Only a generation/TTS tool lives here, never an "understand" tool (this
epic's directory-layout decision).
"""

from __future__ import annotations

import base64
import hashlib
import json
import wave
from io import BytesIO
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool
from google import genai
from google.genai import types
from PIL import Image

from orchestrator.contracts.veo import VeoFailureContract, VeoSeedContract, sha256_file, shot_fingerprint
from orchestrator.contracts.visual_plan import Shot
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
VEO_SEED_DIR = Path("generated/veo_seeds")


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


def extract_generated_image(response) -> tuple[Image.Image | None, str]:
    """Extract the first generated image and any explanatory model text."""

    text_parts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                text_parts.append(text)
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data:
                if isinstance(data, str):
                    data = base64.b64decode(data)
                return Image.open(BytesIO(bytes(data))).convert("RGB"), "\n".join(text_parts).strip()
    return None, "\n".join(text_parts).strip()


def build_seed_spec(shot: dict, asset: dict, subtitle_text: str) -> dict:
    """Build a dynamic recomposition request from the validated shot.

    Unlike the legacy script this works for any shot assigned VEO mode; it
    never contains a hard-coded [2, 5, 6] routing table.
    """

    validated_shot = Shot.model_validate(shot)
    if validated_shot.generation_mode != "VEO":
        raise ValueError(f"Shot {validated_shot.sequence} is not assigned VEO mode")
    if asset.get("asset_id") not in validated_shot.source_asset_ids:
        raise ValueError("Seed source asset is not cited by the VEO shot")
    source_path = Path(str(asset.get("source_path", "")))
    if not source_path.is_file():
        raise FileNotFoundError(f"Seed source image does not exist: {source_path}")

    analysis = asset.get("analysis", {})
    visual = analysis.get("visual", {})
    production = analysis.get("production", {})
    prompt = (
        "Recompose the supplied source screenshot into a clean, complete 9:16 production frame "
        "for image-to-video generation. Remove all app UI, status bars, buttons, progress bars, "
        "card chrome, instruction text, captions, logos, and watermarks. Preserve the source's "
        "editorial illustration style, palette, subjects, and emotional meaning; do not drift to "
        "photorealism and do not add characters or duplicate subjects. "
        f"Shot goal: {validated_shot.shot_goal}. "
        f"Required composition: {validated_shot.frame_composition}. "
        f"Planned motion context: {validated_shot.motion_plan}. "
        f"Source support: {validated_shot.source_support}. "
        f"Source visual description: {visual.get('description', '')}. "
        f"Source art description: {production.get('story_art_description', '')}. "
        f"Narration context: {subtitle_text.strip()}."
    )
    return {
        "shot_sequence": validated_shot.sequence,
        "shot_fingerprint": shot_fingerprint(validated_shot),
        "source_asset_id": asset["asset_id"],
        "source_image_path": str(source_path),
        "output_image_path": str(VEO_SEED_DIR / f"shot_{validated_shot.sequence:02d}_seed.png"),
        "prompt": prompt,
    }


def generate_seed_image(
    client: genai.Client,
    spec: dict,
    *,
    model: str,
    cost_usd: float,
) -> tuple[VeoSeedContract, str]:
    """Run one Gemini image recomposition and materialize its seed."""

    source_path = Path(spec["source_image_path"])
    if not source_path.is_file():
        raise FileNotFoundError(f"Seed source image does not exist: {source_path}")
    source_image = Image.open(source_path).convert("RGB")
    response = client.models.generate_content(
        model=model,
        contents=[source_image, spec["prompt"]],
        config=types.GenerateContentConfig(
            response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
        ),
    )
    generated_image, model_text = extract_generated_image(response)
    if generated_image is None:
        raise RuntimeError("Gemini image recomposition returned no image")

    output_path = Path(spec["output_image_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    generated_image.save(temporary_path, format="PNG", optimize=True)
    temporary_path.replace(output_path)

    seed = VeoSeedContract(
        shot_sequence=spec["shot_sequence"],
        shot_fingerprint=spec["shot_fingerprint"],
        source_asset_id=spec["source_asset_id"],
        source_image_path=str(source_path),
        source_image_sha256=sha256_file(source_path),
        local_path=str(output_path),
        seed_sha256=sha256_file(output_path),
        prompt=spec["prompt"],
        model=model,
        approved=False,
        qa_summary="",
        cost_usd=cost_usd,
    )
    return seed, model_text


def _image_content(path: Path) -> dict:
    return {
        "type": "image",
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        "mimeType": "image/png",
    }


@tool(
    "generate_veo_seed",
    "Recompose one validated VEO shot into a clean seed and return it for visual QA",
    {
        "shot": dict,
        "asset": dict,
        "subtitle_text": str,
        "project_id": str,
        "location": str,
        "image_model": str,
        "cost_usd": float,
    },
)
async def generate_veo_seed(args: dict) -> dict:
    try:
        spec = build_seed_spec(args["shot"], args["asset"], args.get("subtitle_text", ""))
        client = genai.Client(
            vertexai=True,
            project=args["project_id"],
            location=args["location"],
            http_options=types.HttpOptions(api_version="v1"),
        )
        seed, model_text = generate_seed_image(
            client,
            spec,
            model=args["image_model"],
            cost_usd=float(args["cost_usd"]),
        )
    except Exception as exc:
        try:
            fingerprint = shot_fingerprint(args["shot"])
            sequence = int(args["shot"]["sequence"])
        except Exception:
            fingerprint = hashlib.sha256(
                json.dumps(args.get("shot"), sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            sequence = 1
        failure = VeoFailureContract(
            produced_by="veo_tool",
            shot_sequence=sequence,
            shot_fingerprint=fingerprint,
            stage="seed_generation",
            code="SEED_GENERATION_FAILED",
            reason=f"{type(exc).__name__}: {exc}",
            retryable=True,
            attempt=1,
            cost_usd=0.0,
        )
        return {
            "content": [
                {"type": "text", "text": json.dumps({"failure": failure.model_dump(mode="json")}, ensure_ascii=False)}
            ]
        }

    payload = seed.model_dump(mode="json")
    payload["model_text_response"] = model_text
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
            _image_content(Path(seed.local_path)),
        ]
    }


gemini_server = create_sdk_mcp_server(
    name="gemini",
    tools=[generate_narration_audio, generate_veo_seed],
)
