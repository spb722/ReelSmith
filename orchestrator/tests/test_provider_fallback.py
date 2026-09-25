"""A shot whose images keep failing on Gemini gets a fresh round on Codex."""

import asyncio
import json

import pytest

import orchestrator.run as run
from orchestrator.settings import SettingsError, load_settings
from orchestrator.state.production_assets import load_production_assets
from orchestrator.state.run_manifest import load_run_manifest, save_run_manifest
from orchestrator.tests.test_full_chain_resume import (  # noqa: F401 -- `chain` is a fixture
    approved_still_outcome_for,
    chain,
    failed_still_outcome_for,
    still_visual_plan_for,
    subtitle_cues_for,
)
from orchestrator.tests.test_guard_hooks import result_message
from orchestrator.tests.test_preflight import make_settings as _make_settings


def make_settings(**overrides):
    return _make_settings(**{"claude_shot_attempt_cap_usd": 1.0, "claude_reviewer_cap_usd": 0.0, **overrides})


def write_plan(asset_id):
    plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    return plan


def gemini_fails_codex_draws(plan, providers):
    async def agent(**kwargs):
        provider = kwargs["settings"]["image_provider"]
        providers.append(provider)
        shot = plan.shots[0]
        outcome = (
            approved_still_outcome_for(shot) if provider == "codex"
            else failed_still_outcome_for(shot, retryable=True, reason="card panel still visible")
        )
        return result_message(outcome.model_dump(mode="json"), cost=0.1)

    return agent


def production(settings):
    return asyncio.run(run.run_production_stage(settings, load_run_manifest(run.RUN_STATE_DIR)))


def test_exhausted_gemini_shot_gets_a_fresh_round_on_codex(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(asset_id)
    providers = []
    monkeypatch.setattr(run, "generate_still_asset", gemini_fails_codex_draws(plan, providers))

    assert production(make_settings(image_fallback_provider="codex")) == 0

    assert providers == ["gemini", "gemini", "gemini", "codex"]
    assert "1" in load_production_assets(run.PRODUCTION_ASSETS_FILE).shots
    assert load_run_manifest(run.RUN_STATE_DIR).iteration_counts["stills_agent_shot_1"] == 1
    history = run.PROVIDER_FALLBACK_HISTORY_DIR / "stills_agent_shot_1"
    assert sorted(p.name for p in history.iterdir()) == [
        f"stills_agent_shot_1_attempt_{n}.json" for n in (1, 2, 3)
    ]
    assert json.loads(run._shot_provider_file("stills_agent", 1).read_text()) == {"image_provider": "codex"}


def test_without_a_fallback_the_shot_fails_as_before(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(asset_id)
    providers = []
    monkeypatch.setattr(run, "generate_still_asset", gemini_fails_codex_draws(plan, providers))

    assert production(make_settings()) == 1

    assert providers == ["gemini"] * 3
    assert not run._shot_provider_file("stills_agent", 1).exists()


def test_a_shot_exhausted_in_an_earlier_run_falls_back_on_resume(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(asset_id)
    monkeypatch.setattr(run, "generate_still_asset", gemini_fails_codex_draws(plan, []))
    assert production(make_settings()) == 1  # an earlier run, before the fallback was configured

    providers = []
    monkeypatch.setattr(run, "generate_still_asset", gemini_fails_codex_draws(plan, providers))
    assert production(make_settings(image_fallback_provider="codex")) == 0

    assert providers == ["codex"]


def test_a_resumed_codex_round_stays_on_codex(chain, monkeypatch):
    _, asset_id = chain
    plan = write_plan(asset_id)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    manifest.iteration_counts["stills_agent_shot_1"] = 1
    save_run_manifest(manifest, state_dir=run.RUN_STATE_DIR)
    run._shot_provider_file("stills_agent", 1).parent.mkdir(parents=True, exist_ok=True)
    run._shot_provider_file("stills_agent", 1).write_text(json.dumps({"image_provider": "codex"}))
    providers = []
    monkeypatch.setattr(run, "generate_still_asset", gemini_fails_codex_draws(plan, providers))

    assert production(make_settings(image_fallback_provider="codex")) == 0

    assert providers == ["codex"]


@pytest.mark.parametrize("stage, falls_back", [
    ("still_qa", True), ("seed_qa", True), ("seed_generation", True), ("clip_qa", False), ("veo_generation", False),
])
def test_only_image_stage_failures_fall_back(stage, falls_back):
    failure = run.ShotFailure(1, "veo_agent", "VeoFailureContract", "x", shot_specific=True, failure_stage=stage)
    settings = make_settings(image_fallback_provider="codex")
    assert run._should_fall_back(failure, settings, settings, "veo_agent", 1) is falls_back


def test_fallback_setting_is_validated(monkeypatch):
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
    for value, expected in [("", ""), ("none", ""), ("Codex", "codex")]:
        monkeypatch.setenv("IMAGE_FALLBACK_PROVIDER", value)
        assert load_settings().image_fallback_provider == expected
    for bad in ("gemini", "dalle"):
        monkeypatch.setenv("IMAGE_FALLBACK_PROVIDER", bad)
        with pytest.raises(SettingsError):
            load_settings()
