"""Centralized orchestrator settings.

Single source of truth for `PROJECT_ID`, region, GCS bucket URI, budget
ceiling, and model names. Every tool/agent added in later stories reads
from here instead of re-declaring its own module-level constants (the
pattern used today by `generate_veo_clips.py`, `analyze_assets.py`,
`story_director.py`, and friends).
"""

from __future__ import annotations

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
DEFAULT_MAX_BUDGET_USD = 5.0
DEFAULT_VEO_MODEL = "veo-3.1-fast-generate-001"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


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


def load_settings() -> Settings:
    """Build a `Settings` instance from environment variables.

    Falls back to the existing scripts' hardcoded literals as defaults.
    A malformed `MAX_BUDGET_USD` raises `SettingsError` (a clear, caught
    error) rather than letting an uncaught `ValueError` from `float()`
    propagate out of this function or out of module import.
    """

    max_budget_raw = os.getenv("MAX_BUDGET_USD")

    if max_budget_raw is None:
        max_budget_usd = DEFAULT_MAX_BUDGET_USD
    else:
        try:
            max_budget_usd = float(max_budget_raw)
        except ValueError as exc:
            raise SettingsError(
                f"MAX_BUDGET_USD={max_budget_raw!r} is not a valid float"
            ) from exc

    return Settings(
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID),
        location=os.getenv("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION),
        gcs_bucket_uri=os.getenv("GCS_BUCKET_URI", DEFAULT_GCS_BUCKET_URI),
        max_budget_usd=max_budget_usd,
        veo_model=os.getenv("VEO_MODEL", DEFAULT_VEO_MODEL),
        gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
    )
