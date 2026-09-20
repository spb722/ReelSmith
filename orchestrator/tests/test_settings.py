from __future__ import annotations

import pytest

from orchestrator.settings import (
    DEFAULT_GCS_BUCKET_URI,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_IMAGE_CALL_COST_USD,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_LOCATION,
    DEFAULT_MAX_BUDGET_USD,
    DEFAULT_MAX_VEO_ATTEMPTS,
    DEFAULT_PROJECT_ID,
    DEFAULT_VEO_CALL_COST_USD,
    DEFAULT_VEO_DURATION_SECONDS,
    DEFAULT_VEO_MAX_POLL_SECONDS,
    DEFAULT_VEO_MODEL,
    DEFAULT_VEO_POLL_SECONDS,
    DEFAULT_VEO_RESOLUTION,
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
    "IMAGE_MODEL",
    "VEO_RESOLUTION",
    "VEO_DURATION_SECONDS",
    "VEO_POLL_SECONDS",
    "VEO_MAX_POLL_SECONDS",
    "MAX_VEO_ATTEMPTS",
    "IMAGE_CALL_COST_USD",
    "VEO_CALL_COST_USD",
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
    assert settings.image_model == DEFAULT_IMAGE_MODEL
    assert settings.veo_resolution == DEFAULT_VEO_RESOLUTION
    assert settings.veo_duration_seconds == DEFAULT_VEO_DURATION_SECONDS
    assert settings.veo_poll_seconds == DEFAULT_VEO_POLL_SECONDS
    assert settings.veo_max_poll_seconds == DEFAULT_VEO_MAX_POLL_SECONDS
    assert settings.max_veo_attempts == DEFAULT_MAX_VEO_ATTEMPTS
    assert settings.image_call_cost_usd == DEFAULT_IMAGE_CALL_COST_USD
    assert settings.veo_call_cost_usd == DEFAULT_VEO_CALL_COST_USD


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


def test_veo_generation_settings_are_externally_configurable(monkeypatch):
    monkeypatch.setenv("IMAGE_MODEL", "image-custom")
    monkeypatch.setenv("VEO_RESOLUTION", "1080p")
    monkeypatch.setenv("VEO_DURATION_SECONDS", "6")
    monkeypatch.setenv("VEO_POLL_SECONDS", "2.5")
    monkeypatch.setenv("VEO_MAX_POLL_SECONDS", "100")
    monkeypatch.setenv("MAX_VEO_ATTEMPTS", "4")
    monkeypatch.setenv("IMAGE_CALL_COST_USD", "0.08")
    monkeypatch.setenv("VEO_CALL_COST_USD", "2.4")

    settings = load_settings()
    assert settings.image_model == "image-custom"
    assert settings.veo_resolution == "1080p"
    assert settings.veo_duration_seconds == 6
    assert settings.veo_poll_seconds == 2.5
    assert settings.veo_max_poll_seconds == 100
    assert settings.max_veo_attempts == 4
    assert settings.image_call_cost_usd == 0.08
    assert settings.veo_call_cost_usd == 2.4


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_VEO_ATTEMPTS", "0"),
        ("VEO_DURATION_SECONDS", "not-an-int"),
        ("IMAGE_CALL_COST_USD", "nan"),
        ("VEO_CALL_COST_USD", "-1"),
    ],
)
def test_invalid_veo_settings_fail_loud(name, value, monkeypatch):
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError):
        load_settings()


def test_character_reference_paths_default_to_the_assets_directory(monkeypatch):
    monkeypatch.delenv("CHARACTER_REFERENCE_PATH", raising=False)
    monkeypatch.delenv("CHARACTER_FACE_REFERENCE_PATH", raising=False)
    settings = load_settings()
    assert settings.character_reference_path == "assets/character/character.png"
    assert settings.character_face_reference_path == "assets/character/character_face.png"


def test_character_reference_path_is_env_overridable(monkeypatch):
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", "/tmp/hero.png")
    assert load_settings().character_reference_path == "/tmp/hero.png"


def test_empty_character_reference_path_disables_rather_than_raising(monkeypatch):
    """The documented off switch -- `_str_setting` would raise on this."""
    monkeypatch.setenv("CHARACTER_REFERENCE_PATH", "")
    monkeypatch.setenv("CHARACTER_FACE_REFERENCE_PATH", "")
    settings = load_settings()
    assert settings.character_reference_path == ""
    assert settings.character_face_reference_path == ""
