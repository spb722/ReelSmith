"""Centralized orchestrator settings.

Single source of truth for `PROJECT_ID`, region, GCS bucket URI, budget
ceiling, and model names. Every tool/agent added in later stories reads
from here instead of re-declaring its own module-level constants (the
pattern used today by `generate_veo_clips.py`, `analyze_assets.py`,
`story_director.py`, and friends).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass


class SettingsError(Exception):
    """Raised when settings cannot be loaded from the environment."""


# Defaults mirror the literals already hardcoded across the existing
# root scripts (see docs/implementation-artifacts/spec-1-1-orchestrator-
# scaffold-preflight-budget.md Code Map for exact source locations).
DEFAULT_PROJECT_ID = "gen-lang-client-0240752803"
DEFAULT_LOCATION = "global"
DEFAULT_GCS_BUCKET_URI = "gs://sachin-kayaking-video-test/book_reels/veo/"
# Raised from 5.0 when reels began planning 2-3 real Veo shots: each one
# reserves image_call_cost_usd + veo_call_cost_usd (~$1.24) of headroom before
# it may start, which a $5 ceiling could not cover alongside the Claude stages.
DEFAULT_MAX_BUDGET_USD = 15.0
DEFAULT_VEO_MODEL = "veo-3.1-fast-generate-001"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
# Which backend actually draws the stills and Veo seeds. "gemini" is the
# one-shot image-edit API; "codex" shells out to the local Codex CLI and asks
# it, via the `$imagegen` directive, to write the image to disk. Gemini stays
# the default so an unset environment reproduces today's behaviour exactly.
IMAGE_PROVIDERS = ("gemini", "codex")
DEFAULT_IMAGE_PROVIDER = "gemini"
# Codex runs on the operator's ChatGPT subscription rather than per-call API
# billing, so a Codex image reserves no budget headroom. The Gemini estimate
# below still applies whenever that provider is selected.
CODEX_IMAGE_CALL_COST_USD = 0.0
# One Codex image is a whole agent turn, not an API round trip. Minutes are
# normal; this ceiling only exists so a wedged call cannot stall a run forever.
DEFAULT_CODEX_IMAGE_TIMEOUT_SECONDS = 900.0
DEFAULT_VEO_RESOLUTION = "720p"
DEFAULT_VEO_DURATION_SECONDS = 8
# The clip lengths Veo accepts, shortest first. A shot only becomes a video
# shot if it fits inside the longest of these, and it is generated at the
# shortest one that covers it rather than always paying for the maximum.
# Configuration, not model knowledge (same framing as the cost estimates
# below) -- narrow it to (8,) if a model rejects the shorter values.
VEO_ALLOWED_DURATION_SECONDS = (4, 6, 8)
DEFAULT_VEO_POLL_SECONDS = 15.0
DEFAULT_VEO_MAX_POLL_SECONDS = 900.0
DEFAULT_MAX_VEO_ATTEMPTS = 3
# How many tries each stage gets before the run halts. These were constants
# scattered through run.py; they live here so a project's config.json can be
# seeded from one place and the operator can raise a ceiling without editing
# Python. Keys match the stage ids used in `iteration_counts`.
DEFAULT_MAX_ATTEMPTS = {
    "asset_analyst": 4,
    "story_agent": 4,
    "visual_agent": 4,
    "voice_agent": 3,
    "veo_agent": DEFAULT_MAX_VEO_ATTEMPTS,
    "stills_agent": 3,
}
# Cost estimates are configuration, not hidden model knowledge. They are
# intentionally conservative and can be updated without code changes.
DEFAULT_IMAGE_CALL_COST_USD = 0.04
DEFAULT_VEO_CALL_COST_USD = 1.20

# The Claude model every orchestrator agent runs on. Left unset, the Agent SDK
# picks its own default (claude-sonnet-5 in the runs before this was pinned),
# which is not something a reel's cost or quality should depend on silently.
DEFAULT_CLAUDE_MODEL = "claude-opus-5"

# Recurring-character reference images. Empty string disables the feature and
# reproduces the pre-character behaviour exactly (one image per Gemini edit).
DEFAULT_CHARACTER_REFERENCE_PATH = "assets/character/character.png"
# Off by default, and deliberately so. Sending a tight, photoreal close-up of a
# real person's face alongside the body sheet made the image model return
# prompt_feedback.block_reason=OTHER -- its likeness filter reads a portrait
# crop as a request to depict a real individual. The identical request with the
# full-body sheet alone is accepted. Point CHARACTER_FACE_REFERENCE_PATH at a
# flatter, more illustrated face crop to opt back in.
DEFAULT_CHARACTER_FACE_REFERENCE_PATH = ""
# A multi-angle sheet (front, three-quarter, profile). Preferred over the
# single front-on sheet when present: a scene that draws its figure in profile
# cannot be checked against a front view, and a live run lost three attempts
# to exactly that -- the QA agent reported the reference's "face shape, thick
# straight eyebrows, broad nose and mouth shape are not visible" on a figure
# the source itself draws side-on.
DEFAULT_CHARACTER_TURNAROUND_REFERENCE_PATH = "assets/character/character_turnaround.png"


# Story 1.5: Gemini TTS / Google STT per-unit cost-rate constants. Unlike
# Claude calls (which get total_cost_usd automatically from the Agent SDK),
# no cost-rate constants exist anywhere in the repo for these tools yet.
# Best-available public 2026 pricing -- sourced from public pricing pages,
# not repo precedent; worth reconfirming if it drifts.
GEMINI_TTS_OUTPUT_TOKEN_COST_USD = 20.0 / 1_000_000  # $20 / 1M output audio tokens
GEMINI_TTS_INPUT_TOKEN_COST_USD = 1.0 / 1_000_000  # $1 / 1M input text tokens
GEMINI_TTS_AUDIO_TOKENS_PER_SECOND = 25  # 25 audio tokens per second of output
STT_COST_PER_SECOND_USD = 0.016 / 60  # $0.016 / minute, standard real-time


@dataclass(frozen=True)
class Settings:
    project_id: str
    location: str
    gcs_bucket_uri: str
    max_budget_usd: float
    veo_model: str
    gemini_model: str
    image_model: str = DEFAULT_IMAGE_MODEL
    image_provider: str = DEFAULT_IMAGE_PROVIDER
    codex_image_timeout_seconds: float = DEFAULT_CODEX_IMAGE_TIMEOUT_SECONDS
    veo_resolution: str = DEFAULT_VEO_RESOLUTION
    veo_duration_seconds: int = DEFAULT_VEO_DURATION_SECONDS
    veo_poll_seconds: float = DEFAULT_VEO_POLL_SECONDS
    veo_max_poll_seconds: float = DEFAULT_VEO_MAX_POLL_SECONDS
    max_veo_attempts: int = DEFAULT_MAX_VEO_ATTEMPTS
    image_call_cost_usd: float = DEFAULT_IMAGE_CALL_COST_USD
    veo_call_cost_usd: float = DEFAULT_VEO_CALL_COST_USD
    claude_model: str = DEFAULT_CLAUDE_MODEL
    character_reference_path: str = DEFAULT_CHARACTER_REFERENCE_PATH
    character_face_reference_path: str = DEFAULT_CHARACTER_FACE_REFERENCE_PATH
    character_turnaround_reference_path: str = DEFAULT_CHARACTER_TURNAROUND_REFERENCE_PATH


def _float_setting(name: str, default: float, *, positive: bool = False) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name}={raw!r} is not a valid float") from exc
    if not math.isfinite(value):
        raise SettingsError(f"{name} must be finite")
    if positive and value <= 0:
        raise SettingsError(f"{name} must be greater than zero")
    if value < 0:
        raise SettingsError(f"{name} must not be negative")
    return value


def _str_setting(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    if not raw.strip():
        raise SettingsError(f"{name} must not be empty")
    return raw


def _int_setting(name: str, default: int, *, positive: bool = False) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name}={raw!r} is not a valid integer") from exc
    if positive and value <= 0:
        raise SettingsError(f"{name} must be greater than zero")
    if value < 0:
        raise SettingsError(f"{name} must not be negative")
    return value


def _image_provider_setting() -> str:
    raw = _str_setting("IMAGE_PROVIDER", DEFAULT_IMAGE_PROVIDER).strip().lower()
    if raw not in IMAGE_PROVIDERS:
        raise SettingsError(
            f"IMAGE_PROVIDER={raw!r} is not one of {', '.join(IMAGE_PROVIDERS)}"
        )
    return raw


def _image_call_cost_setting(provider: str) -> float:
    """Per-image budget reservation for the selected provider.

    Codex bills against the operator's subscription, so its calls reserve
    nothing -- but an explicit IMAGE_CALL_COST_USD still wins, since the
    operator may want the ceiling to account for them anyway.
    """

    default = (
        CODEX_IMAGE_CALL_COST_USD if provider == "codex" else DEFAULT_IMAGE_CALL_COST_USD
    )
    return _float_setting("IMAGE_CALL_COST_USD", default)


def load_settings() -> Settings:
    """Build a `Settings` instance from environment variables.

    Falls back to the existing scripts' hardcoded literals as defaults.
    A malformed `MAX_BUDGET_USD` raises `SettingsError` (a clear, caught
    error) rather than letting an uncaught `ValueError` from `float()`
    propagate out of this function or out of module import.
    """

    max_budget_usd = _float_setting("MAX_BUDGET_USD", DEFAULT_MAX_BUDGET_USD)
    image_provider = _image_provider_setting()

    return Settings(
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID),
        location=os.getenv("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION),
        gcs_bucket_uri=os.getenv("GCS_BUCKET_URI", DEFAULT_GCS_BUCKET_URI),
        max_budget_usd=max_budget_usd,
        veo_model=_str_setting("VEO_MODEL", DEFAULT_VEO_MODEL),
        gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        image_model=_str_setting("IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
        veo_resolution=_str_setting("VEO_RESOLUTION", DEFAULT_VEO_RESOLUTION),
        veo_duration_seconds=_int_setting(
            "VEO_DURATION_SECONDS", DEFAULT_VEO_DURATION_SECONDS, positive=True
        ),
        veo_poll_seconds=_float_setting("VEO_POLL_SECONDS", DEFAULT_VEO_POLL_SECONDS, positive=True),
        veo_max_poll_seconds=_float_setting(
            "VEO_MAX_POLL_SECONDS", DEFAULT_VEO_MAX_POLL_SECONDS, positive=True
        ),
        max_veo_attempts=_int_setting("MAX_VEO_ATTEMPTS", DEFAULT_MAX_VEO_ATTEMPTS, positive=True),
        image_provider=image_provider,
        codex_image_timeout_seconds=_float_setting(
            "CODEX_IMAGE_TIMEOUT_SECONDS", DEFAULT_CODEX_IMAGE_TIMEOUT_SECONDS, positive=True
        ),
        image_call_cost_usd=_image_call_cost_setting(image_provider),
        veo_call_cost_usd=_float_setting("VEO_CALL_COST_USD", DEFAULT_VEO_CALL_COST_USD),
        # Plain os.getenv, not _str_setting: an empty value is the documented
        # "no character" switch, and _str_setting raises on an empty string.
        claude_model=_str_setting("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL),
        character_reference_path=os.getenv(
            "CHARACTER_REFERENCE_PATH", DEFAULT_CHARACTER_REFERENCE_PATH
        ),
        character_face_reference_path=os.getenv(
            "CHARACTER_FACE_REFERENCE_PATH", DEFAULT_CHARACTER_FACE_REFERENCE_PATH
        ),
        character_turnaround_reference_path=os.getenv(
            "CHARACTER_TURNAROUND_REFERENCE_PATH", DEFAULT_CHARACTER_TURNAROUND_REFERENCE_PATH
        ),
    )
