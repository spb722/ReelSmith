from __future__ import annotations

import json

from orchestrator.settings import DEFAULT_MAX_ATTEMPTS
from orchestrator.state.run_manifest import (
    RunManifest,
    build_failure_report,
    load_run_manifest,
    save_run_manifest,
    write_failure_report,
)


def test_save_and_load_run_manifest_round_trip(tmp_path):
    manifest = RunManifest(
        budget_spent_usd=3.25,
        iteration_counts={"story": 2},
        session_id="abc123",
    )

    save_run_manifest(manifest, state_dir=tmp_path)
    loaded = load_run_manifest(state_dir=tmp_path)

    assert loaded == manifest


def fresh_manifest() -> RunManifest:
    """What a fallback produces: no usage, every ceiling at its default."""

    return RunManifest(max_attempts=dict(DEFAULT_MAX_ATTEMPTS))


def test_load_run_manifest_missing_file_returns_fresh_manifest(tmp_path):
    loaded = load_run_manifest(state_dir=tmp_path / "does-not-exist")
    assert loaded == fresh_manifest()


def test_load_run_manifest_corrupt_json_falls_back_to_fresh_manifest(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text("{not valid json", encoding="utf-8")

    loaded = load_run_manifest(state_dir=tmp_path)

    assert loaded == fresh_manifest()


def test_load_run_manifest_non_numeric_budget_falls_back_to_fresh_manifest(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text(
        json.dumps({"budget_spent_usd": "not-a-number"}), encoding="utf-8"
    )

    loaded = load_run_manifest(state_dir=tmp_path)

    assert loaded == fresh_manifest()


def test_load_run_manifest_nan_budget_falls_back_to_fresh_manifest(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps({"budget_spent_usd": float("nan")}), encoding="utf-8")

    loaded = load_run_manifest(state_dir=tmp_path)

    assert loaded == fresh_manifest()


def test_save_run_manifest_writes_atomically_no_leftover_tmp_file(tmp_path):
    manifest = RunManifest(budget_spent_usd=1.0)

    path = save_run_manifest(manifest, state_dir=tmp_path)

    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_write_failure_report_is_atomic_and_content_is_correct(tmp_path):
    report = build_failure_report(
        stage_id="preflight",
        failed_contract_name="bucket",
        reason="bucket unreachable",
        attempt_count=1,
        partial_artifact_paths=["metadata/partial.json"],
    )

    path = write_failure_report(report, output_dir=tmp_path)

    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["stage_id"] == "preflight"
    assert data["failed_contract_name"] == "bucket"
    assert data["reason"] == "bucket unreachable"
    assert data["attempt_count"] == 1
    assert data["partial_artifact_paths"] == ["metadata/partial.json"]
    assert "timestamp" in data


# --- the operator-editable config -----------------------------------------
# One file holds both halves: `limits` you edit to configure, `usage` you edit
# to reset. Before this, a stuck counter could only be cleared by hand-patching
# machine-written state.


def test_saving_writes_limits_and_usage_separately(tmp_path):
    m = RunManifest(budget_spent_usd=3.31, iteration_counts={"veo_agent_shot_3": 1})
    path = save_run_manifest(m, state_dir=tmp_path)

    assert path.name == "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["usage"]["budget_spent_usd"] == 3.31
    assert data["usage"]["attempts_used"] == {"veo_agent_shot_3": 1}
    # Every ceiling is written out, defaults included, so the file shows every
    # knob rather than only the ones already changed.
    assert data["limits"]["max_attempts"] == DEFAULT_MAX_ATTEMPTS


def test_editing_a_ceiling_in_the_file_is_honoured(tmp_path):
    """The whole point: raise a limit by editing the file, not the code."""
    save_run_manifest(RunManifest(), state_dir=tmp_path)
    path = tmp_path / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["limits"]["max_attempts"]["veo_agent"] = 7
    path.write_text(json.dumps(data), encoding="utf-8")

    assert load_run_manifest(state_dir=tmp_path).attempts_allowed("veo_agent") == 7


def test_resetting_a_counter_in_the_file_is_honoured(tmp_path):
    """And the other half: clear a stuck counter by editing the same file."""
    save_run_manifest(
        RunManifest(budget_spent_usd=30.0, iteration_counts={"veo_agent_shot_3": 3}),
        state_dir=tmp_path,
    )
    path = tmp_path / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["usage"]["attempts_used"]["veo_agent_shot_3"] = 0
    data["usage"]["budget_spent_usd"] = 3.31
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_run_manifest(state_dir=tmp_path)
    assert loaded.iteration_counts["veo_agent_shot_3"] == 0
    assert loaded.budget_spent_usd == 3.31


def test_a_ceiling_left_out_of_the_file_falls_back_to_its_default(tmp_path):
    """A hand-edited config that drops a key must still run."""
    (tmp_path / "config.json").write_text(
        json.dumps({"limits": {"max_attempts": {"veo_agent": 9}}, "usage": {}}),
        encoding="utf-8",
    )
    loaded = load_run_manifest(state_dir=tmp_path)
    assert loaded.attempts_allowed("veo_agent") == 9
    assert loaded.attempts_allowed("stills_agent") == DEFAULT_MAX_ATTEMPTS["stills_agent"]


def test_an_old_run_manifest_is_read_so_a_live_reel_keeps_resuming(tmp_path):
    """Projects made before config.json existed must not lose their state."""
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"budget_spent_usd": 6.85, "iteration_counts": {"veo_agent_shot_3": 3}}),
        encoding="utf-8",
    )
    loaded = load_run_manifest(state_dir=tmp_path)
    assert loaded.budget_spent_usd == 6.85
    assert loaded.iteration_counts == {"veo_agent_shot_3": 3}
    assert loaded.attempts_allowed("veo_agent") == DEFAULT_MAX_ATTEMPTS["veo_agent"]


def test_config_wins_over_a_leftover_run_manifest(tmp_path):
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"budget_spent_usd": 99.0}), encoding="utf-8")
    save_run_manifest(RunManifest(budget_spent_usd=1.0), state_dir=tmp_path)

    assert load_run_manifest(state_dir=tmp_path).budget_spent_usd == 1.0
