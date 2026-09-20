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
import time
import wave
from io import BytesIO
from pathlib import Path
from typing import Sequence

from claude_agent_sdk import create_sdk_mcp_server, tool
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image

from orchestrator.contracts.stills import STILLS_DIR, StillFailureContract, StillResultContract
from orchestrator.contracts.veo import VeoFailureContract, VeoSeedContract, sha256_file, shot_fingerprint
from orchestrator.contracts.visual_plan import Shot
from orchestrator.settings import load_settings
from orchestrator.state.voice_cache import TTS_MODEL, TTS_VOICE_NAME, selected_voice_name
from orchestrator.tools.deterministic_tools import tokenize

# Ported verbatim from generate_voice.py -- config literals unchanged.
TTS_LANGUAGE_CODE = "en-US"

AUDIO_DIR = Path("audio")
NARRATION_WAV = AUDIO_DIR / "narration.wav"

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
VEO_SEED_DIR = Path("generated/veo_seeds")

# An image-edit call that comes back with text and no image is usually
# transient -- the identical request succeeds on a retry. A *blocked*
# response is not: retrying a safety refusal only spends quota to be
# refused again, which is exactly how a live run drained its image quota.
EMPTY_IMAGE_MAX_ATTEMPTS = 3
EMPTY_IMAGE_RETRY_SECONDS = 2.0
# Minimum wall-clock gap between image-model calls, process-wide. The
# agent loop plus internal retries can otherwise burst a dozen calls in a
# few seconds and trip the per-minute quota.
GEMINI_IMAGE_MIN_INTERVAL_SECONDS = 5.0
# 429 means slow down, not give up, so it gets its own longer backoff.
QUOTA_RETRY_SECONDS = (15.0, 45.0)
_last_image_call_monotonic = 0.0


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

    voice_name = selected_voice_name()
    print(f"TTS: model={TTS_MODEL} voice={voice_name}")
    prompt = build_tts_prompt(narration_script, args.get("voice_direction", {}))

    client = genai.Client(vertexai=True, project=args["project_id"], location=args["location"])
    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            speech_config=types.SpeechConfig(
                language_code=TTS_LANGUAGE_CODE,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name),
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
        "voice_name": voice_name,
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
        "photorealism, and do not duplicate subjects or invent people who are not "
        "already in the source scene. "
        f"Shot goal: {validated_shot.shot_goal}. "
        f"Required composition: {validated_shot.frame_composition}. "
        f"Planned motion context: {validated_shot.motion_plan}. "
        f"Source support: {validated_shot.source_support}. "
        f"Source visual description: {visual.get('description', '')}. "
        f"Source art description: {production.get('story_art_description', '')}. "
        f"Narration context (for mood and meaning only, never to be written into the image): "
        f"{subtitle_text.strip()}. "
    )
    # FINAL RULE always lands last, after any character block, so a plan that
    # asks for on-screen text cannot win.
    ladder = build_prompt_ladder(prompt, asset)
    return {
        "shot_sequence": validated_shot.sequence,
        "shot_fingerprint": shot_fingerprint(validated_shot),
        "source_asset_id": asset["asset_id"],
        "source_image_path": str(source_path),
        "output_image_path": str(VEO_SEED_DIR / f"shot_{validated_shot.sequence:02d}_seed.png"),
        "prompt": ladder[0]["prompt"],
        "character_reference_paths": list(ladder[0]["reference_image_paths"]),
        "prompt_ladder": ladder,
    }


# Subject types in `analyzed_assets` that mean "a person is already drawn here".
HUMAN_SUBJECT_TYPES = {"REAL_PERSON", "ILLUSTRATED_PERSON", "CARTOON_CHARACTER"}


