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
from pathlib import Path


class SettingsError(Exception):
    """Raised when settings cannot be loaded from the environment."""


# Absolute, because `enter_project` changes the cwd to `projects/<name>`.
DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv_file(path: Path | None = None) -> None:
    """Put the repo's `.env` into the environment; a variable already set wins.

    `KEY=value` lines only, with optional `export ` and surrounding quotes.
    """

    path = DOTENV_PATH if path is None else path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


# Defaults mirror the literals already hardcoded across the existing
# root scripts (see docs/implementation-artifacts/spec-1-1-orchestrator-
# scaffold-preflight-budget.md Code Map for exact source locations).
DEFAULT_PROJECT_ID = "gen-lang-client-0240752803"
DEFAULT_LOCATION = "global"
DEFAULT_GCS_BUCKET_URI = "gs://sachin-kayaking-video-test/book_reels/veo/"
# Raised from 5.0 when reels began planning 2-3 real Veo shots: each one
# reserves image_call_cost_usd + veo_call_cost_usd (~$1.24) of headroom before
# it may start, which a $5 ceiling could not cover alongside the Claude stages.
# Set generously while Epic 4 is tested end to end; tune once Langfuse shows
# where a real run's money goes.
DEFAULT_MAX_BUDGET_USD = 40.0
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
# Veo "extend video": one 8 s clip + one 7 s extension, tested only this way
# (veo-3.1-fast-generate-001, 720p, 9:16, 2026-09-30). Constants, not env settings.
VEO_EXTENSION_SECONDS = 7
VEO_EXTENSION_RESOLUTIONS = ("720p",)
DEFAULT_VEO_EXTEND_LONG_SHOTS = False
BOOL_TRUE = ("1", "true", "yes", "on")
BOOL_FALSE = ("0", "false", "no", "off")
DEFAULT_VEO_POLL_SECONDS = 15.0
DEFAULT_VEO_MAX_POLL_SECONDS = 900.0
DEFAULT_MAX_VEO_ATTEMPTS = 3
# How many shots per reel may become real Veo video (AD-15). Three is what
# every reel before this setting got; raise it per project for more motion.
# The ceiling keeps a typo from reserving budget for a whole reel of clips.
DEFAULT_MAX_VEO_SHOTS = 3
MAX_VEO_SHOTS_LIMIT = 10
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
# Story 4.3: the Claude spend one shot attempt may use; it becomes that shot
# query's `max_budget_usd`. Live Veo and stills attempts have cost $0.18-$0.59
# of Claude each; $3.00 is deliberately generous until traced runs show the
# real spread with skills loaded.
DEFAULT_CLAUDE_SHOT_ATTEMPT_CAP_USD = 3.0
# How many calls to each vendor may be in flight at once, across every shot.
DEFAULT_VEO_CONCURRENCY = 3
DEFAULT_GEMINI_IMAGE_CONCURRENCY = 2
DEFAULT_CODEX_IMAGE_CONCURRENCY = 1
# Story 4.4: how many shots are in production at once. Each runs its own
# Claude query; the vendor caps above still bound the paid calls under them.
DEFAULT_SHOT_CONCURRENCY = 4
# Story 4.5: the Claude spend one fresh-context story-critic or visual-QA
# reviewer call may use; it is that query's `max_budget_usd`, and every shot
# attempt's reservation (and every story attempt's budget) holds one of them.
# Generous on purpose, like the shot cap, until traced runs show the spread.
DEFAULT_CLAUDE_REVIEWER_CAP_USD = 1.0

# The Claude model every orchestrator agent runs on. Left unset, the Agent SDK
# picks its own default (claude-sonnet-5 in the runs before this was pinned),
# which is not something a reel's cost or quality should depend on silently.
DEFAULT_CLAUDE_MODEL = "claude-opus-5"

# Which writing skill the story agent and its critic load. "story-craft" is
# today's dense reel; "story-craft-easy" is a longer, easier-to-follow one
# (its own word and duration limits live in the story plan contract).
STORY_SKILLS = ("story-craft", "story-craft-easy")
DEFAULT_STORY_SKILL = "story-craft"

