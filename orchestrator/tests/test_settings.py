from __future__ import annotations

import pytest

from orchestrator.settings import (
    DEFAULT_GCS_BUCKET_URI,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LOCATION,
    DEFAULT_MAX_BUDGET_USD,
    DEFAULT_PROJECT_ID,
    DEFAULT_VEO_MODEL,
    SettingsError,
    load_settings,
)

ENV_VARS = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GCS_BUCKET_URI",
    "MAX_BUDGET_USD",
    "VEO_MODEL",
    "GEMINI_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_settings_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_no_env_vars_set():
    settings = load_settings()

    assert settings.project_id == DEFAULT_PROJECT_ID
    assert settings.location == DEFAULT_LOCATION
    assert settings.gcs_bucket_uri == DEFAULT_GCS_BUCKET_URI
    assert settings.max_budget_usd == DEFAULT_MAX_BUDGET_USD
    assert settings.veo_model == DEFAULT_VEO_MODEL
    assert settings.gemini_model == DEFAULT_GEMINI_MODEL


def test_project_id_env_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    assert load_settings().project_id == "my-project"


def test_location_env_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    assert load_settings().location == "us-central1"


def test_gcs_bucket_uri_env_override(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET_URI", "gs://my-bucket/prefix/")
    assert load_settings().gcs_bucket_uri == "gs://my-bucket/prefix/"


def test_max_budget_usd_env_override(monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "42.5")
    assert load_settings().max_budget_usd == 42.5


def test_veo_model_env_override(monkeypatch):
    monkeypatch.setenv("VEO_MODEL", "veo-custom")
    assert load_settings().veo_model == "veo-custom"


def test_gemini_model_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-custom")
    assert load_settings().gemini_model == "gemini-custom"


def test_malformed_max_budget_usd_raises_settings_error_not_value_error(monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "not-a-number")

    with pytest.raises(SettingsError):
        load_settings()


def test_malformed_max_budget_usd_never_raises_bare_value_error(monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "not-a-number")

    try:
        load_settings()
        assert False, "expected SettingsError"
    except SettingsError:
        pass
    except ValueError:
        pytest.fail("load_settings() leaked an uncaught bare ValueError")
