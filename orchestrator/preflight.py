"""Credential/environment preflight (AD-13) and budget gate (AD-7).

Runs before any Gemini/Veo/STT API call, on both a fresh and a resumed
invocation. Returns a structured pass/fail result rather than raising, so
`orchestrator/run.py` can route any failure through the same
failure-report writer every later agent's halt uses (AD-6/NFR5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

import google.auth

from orchestrator.settings import Settings, SettingsError, load_settings

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
    ):
        failure = check()
        if failure is not None:
            return failure

    return PreflightResult(passed=True)