# What the source screenshots provide. "content_and_images" (default) is
# today's pipeline: stills and Veo seeds are recomposed from the screenshots'
# own artwork. "content" treats them as text only: the visual agent invents
# every picture, and images are drawn from its descriptions, never from the
# screenshot pixels.
SOURCE_MODES = ("content_and_images", "content")
DEFAULT_SOURCE_MODE = "content_and_images"

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
    # A shot whose images keep failing on `image_provider` gets one more full
    # round of tries on this provider; "" means no fallback.
    image_fallback_provider: str = ""
    codex_image_timeout_seconds: float = DEFAULT_CODEX_IMAGE_TIMEOUT_SECONDS
    veo_resolution: str = DEFAULT_VEO_RESOLUTION
    veo_duration_seconds: int = DEFAULT_VEO_DURATION_SECONDS
    veo_poll_seconds: float = DEFAULT_VEO_POLL_SECONDS
    veo_max_poll_seconds: float = DEFAULT_VEO_MAX_POLL_SECONDS
    max_veo_attempts: int = DEFAULT_MAX_VEO_ATTEMPTS
    max_veo_shots: int = DEFAULT_MAX_VEO_SHOTS
    veo_extend_long_shots: bool = DEFAULT_VEO_EXTEND_LONG_SHOTS
    image_call_cost_usd: float = DEFAULT_IMAGE_CALL_COST_USD
    veo_call_cost_usd: float = DEFAULT_VEO_CALL_COST_USD
    claude_shot_attempt_cap_usd: float = DEFAULT_CLAUDE_SHOT_ATTEMPT_CAP_USD
    veo_concurrency: int = DEFAULT_VEO_CONCURRENCY
    gemini_image_concurrency: int = DEFAULT_GEMINI_IMAGE_CONCURRENCY
    codex_image_concurrency: int = DEFAULT_CODEX_IMAGE_CONCURRENCY
    shot_concurrency: int = DEFAULT_SHOT_CONCURRENCY
    claude_reviewer_cap_usd: float = DEFAULT_CLAUDE_REVIEWER_CAP_USD
    claude_model: str = DEFAULT_CLAUDE_MODEL
    # The story critic's and the visual-QA reviewer's models; `load_settings`
    # defaults both to `claude_model`.
    critic_model: str = DEFAULT_CLAUDE_MODEL
    reviewer_model: str = DEFAULT_CLAUDE_MODEL
    # Loaded here so a bad value stops the run at startup. Code reads them with
    # `story_skill_setting()` / `source_mode_setting()`, the one source the
    # agents, contracts and image tools share.
    story_skill: str = DEFAULT_STORY_SKILL
    source_mode: str = DEFAULT_SOURCE_MODE
    # SOURCE_MODE=content only: the file in styles/ the reel is drawn in. Set
    # from `run --style`, not from the environment.
    visual_style: str = "flat_editorial"
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


