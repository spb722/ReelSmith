"""Content-addressed narration provenance shared by generation and resume checks."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from orchestrator.state.run_manifest import _atomic_write_json

TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_VOICE_NAME = "Gacrux"
AUDIO_CACHE_FILE = Path("metadata/narration_audio.json")
AUDIO_FILE = Path("audio/narration.wav")


def selected_voice_name() -> str:
    voice = os.environ.get("TTS_VOICE_NAME", TTS_VOICE_NAME).strip()
    if not voice:
        raise ValueError("TTS_VOICE_NAME must not be empty")
    return voice


def audio_provenance(narration: str, direction: dict) -> dict:
    return {
        "version": 1,
        "narration_script": narration,
        "voice_direction": direction,
        "voice_name": selected_voice_name(),
        "model": TTS_MODEL,
        "audio_sha256": hashlib.sha256(AUDIO_FILE.read_bytes()).hexdigest(),
    }


def validate_audio_provenance(provenance: dict, narration: str, direction: dict) -> None:
    if not AUDIO_FILE.is_file() or AUDIO_FILE.stat().st_size == 0:
        raise ValueError("Narration audio is missing or empty")
    if provenance != audio_provenance(narration, direction):
        raise ValueError("Narration audio provenance is stale or unverifiable")


def save_audio_cache(payload: dict, narration: str, direction: dict) -> dict:
    if payload.get("model") != TTS_MODEL or payload.get("voice_name") != selected_voice_name():
        raise ValueError("TTS response model/voice does not match the requested configuration")
    if Path(payload["audio_path"]) != AUDIO_FILE:
        raise ValueError("TTS must produce the canonical audio/narration.wav")
    provenance = audio_provenance(narration, direction)
    validate_audio_provenance(provenance, narration, direction)
    _atomic_write_json(AUDIO_CACHE_FILE, {"provenance": provenance, "tts": payload})
    return provenance


def load_audio_cache(narration: str, direction: dict) -> dict | None:
    try:
        cached = json.loads(AUDIO_CACHE_FILE.read_text(encoding="utf-8"))
        validate_audio_provenance(cached["provenance"], narration, direction)
        payload = cached["tts"]
        if Path(payload["audio_path"]) != AUDIO_FILE:
            return None
        if payload["model"] != TTS_MODEL or payload["voice_name"] != selected_voice_name():
            return None
        duration = float(payload["duration_seconds"])
        if not math.isfinite(duration) or duration <= 0:
            return None
        return cached
    except (OSError, ValueError, TypeError, KeyError):
        return None