def resolve_character_references() -> list[Path]:
    """Return the configured character reference images, most general first.

    Empty list means the feature is off, which reproduces the pre-character
    behaviour exactly. The path is read from settings here rather than taken
    as a tool argument on purpose: the SDK marks every declared tool-schema
    key required, so routing it through the agent would force the model to
    retype the path on every call -- and identity-critical values are not
    agent output (the same reasoning that made the orchestrator stamp
    `shot_fingerprint` itself).
    """

    settings = load_settings()

    body_raw = (settings.character_reference_path or "").strip()
    if not body_raw:
        return []
    body = Path(body_raw)
    if not body.is_file():
        return []

    # The face crop only ever supplements the full-body sheet; a face alone
    # cannot describe a whole figure, so it is never used on its own.
    references = [body]
    face_raw = (settings.character_face_reference_path or "").strip()
    if face_raw:
        face = Path(face_raw)
        if face.is_file() and face.resolve() != body.resolve():
            references.append(face)
    return references


ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")


def authoritative_asset(asset: dict) -> dict:
    """Re-read the agent-supplied asset from the persisted analysis.

    The agent relays this dict from its prompt into the tool call, and a large
    one can come back trimmed -- a live run lost `analysis.visual` on shot 6,
    the only shot citing two assets, which silently disabled the character for
    that shot. The asset_id survives (it is already cross-checked against the
    shot), so everything else is read from the file the analyst wrote. Same
    rule as shot_fingerprint: nothing that decides identity is taken from
    agent output.
    """

    asset_id = asset.get("asset_id")
    if not asset_id or not ANALYZED_ASSETS_FILE.is_file():
        return asset
    try:
        persisted = json.loads(ANALYZED_ASSETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return asset
    for candidate in persisted.get("assets") or []:
        if candidate.get("asset_id") == asset_id:
            return candidate
    return asset


def asset_has_human_figure(asset: dict) -> bool:
    """True when the analyzed asset already depicts a person.

    The character only ever replaces a figure that is already there; it is
    never inserted into a scene that has none. `analyzed_assets` already
    answers this, so the rule is enforced in code rather than asked of the
    image model.
    """

    visual = (asset.get("analysis") or {}).get("visual") or {}
    if visual.get("real_people_visible") or visual.get("illustrated_or_cartoon_figures_visible"):
        return True
    return any(
        (subject or {}).get("subject_type") in HUMAN_SUBJECT_TYPES
        for subject in visual.get("visual_subjects") or []
    )


def focal_figure_description(asset: dict) -> str:
    """The first described figure -- the one the character replaces."""

    visual = (asset.get("analysis") or {}).get("visual") or {}
    descriptions = [d for d in (visual.get("figure_descriptions") or []) if str(d).strip()]
    if descriptions:
        return str(descriptions[0]).strip()
    return "the single human figure already present in the scene"


def build_character_prompt_block(
    figure_description: str, reference_count: int, level: int = 0,
) -> str:
    """The character-substitution instruction shared by stills and Veo seeds.

    Deliberately says *replace the figure already present* rather than *add a
    person*: adding is what the surrounding prompt still forbids.
    """

    if reference_count >= 2:
        which = (
            "The SECOND image is a full-body character reference. The THIRD image is a "
            "close-up of that same character's face and is the authority on their facial "
            "features -- follow it exactly for the face. "
        )
    else:
        which = "The SECOND image is a character reference. "

    if level >= 1:
        # Rung 2 of the ladder: identity carried by hair, build and clothing
        # only. Asking for a visible face is what the likeness filter refuses
        # on a figure the source draws from behind, and such a figure has no
        # face to show anyway.
        identity = (
            "Redraw that one figure as the referenced character, matching their hair, "
            "build, colouring and clothing, and keeping the source figure's existing pose, "
            "body angle and head direction exactly as drawn -- do not turn, lift or rotate "
            "the head, and do not add a face that the source does not show. "
        )
    else:
        identity = (
            "Redraw that one figure as the referenced character, keeping the character's "
            "exact face: same face shape, eyes, eyebrows, nose, mouth, beard, hairstyle, "
            "hair colour and skin tone. Study the face in the reference closely and carry "
            "its proportions across even when the character is drawn small. The face must "
            "be clearly visible, in focus, and detailed enough to recognise -- never "
            "faceless, blank, featureless, blurred, hidden, or turned away, and never "
            "simplified into dots or a plain oval even if the scene's other figures are "
            "drawn that way. If the figure in the source scene was looking down or into "
            "shadow, keep its pose and emotional beat but lift the head just enough that "
            "the face reads clearly toward the viewer, and light the face enough to see "
            "its features. "
        )

    return (
        "CHARACTER SUBSTITUTION. The FIRST image is the source scene described above. "
        + which
        + "The character reference images show the person only -- never copy their plain "
        "background, standing pose, framing, crop, or layout into the scene. "
        f"The source scene already contains this human figure: {figure_description} "
        + identity
        + "Draw the character in the character reference's own "
        "illustration style, but keep everything else exactly as the source scene draws it: "
        "the scene's art style, palette, line quality, texture, lighting, background, props "
        "and composition are unchanged. Do NOT restyle the scene to match the character. "
        "Adapt only the character's pose, body angle, scale and clothing to the figure "
        "already occupying that spot, so the character sits or stands exactly where that "
        "figure stood and carries the same emotional beat. Replace that ONE focal figure "
        "only. Every other person in the scene -- background silhouettes, crowds, secondary "
        "figures -- stays exactly as the source draws them. Add no second character and no "
        "duplicate of the character anywhere in the frame. "
    )


def character_reference_paths_for_asset(asset: dict) -> list[Path]:
    """Character references to send for this asset, or [] when none apply."""

    if not asset_has_human_figure(authoritative_asset(asset)):
        return []
    return resolve_character_references()


def describe_empty_image_response(response, model_text: str) -> str:
    """Summarise why an image-edit call came back without an image."""

    details: list[str] = []
    feedback = getattr(response, "prompt_feedback", None)
    blocked = getattr(feedback, "block_reason", None) if feedback is not None else None
    if blocked:
        details.append(f"prompt_feedback.block_reason={blocked}")
    for candidate in getattr(response, "candidates", None) or []:
        finish = getattr(candidate, "finish_reason", None)
        if finish:
            details.append(f"finish_reason={finish}")
        for rating in getattr(candidate, "safety_ratings", None) or []:
            if getattr(rating, "blocked", False):
                details.append(f"blocked_safety_category={getattr(rating, 'category', '?')}")
    if model_text:
        details.append(f"model_text={model_text[:600]!r}")
    if not details:
        details.append("no candidates, no text, and no block reason were returned")
    return " ".join(details)


def _pace_image_call() -> None:
    """Keep at least `GEMINI_IMAGE_MIN_INTERVAL_SECONDS` between image calls."""

    global _last_image_call_monotonic
    if _last_image_call_monotonic:
        elapsed = time.monotonic() - _last_image_call_monotonic
        remaining = GEMINI_IMAGE_MIN_INTERVAL_SECONDS - elapsed
        if remaining > 0:
            time.sleep(remaining)
    _last_image_call_monotonic = time.monotonic()


def blocked_reason(response) -> str:
    """The safety/policy block reason on a response, or "" when not blocked."""

    feedback = getattr(response, "prompt_feedback", None)
    reason = getattr(feedback, "block_reason", None) if feedback is not None else None
    return str(reason) if reason else ""


class ImageEditRefused(RuntimeError):
    """The image model declined this request and will decline it again.

    Distinct from a transient empty response: a refusal is deterministic, so
    the caller must change the request rather than retry it.
    """

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


FINAL_NO_TEXT_RULE = (
    "FINAL RULE, overriding anything above: render NO text of any kind -- no words, "
    "letters, captions, titles, quotes, signage, or handwriting, anywhere in the frame. "
    "If any description above mentions a text overlay, on-screen wording, or a line that "
    "fades in, that belongs to a later compositing step, not to this image -- produce the "
    "clean plate without it."
)

# How many character-carrying wordings to try before dropping the character.
CHARACTER_PROMPT_LEVELS = 2


def build_prompt_ladder(base_prompt: str, asset: dict) -> list[dict]:
    """Prompt variants to try in order, strongest first.

    The image model's likeness filter refuses some combinations outright
    (block_reason=OTHER) and the refusal is deterministic, so the only way to
    find the strongest wording it will accept is to ask. Predicting it from the
    figure description was tried and rejected: that means keyword-matching free
    text an LLM wrote, which breaks on the first phrasing nobody listed.

      0  character + a recognisable face        (best likeness)
      1  character + the source's own pose      (no face invented)
      2  no character                           (scene still renders)
    """

    character_paths = character_reference_paths_for_asset(asset)
    description = focal_figure_description(authoritative_asset(asset))

    ladder: list[dict] = []
    for level in range(CHARACTER_PROMPT_LEVELS) if character_paths else ():
        ladder.append({
            "level": level,
            "prompt": base_prompt + build_character_prompt_block(
                description, len(character_paths), level,
            ) + FINAL_NO_TEXT_RULE,
            "reference_image_paths": [str(path) for path in character_paths],
        })
    ladder.append({
        "level": CHARACTER_PROMPT_LEVELS,
        "prompt": base_prompt + FINAL_NO_TEXT_RULE,
        "reference_image_paths": [],
    })
    return ladder


def run_gemini_image_edit(
    client: genai.Client,
    *,
    source_image_path: Path,
    prompt: str,
    model: str,
    reference_image_paths: Sequence[Path] = (),
    max_attempts: int = EMPTY_IMAGE_MAX_ATTEMPTS,
) -> tuple[Image.Image, str]:
    """Run one Gemini image-edit call and return the generated image plus any
    explanatory model text. Shared by every one-shot Gemini image-edit tool
    (Veo seed recomposition, still generation) so the raw call mechanics
    never drift between them.
    """

    if not source_image_path.is_file():
        raise FileNotFoundError(f"Source image does not exist: {source_image_path}")
    source_image = Image.open(source_image_path).convert("RGB")
    # Scene first: Gemini image-edit treats the leading image as the plate being
    # edited, so a reference in front of it invites an edited portrait instead
    # of an edited scene.
    reference_images = []
    for reference_path in reference_image_paths:
        reference_path = Path(reference_path)
        if not reference_path.is_file():
            raise FileNotFoundError(f"Reference image does not exist: {reference_path}")
        reference_images.append(Image.open(reference_path).convert("RGB"))
    # Three distinct outcomes, three different responses:
    #   blocked  -> refusal, the answer will not change, fail now
    #   429      -> slow down and try again, with a long backoff
    #   no image -> transient null, retry quickly
    diagnosis = ""
    for attempt in range(1, max_attempts + 1):
        _pace_image_call()
        try:
            response = client.models.generate_content(
                model=model,
                contents=[source_image, *reference_images, prompt],
                config=types.GenerateContentConfig(
                    response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
                ),
            )
        except genai_errors.APIError as exc:
            if getattr(exc, "code", None) != 429 or attempt == max_attempts:
                raise
            backoff = QUOTA_RETRY_SECONDS[min(attempt, len(QUOTA_RETRY_SECONDS)) - 1]
            time.sleep(backoff)
            continue

        generated_image, model_text = extract_generated_image(response)
        if generated_image is not None:
            return generated_image, model_text

        # The model's own text is the only thing that distinguishes a refusal
        # (safety, likeness, policy) from a transient empty response, so it
        # must reach the failure report rather than being discarded here.
        diagnosis = describe_empty_image_response(response, model_text)
        blocked = blocked_reason(response)
        if blocked:
            raise ImageEditRefused(
                "Gemini image edit refused this request and will keep refusing it, "
                f"so it was not retried. {diagnosis}",
                reason=blocked,
            )
        if attempt < max_attempts:
            time.sleep(EMPTY_IMAGE_RETRY_SECONDS * attempt)

    raise RuntimeError(
        f"Gemini image edit returned no image after {max_attempts} attempts. {diagnosis}"
    )


def _save_generated_image(generated_image: Image.Image, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    generated_image.save(temporary_path, format="PNG", optimize=True)
    temporary_path.replace(output_path)


def generate_seed_image(
    client: genai.Client,
    spec: dict,
    *,
    model: str,
    cost_usd: float,
) -> tuple[VeoSeedContract, str]:
    """Run one Gemini image recomposition and materialize its seed."""

    source_path = Path(spec["source_image_path"])
    generated_image, model_text, rung = run_image_edit_ladder(client, spec, model=model)
    spec["used_prompt_level"] = rung["level"]

    output_path = Path(spec["output_image_path"])
    _save_generated_image(generated_image, output_path)

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


def build_still_spec(shot: dict, asset: dict, subtitle_text: str) -> dict:
    """Build a dynamic still-generation request from the validated shot.

    Unlike the legacy `prepare_remotion_stills.py` script this works for any
    shot assigned STILL mode; it never contains a hard-coded per-shot-number
    `if shot_sequence == N` prompt ladder.
    """

    validated_shot = Shot.model_validate(shot)
    if validated_shot.generation_mode != "STILL":
        raise ValueError(f"Shot {validated_shot.sequence} is not assigned STILL mode")
    if asset.get("asset_id") not in validated_shot.source_asset_ids:
        raise ValueError("Still source asset is not cited by the STILL shot")
    source_path = Path(str(asset.get("source_path", "")))
    if not source_path.is_file():
        raise FileNotFoundError(f"Still source image does not exist: {source_path}")

    analysis = asset.get("analysis", {})
    visual = analysis.get("visual", {})
    production = analysis.get("production", {})
    prompt = (
        "Use the provided mobile screenshot only as visual reference for the embedded illustration. "
        "Create a clean standalone vertical 9:16 editorial illustration for a cinematic short-form reel. "
        "Remove all app UI, status bars, buttons, progress indicators, card chrome, captions, and "
        "surrounding interface. Do not include any visible text, letters, numbers, logos, or watermarks. "
        "Preserve the source's editorial illustration style, palette, subjects, and emotional meaning; "
        "do not drift to photorealism. Show exactly one instance of the focal subject -- do not duplicate "
        "the same subject or scene twice in frame. Do not invent people who are not "
        "already in the source scene. "
        "Leave clean visual space for later Remotion subtitles and typography. "
        f"Shot goal: {validated_shot.shot_goal}. "
        f"Required composition: {validated_shot.frame_composition}. "
        f"Planned motion context: {validated_shot.motion_plan}. "
        f"Source support: {validated_shot.source_support}. "
        f"Source visual description: {visual.get('description', '')}. "
        f"Source art description: {production.get('story_art_description', '')}. "
        f"Narration context (for mood and meaning only, never to be written into the image): "
        f"{subtitle_text.strip()}. "
    )
    # FINAL RULE always lands last, after any character block, so a plan that
    # asks for on-screen text cannot win.
    ladder = build_prompt_ladder(prompt, asset)
    return {
        "shot_sequence": validated_shot.sequence,
        "shot_fingerprint": shot_fingerprint(validated_shot),
        "source_asset_id": asset["asset_id"],
        "source_image_path": str(source_path),
        "output_image_path": str(STILLS_DIR / f"shot_{validated_shot.sequence:02d}.png"),
        "prompt": ladder[0]["prompt"],
        "character_reference_paths": list(ladder[0]["reference_image_paths"]),
        "prompt_ladder": ladder,
    }


def run_image_edit_ladder(
    client: genai.Client, spec: dict, *, model: str,
) -> tuple[Image.Image, str, dict]:
    """Walk the spec's prompt ladder until the model accepts one rung.

    Returns the image, the model's text, and the rung that produced it. Only a
    *refusal* advances the ladder -- a transient empty response is already
    retried inside `run_gemini_image_edit`, and any other error propagates.
    """

    source_path = Path(spec["source_image_path"])
    ladder = spec.get("prompt_ladder") or [{
        "level": 0,
        "prompt": spec["prompt"],
        "reference_image_paths": spec.get("character_reference_paths", []),
    }]

    refusals: list[str] = []
    for rung in ladder:
        try:
            image, model_text = run_gemini_image_edit(
                client,
                source_image_path=source_path,
                prompt=rung["prompt"],
                model=model,
                reference_image_paths=[Path(p) for p in rung["reference_image_paths"]],
            )
        except ImageEditRefused as exc:
            refusals.append(f"level {rung['level']}: {exc}")
            continue
        return image, model_text, rung

    raise ImageEditRefused(
        "Gemini image edit refused every prompt variant, including the one with no "
        "character reference, so the scene itself is the trigger. "
        + " | ".join(refusals)
    )


def describe_ladder_outcome(rung: dict) -> str:
    """One line for the QA agent explaining which rung produced the image."""

    return {
        0: "Character reference applied with a recognisable face, as planned.",
        1: (
            "The image model refused the recognisable-face wording for this scene, so the "
            "character was applied keeping the source figure's own pose and head direction. "
            "Judge the likeness on hair, build, colouring and clothing -- do NOT reject this "
            "image for a face that is turned away or not visible."
        ),
        2: (
            "The image model refused every wording that included the character reference, so "
            "this scene was generated without the character. The figure is the source's own. "
            "Do NOT reject this image for missing the character."
        ),
    }.get(rung["level"], "")


def generate_still_image(
    client: genai.Client,
    spec: dict,
    *,
    model: str,
    cost_usd: float,
) -> tuple[StillResultContract, str]:
    """Run one Gemini image edit and materialize the resulting still."""

    source_path = Path(spec["source_image_path"])
    generated_image, model_text, rung = run_image_edit_ladder(client, spec, model=model)
    spec["used_prompt_level"] = rung["level"]

    output_path = Path(spec["output_image_path"])
    _save_generated_image(generated_image, output_path)

    result = StillResultContract(
        shot_sequence=spec["shot_sequence"],
        shot_fingerprint=spec["shot_fingerprint"],
        source_asset_id=spec["source_asset_id"],
        local_image_path=str(output_path),
        image_sha256=sha256_file(output_path),
        model=model,
        approved=False,
        qa_summary="",
        cost_usd=cost_usd,
    )
    return result, model_text


def _ladder_outcome_block(spec: dict) -> list[dict]:
    """Tell the QA agent which prompt rung actually produced this image.

    Without it the agent rejects a correct image for a face the model was
    refused permission to draw, burning its whole retry ceiling on something no
    retry can change.
    """

    note = describe_ladder_outcome({"level": spec.get("used_prompt_level", 0)})
    return [{"type": "text", "text": note}] if note else []


def _character_reference_blocks(spec: dict) -> list[dict]:
    """Label + image blocks letting the QA agent compare against the character.

    The reference travels in the tool *result*, never in `payload`: the agent
    copies `payload` into its outcome contract, which is `extra="forbid"`, so
    every extra key there is a fresh validation hazard.
    """

    paths = [Path(path) for path in spec.get("character_reference_paths", [])]
    if not paths:
        return []

    blocks: list[dict] = [{
        "type": "text",
        "text": (
            "The image above is the generated shot and is the ONLY image under QA. "
            f"The next {len(paths)} image(s) are character reference sheets, NOT the shot: "
            "use them only to check that the person drawn in the shot is this character. "
            "Never judge the shot against a reference's background, pose, crop, or framing."
        ),
    }]
    for path in paths:
        blocks.append(_image_content(path))
    return blocks


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
            *_ladder_outcome_block(spec),
            *_character_reference_blocks(spec),
        ]
    }


@tool(
    "generate_still",
    "Generate one validated STILL shot's production image via Gemini and return it for visual QA",
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
async def generate_still(args: dict) -> dict:
    try:
        spec = build_still_spec(args["shot"], args["asset"], args.get("subtitle_text", ""))
        client = genai.Client(
            vertexai=True,
            project=args["project_id"],
            location=args["location"],
            http_options=types.HttpOptions(api_version="v1"),
        )
        result, model_text = generate_still_image(
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
        failure = StillFailureContract(
            produced_by="still_tool",
            shot_sequence=sequence,
            shot_fingerprint=fingerprint,
            stage="still_generation",
            code="STILL_GENERATION_FAILED",
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

    payload = result.model_dump(mode="json")
    payload["model_text_response"] = model_text
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
            _image_content(Path(result.local_image_path)),
            *_ladder_outcome_block(spec),
            *_character_reference_blocks(spec),
        ]
    }


gemini_server = create_sdk_mcp_server(
    name="gemini",
    tools=[generate_narration_audio, generate_veo_seed, generate_still],
)