def _bool_setting(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in BOOL_TRUE:
        return True
    if value in BOOL_FALSE:
        return False
    raise SettingsError(f"{name}={raw!r} is not true or false")


def _max_veo_shots_setting() -> int:
    value = _int_setting("MAX_VEO_SHOTS", DEFAULT_MAX_VEO_SHOTS)
    if value > MAX_VEO_SHOTS_LIMIT:
        raise SettingsError(f"MAX_VEO_SHOTS must be {MAX_VEO_SHOTS_LIMIT} or fewer, got {value}")
    return value


def _image_provider_setting() -> str:
    raw = _str_setting("IMAGE_PROVIDER", DEFAULT_IMAGE_PROVIDER).strip().lower()
    if raw not in IMAGE_PROVIDERS:
        raise SettingsError(
            f"IMAGE_PROVIDER={raw!r} is not one of {', '.join(IMAGE_PROVIDERS)}"
        )
    return raw


def _image_fallback_provider_setting(image_provider: str) -> str:
    raw = (os.getenv("IMAGE_FALLBACK_PROVIDER") or "").strip().lower()
    if raw in ("", "none", "off"):
        return ""
    if raw not in IMAGE_PROVIDERS:
        raise SettingsError(
            f"IMAGE_FALLBACK_PROVIDER={raw!r} is not one of {', '.join(IMAGE_PROVIDERS)} (or none)"
        )
    if raw == image_provider:
        raise SettingsError(f"IMAGE_FALLBACK_PROVIDER={raw!r} is the same as IMAGE_PROVIDER")
    return raw


def story_skill_setting() -> str:
    """The story-writing skill named by `STORY_SKILL`, validated.

    A plain function, not only a `Settings` field, so the story plan contract
    can read it while validating a plan.
    """

    raw = _str_setting("STORY_SKILL", DEFAULT_STORY_SKILL).strip()
    if raw not in STORY_SKILLS:
        raise SettingsError(f"STORY_SKILL={raw!r} is not one of {', '.join(STORY_SKILLS)}")
    return raw


def source_mode_setting() -> str:
    """What the screenshots provide, named by `SOURCE_MODE`, validated.

    A plain function for the same reason as `story_skill_setting`: the image
    tools read it without a full settings load.
    """

    raw = _str_setting("SOURCE_MODE", DEFAULT_SOURCE_MODE).strip().lower()
    if raw not in SOURCE_MODES:
        raise SettingsError(f"SOURCE_MODE={raw!r} is not one of {', '.join(SOURCE_MODES)}")
    return raw


def veo_extend_long_shots_setting() -> bool:
    """Whether `VEO_EXTEND_LONG_SHOTS` lets 8-15 s shots be an extended Veo clip.

    A plain function for the same reason as `story_skill_setting`: the Veo
    tool and the planner read it without a full settings load.
    """

    return _bool_setting("VEO_EXTEND_LONG_SHOTS", DEFAULT_VEO_EXTEND_LONG_SHOTS)


def image_call_cost_for_provider(provider: str) -> float:
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
    claude_model = _str_setting("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)

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
        max_veo_shots=_max_veo_shots_setting(),
        veo_extend_long_shots=veo_extend_long_shots_setting(),
        image_provider=image_provider,
        image_fallback_provider=_image_fallback_provider_setting(image_provider),
        codex_image_timeout_seconds=_float_setting(
            "CODEX_IMAGE_TIMEOUT_SECONDS", DEFAULT_CODEX_IMAGE_TIMEOUT_SECONDS, positive=True
        ),
        image_call_cost_usd=image_call_cost_for_provider(image_provider),
        veo_call_cost_usd=_float_setting("VEO_CALL_COST_USD", DEFAULT_VEO_CALL_COST_USD),
        claude_shot_attempt_cap_usd=_float_setting(
            "CLAUDE_SHOT_ATTEMPT_CAP_USD", DEFAULT_CLAUDE_SHOT_ATTEMPT_CAP_USD, positive=True
        ),
        veo_concurrency=_int_setting("VEO_CONCURRENCY", DEFAULT_VEO_CONCURRENCY, positive=True),
        gemini_image_concurrency=_int_setting(
            "GEMINI_IMAGE_CONCURRENCY", DEFAULT_GEMINI_IMAGE_CONCURRENCY, positive=True
        ),
        codex_image_concurrency=_int_setting(
            "CODEX_IMAGE_CONCURRENCY", DEFAULT_CODEX_IMAGE_CONCURRENCY, positive=True
        ),
        shot_concurrency=_int_setting("SHOT_CONCURRENCY", DEFAULT_SHOT_CONCURRENCY, positive=True),
        claude_reviewer_cap_usd=_float_setting(
            "CLAUDE_REVIEWER_CAP_USD", DEFAULT_CLAUDE_REVIEWER_CAP_USD, positive=True
        ),
        # Plain os.getenv, not _str_setting: an empty value is the documented
        # "no character" switch, and _str_setting raises on an empty string.
        claude_model=claude_model,
        critic_model=_str_setting("CRITIC_MODEL", claude_model),
        reviewer_model=_str_setting("REVIEWER_MODEL", claude_model),
        story_skill=story_skill_setting(),
        source_mode=source_mode_setting(),
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
