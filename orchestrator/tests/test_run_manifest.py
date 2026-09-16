from __future__ import annotations

import json

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


def test_load_run_manifest_missing_file_returns_fresh_manifest(tmp_path):
    loaded = load_run_manifest(state_dir=tmp_path / "does-not-exist")
    assert loaded == RunManifest()


def test_load_run_manifest_corrupt_json_falls_back_to_fresh_manifest(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text("{not valid json", encoding="utf-8")

    loaded = load_run_manifest(state_dir=tmp_path)

    assert loaded == RunManifest()


def test_load_run_manifest_non_numeric_budget_falls_back_to_fresh_manifest(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text(
        json.dumps({"budget_spent_usd": "not-a-number"}), encoding="utf-8"
    )

    loaded = load_run_manifest(state_dir=tmp_path)

    assert loaded == RunManifest()


def test_load_run_manifest_nan_budget_falls_back_to_fresh_manifest(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps({"budget_spent_usd": float("nan")}), encoding="utf-8")

    loaded = load_run_manifest(state_dir=tmp_path)

    assert loaded == RunManifest()


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
