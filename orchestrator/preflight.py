"""Credential/environment preflight (AD-13) and budget gate (AD-7).

Runs before any Gemini/Veo/STT API call, on both a fresh and a resumed
invocation. Returns a structured pass/fail result rather than raising, so
`orchestrator/run.py` can route any failure through the same
failure-report writer every later agent's halt uses (AD-6/NFR5).
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import google.auth

from orchestrator.settings import (
    DEFAULT_CHARACTER_FACE_REFERENCE_PATH,
    DEFAULT_CHARACTER_REFERENCE_PATH,
    Settings,
    SettingsError,
    load_settings,
)

REMOTION_DIR = Path("remotion")
# Two base64 image blocks ride back to the agent inside its 20MB buffer;
# a reference much larger than this is a mistake, not a high-quality sheet.
MAX_CHARACTER_REFERENCE_BYTES = 4 * 1024 * 1024

CredentialsFactory = Callable[[], object]
StorageClientFactory = Callable[[str], object]


@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    failed_check: Optional[str] = None
    reason: Optional[str] = None


def parse_bucket_name(gcs_uri: str) -> str:
    """Parse the bucket name out of a `gs://bucket/prefix...` URI.

    Raises `ValueError` if the URI doesn't use the `gs://` scheme, or if
    no bucket name is present after the scheme.
    """

    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"GCS URI must start with 'gs://': {gcs_uri!r}")

    remainder = gcs_uri[len("gs://"):]
    bucket_name = remainder.split("/", 1)[0]

    if not bucket_name:
        raise ValueError(f"GCS URI has no bucket name: {gcs_uri!r}")

    return bucket_name


def _default_credentials_factory():
    # Reuse the existing ADC pattern from extract_word_timing.py exactly.
    return google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )


def _default_storage_client_factory(project_id: str):
    from google.cloud import storage

    return storage.Client(project=project_id)


def _check_credentials(
    credentials_factory: CredentialsFactory,
) -> Optional[PreflightResult]:
    try:
        credentials_factory()
    except Exception as exc:  # noqa: BLE001 - any ADC failure fails preflight
        return PreflightResult(
            passed=False,
            failed_check="credentials",
            reason=f"Google Cloud ADC credentials check failed: {exc}",
        )

    return None


def _check_bucket(
    settings: Settings,
    storage_client_factory: StorageClientFactory,
) -> Optional[PreflightResult]:
    try:
        bucket_name = parse_bucket_name(settings.gcs_bucket_uri)
    except ValueError as exc:
        return PreflightResult(passed=False, failed_check="bucket", reason=str(exc))

    try:
        client = storage_client_factory(settings.project_id)
        exists = client.bucket(bucket_name).exists()
    except Exception as exc:  # noqa: BLE001 - any GCS failure fails preflight
        return PreflightResult(
            passed=False,
            failed_check="bucket",
            reason=f"GCS bucket {bucket_name!r} check failed: {exc}",
        )

    if not exists:
        return PreflightResult(
            passed=False,
            failed_check="bucket",
            reason=f"GCS bucket {bucket_name!r} is not reachable or accessible",
        )

    return None


def _check_budget(
    settings: Settings,
    budget_spent_usd: float,
) -> Optional[PreflightResult]:
    max_budget = settings.max_budget_usd

    if not math.isfinite(max_budget) or not math.isfinite(budget_spent_usd):
        return PreflightResult(
            passed=False,
            failed_check="budget",
            reason=(
                "Budget values must be finite numbers "
                f"(max_budget_usd={max_budget!r}, budget_spent_usd={budget_spent_usd!r})"
            ),
        )

    remaining = max_budget - budget_spent_usd

    if remaining <= 0:
        return PreflightResult(
            passed=False,
            failed_check="budget",
            reason=(
                f"Budget exhausted: spent {budget_spent_usd} of max {max_budget}"
            ),
        )

    return None


def _character_reference_failure(
    raw_path: str, default_path: str, label: str
) -> Optional[PreflightResult]:
    """Validate one configured character reference image.

    An empty value means the feature is off. A missing file at the *default*
    path also means off -- that is the state of every repo that never adopted
    a character. A missing file at an *explicit* path is a typo and must be
    loud rather than silently dropping the character.
    """

    raw = raw_path.strip()
    if not raw:
        return None

    path = Path(raw)
    if not path.is_file():
        if raw == default_path:
            return None
        return PreflightResult(
            passed=False,
            failed_check="character_reference",
            reason=f"{label} is configured as {raw!r} but no file exists there",
        )

    if path.suffix.lower() != ".png":
        return PreflightResult(
            passed=False,
            failed_check="character_reference",
            reason=f"{label} must be a .png (the tool result declares image/png), got {path.name!r}",
        )

    size = path.stat().st_size
    if size == 0:
        return PreflightResult(
            passed=False, failed_check="character_reference", reason=f"{label} {raw!r} is empty"
        )
    if size > MAX_CHARACTER_REFERENCE_BYTES:
        return PreflightResult(
            passed=False,
            failed_check="character_reference",
            reason=(
                f"{label} {raw!r} is {size} bytes, over the "
                f"{MAX_CHARACTER_REFERENCE_BYTES}-byte limit -- export it around 1024px tall"
            ),
        )

    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        return PreflightResult(
            passed=False,
            failed_check="character_reference",
            reason=f"{label} {raw!r} is not a readable image: {type(exc).__name__}: {exc}",
        )

    return None


def _check_character_reference(settings: Settings) -> Optional[PreflightResult]:
    """Fail before any paid call when a configured character reference is unusable."""

    body_failure = _character_reference_failure(
        settings.character_reference_path,
        DEFAULT_CHARACTER_REFERENCE_PATH,
        "Character reference",
    )
    if body_failure is not None:
        return body_failure

    return _character_reference_failure(
        settings.character_face_reference_path,
        DEFAULT_CHARACTER_FACE_REFERENCE_PATH,
        "Character face reference",
    )


def check_remotion_delivery_toolchain() -> PreflightResult | None:
    """Return a failed `PreflightResult` when Remotion cannot render, else None."""
    if shutil.which("npx") is None:
        return PreflightResult(
            passed=False,
            failed_check="remotion",
            reason="npx is not available on PATH -- install Node.js/npm to render with Remotion",
        )
    if not (REMOTION_DIR / "package.json").is_file():
        return PreflightResult(
            passed=False,
            failed_check="remotion",
            reason=f"Remotion project not found at {REMOTION_DIR.resolve()}",
        )
    if not (REMOTION_DIR / "node_modules").is_dir():
        return PreflightResult(
            passed=False,
            failed_check="remotion",
            reason=(
                f"Remotion dependencies are missing ({REMOTION_DIR / 'node_modules'}); "
                "run npm install in remotion/"
            ),
        )
    return None


def run_preflight(
    settings: Optional[Settings] = None,
    budget_spent_usd: float = 0.0,
    credentials_factory: CredentialsFactory = _default_credentials_factory,
    storage_client_factory: StorageClientFactory = _default_storage_client_factory,
) -> PreflightResult:
    """Run the ordered credentials -> bucket -> budget preflight check.

    Stops at the first failure. Never skipped, whether this is a fresh
    invocation or a resumed one (AD-13).
    """

    if settings is None:
        try:
            settings = load_settings()
        except SettingsError as exc:
            return PreflightResult(
                passed=False, failed_check="settings", reason=str(exc)
            )

    for check in (
        lambda: _check_credentials(credentials_factory),
        lambda: _check_bucket(settings, storage_client_factory),
        lambda: _check_budget(settings, budget_spent_usd),
        lambda: _check_character_reference(settings),
    ):
        failure = check()
        if failure is not None:
            return failure

    return PreflightResult(passed=True)
