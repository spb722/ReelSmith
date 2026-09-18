from __future__ import annotations

import json

import pytest

from orchestrator.preflight import check_remotion_delivery_toolchain, parse_bucket_name, run_preflight
from orchestrator.settings import Settings


def make_settings(**overrides) -> Settings:
    base = dict(
        project_id="test-project",
        location="global",
        gcs_bucket_uri="gs://test-bucket/prefix/",
        max_budget_usd=10.0,
        veo_model="veo-model",
        gemini_model="gemini-model",
    )
    base.update(overrides)
    return Settings(**base)


class FakeBucket:
    def __init__(self, exists_result: bool):
        self._exists_result = exists_result

    def exists(self) -> bool:
        return self._exists_result


class FakeStorageClient:
    """Records the bucket name(s) it was asked to check, so tests can
    assert on the actual name parsed out of the configured GCS URI
    rather than only on preflight pass/fail.
    """

    def __init__(self, project_id: str, exists_result: bool = True):
        self.project_id = project_id
        self._exists_result = exists_result
        self.requested_bucket_names: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.requested_bucket_names.append(name)
        return FakeBucket(self._exists_result)


def ok_credentials_factory():
    return ("fake-credentials", "test-project")


def failing_credentials_factory():
    raise RuntimeError("no ADC configured -- run gcloud auth application-default login")


# ---------------------------------------------------------------------------
# I/O matrix scenario 1: missing ADC
# ---------------------------------------------------------------------------


def test_missing_adc_fails_preflight_immediately():
    result = run_preflight(
        settings=make_settings(),
        budget_spent_usd=0.0,
        credentials_factory=failing_credentials_factory,
        storage_client_factory=lambda project_id: FakeStorageClient(project_id, True),
    )

    assert result.passed is False
    assert result.failed_check == "credentials"
    assert result.reason


# ---------------------------------------------------------------------------
# I/O matrix scenario 2: bucket inaccessible (valid ADC, bad bucket)
# ---------------------------------------------------------------------------


def test_bucket_inaccessible_fails_preflight_after_credentials_pass():
    holder: dict[str, FakeStorageClient] = {}

    def factory(project_id: str) -> FakeStorageClient:
        client = FakeStorageClient(project_id, exists_result=False)
        holder["client"] = client
        return client

    result = run_preflight(
        settings=make_settings(gcs_bucket_uri="gs://test-bucket/prefix/"),
        budget_spent_usd=0.0,
        credentials_factory=ok_credentials_factory,
        storage_client_factory=factory,
    )

    assert result.passed is False
    assert result.failed_check == "bucket"
    assert holder["client"].requested_bucket_names == ["test-bucket"]


def test_bucket_check_raises_fails_preflight_with_actual_bucket_name():
    class RaisingStorageClient:
        def __init__(self, project_id):
            self.requested_bucket_names: list[str] = []

        def bucket(self, name):
            self.requested_bucket_names.append(name)
            raise RuntimeError("network unreachable")

    holder: dict[str, RaisingStorageClient] = {}

    def factory(project_id):
        client = RaisingStorageClient(project_id)
        holder["client"] = client
        return client

    result = run_preflight(
        settings=make_settings(gcs_bucket_uri="gs://another-bucket/x/"),
        budget_spent_usd=0.0,
        credentials_factory=ok_credentials_factory,
        storage_client_factory=factory,
    )

    assert result.passed is False
    assert result.failed_check == "bucket"
    assert holder["client"].requested_bucket_names == ["another-bucket"]


# ---------------------------------------------------------------------------
# I/O matrix scenario 3: budget already exhausted
# ---------------------------------------------------------------------------


def test_budget_already_exhausted_halts_before_stage_1():
    result = run_preflight(
        settings=make_settings(max_budget_usd=10.0),
        budget_spent_usd=10.0,
        credentials_factory=ok_credentials_factory,
        storage_client_factory=lambda project_id: FakeStorageClient(project_id, True),
    )

    assert result.passed is False
    assert result.failed_check == "budget"


def test_budget_overspent_halts():
    result = run_preflight(
        settings=make_settings(max_budget_usd=10.0),
        budget_spent_usd=15.0,
        credentials_factory=ok_credentials_factory,
        storage_client_factory=lambda project_id: FakeStorageClient(project_id, True),
    )

    assert result.passed is False
    assert result.failed_check == "budget"


def test_budget_nan_max_budget_fails_safe_not_silently_passes():
    result = run_preflight(
        settings=make_settings(max_budget_usd=float("nan")),
        budget_spent_usd=0.0,
        credentials_factory=ok_credentials_factory,
        storage_client_factory=lambda project_id: FakeStorageClient(project_id, True),
    )

    assert result.passed is False
    assert result.failed_check == "budget"


def test_budget_inf_spent_fails_safe():
    result = run_preflight(
        settings=make_settings(max_budget_usd=10.0),
        budget_spent_usd=float("inf"),
        credentials_factory=ok_credentials_factory,
        storage_client_factory=lambda project_id: FakeStorageClient(project_id, True),
    )

    assert result.passed is False
    assert result.failed_check == "budget"


# ---------------------------------------------------------------------------
# I/O matrix scenario 4: all checks pass
# ---------------------------------------------------------------------------


def test_all_checks_pass():
    holder: dict[str, FakeStorageClient] = {}

    def factory(project_id: str) -> FakeStorageClient:
        client = FakeStorageClient(project_id, exists_result=True)
        holder["client"] = client
        return client

    result = run_preflight(
        settings=make_settings(gcs_bucket_uri="gs://test-bucket/prefix/", max_budget_usd=10.0),
        budget_spent_usd=1.0,
        credentials_factory=ok_credentials_factory,
        storage_client_factory=factory,
    )

    assert result.passed is True
    assert result.failed_check is None
    assert result.reason is None
    assert holder["client"].requested_bucket_names == ["test-bucket"]


# ---------------------------------------------------------------------------
# I/O matrix scenario 5: resumed invocation still runs preflight
# ---------------------------------------------------------------------------


def test_resumed_invocation_still_executes_every_check():
    call_counts = {"credentials": 0, "bucket": 0}

    def counting_credentials_factory():
        call_counts["credentials"] += 1
        return ("fake", "test-project")

    def counting_storage_factory(project_id):
        call_counts["bucket"] += 1
        return FakeStorageClient(project_id, exists_result=True)

    settings = make_settings()

    # Simulate a fresh invocation, then a "resumed" re-invocation -- the
    # preflight call is never skipped just because it isn't literally
    # the first call of the process (AD-13).
    for _ in range(2):
        result = run_preflight(
            settings=settings,
            budget_spent_usd=0.0,
            credentials_factory=counting_credentials_factory,
            storage_client_factory=counting_storage_factory,
        )
        assert result.passed is True

    assert call_counts["credentials"] == 2
    assert call_counts["bucket"] == 2


# ---------------------------------------------------------------------------
# parse_bucket_name
# ---------------------------------------------------------------------------


def test_parse_bucket_name_extracts_name_from_gs_uri():
    assert parse_bucket_name("gs://test-bucket/book_reels/veo/") == "test-bucket"


def test_parse_bucket_name_rejects_non_gs_scheme():
    with pytest.raises(ValueError):
        parse_bucket_name("https://example.com/test-bucket")


def test_parse_bucket_name_rejects_empty_bucket_name():
    with pytest.raises(ValueError):
        parse_bucket_name("gs://")


# ---------------------------------------------------------------------------
# orchestrator.run CLI: preflight-first-then-halt-via-failure-report
# ---------------------------------------------------------------------------


def test_run_main_writes_failure_report_on_preflight_failure(tmp_path, monkeypatch):
    import orchestrator.run as run_module
    from orchestrator.preflight import PreflightResult
    from orchestrator.settings import Settings as SettingsCls

    monkeypatch.setattr(run_module, "RUN_STATE_DIR", tmp_path)
    monkeypatch.setattr(
        run_module,
        "load_settings",
        lambda: SettingsCls(
            project_id="p",
            location="l",
            gcs_bucket_uri="gs://b/",
            max_budget_usd=1.0,
            veo_model="v",
            gemini_model="g",
        ),
    )
    monkeypatch.setattr(
        run_module,
        "run_preflight",
        lambda settings, budget_spent_usd: PreflightResult(
            passed=False, failed_check="bucket", reason="bucket unreachable"
        ),
    )

    exit_code = run_module.main([str(tmp_path / "source_images")])

    assert exit_code == 1

    failure_files = sorted(tmp_path.glob("failure_preflight_*.json"))
    assert len(failure_files) == 1

    data = json.loads(failure_files[0].read_text(encoding="utf-8"))
    assert data["failed_contract_name"] == "bucket"
    assert data["reason"] == "bucket unreachable"
    assert data["attempt_count"] == 1
    assert data["partial_artifact_paths"] == []
    assert data["stage_id"] == "preflight"
    assert "timestamp" in data


def test_run_main_halts_via_failure_report_on_malformed_budget_env(tmp_path, monkeypatch):
    import orchestrator.run as run_module

    monkeypatch.setattr(run_module, "RUN_STATE_DIR", tmp_path)
    monkeypatch.setenv("MAX_BUDGET_USD", "not-a-number")

    exit_code = run_module.main([str(tmp_path / "source_images")])

    assert exit_code == 1

    failure_files = sorted(tmp_path.glob("failure_settings_*.json"))
    assert len(failure_files) == 1

    data = json.loads(failure_files[0].read_text(encoding="utf-8"))
    assert data["stage_id"] == "settings"
    assert data["failed_contract_name"] == "Settings"


def test_run_main_halts_on_exhausted_budget_from_real_preflight(tmp_path, monkeypatch):
    """Exercises the real (unmocked) run_preflight through orchestrator.run.main(),
    with a persisted manifest whose budget_spent_usd is already at the ceiling --
    guards against a regression that hardcodes budget_spent_usd in run.py.

    Only load_settings and the low-level ADC/GCS I/O (google.auth.default,
    google.cloud.storage.Client) are faked so credentials/bucket pass
    deterministically without a real network call; run_preflight and
    run.main() themselves run for real.
    """
    import google.auth
    import google.cloud.storage as gcs_storage

    import orchestrator.run as run_module
    from orchestrator.settings import Settings as SettingsCls
    from orchestrator.state.run_manifest import RunManifest, save_run_manifest

    monkeypatch.setattr(google.auth, "default", lambda scopes=None: ("fake", "p"))

    class FakeBucket:
        def exists(self) -> bool:
            return True

    class FakeClient:
        def __init__(self, project=None):
            self.project = project

        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr(gcs_storage, "Client", FakeClient)

    monkeypatch.setattr(run_module, "RUN_STATE_DIR", tmp_path)
    monkeypatch.setattr(
        run_module,
        "load_settings",
        lambda: SettingsCls(
            project_id="p",
            location="l",
            gcs_bucket_uri="gs://b/",
            max_budget_usd=5.0,
            veo_model="v",
            gemini_model="g",
        ),
    )

    save_run_manifest(RunManifest(budget_spent_usd=5.0), state_dir=tmp_path)

    exit_code = run_module.main([str(tmp_path / "source_images")])

    assert exit_code == 1

    failure_files = sorted(tmp_path.glob("failure_preflight_*.json"))
    assert len(failure_files) == 1

    data = json.loads(failure_files[0].read_text(encoding="utf-8"))
    assert data["failed_contract_name"] == "budget"


def test_run_main_returns_zero_when_preflight_passes(tmp_path, monkeypatch, capsys):
    import orchestrator.run as run_module
    from orchestrator.preflight import PreflightResult
    from orchestrator.settings import Settings as SettingsCls

    monkeypatch.setattr(run_module, "RUN_STATE_DIR", tmp_path)
    monkeypatch.setattr(
        run_module,
        "load_settings",
        lambda: SettingsCls(
            project_id="p",
            location="l",
            gcs_bucket_uri="gs://b/",
            max_budget_usd=1.0,
            veo_model="v",
            gemini_model="g",
        ),
    )
    monkeypatch.setattr(
        run_module,
        "run_preflight",
        lambda settings, budget_spent_usd: PreflightResult(passed=True),
    )

    stage_order = []

    async def successful_stage(source_images_dir, settings, manifest):
        stage_order.append("screenshot")
        return 0

    async def successful_narration_stage(settings, manifest):
        stage_order.append("narration")
        return 0

    async def successful_visual_stage(settings, manifest):
        stage_order.append("visual")
        return 0

    async def successful_voice_stage(settings, manifest):
        stage_order.append("voice")
        return 0

    async def successful_veo_stage(settings, manifest):
        stage_order.append("veo")
        return 0

    async def successful_stills_stage(settings, manifest):
        stage_order.append("stills")
        return 0

    async def successful_delivery_stage(settings, manifest):
        stage_order.append("delivery")
        return 0

    monkeypatch.setattr(run_module, "run_screenshot_stage", successful_stage)
    monkeypatch.setattr(run_module, "run_narration_stage", successful_narration_stage)
    monkeypatch.setattr(run_module, "run_visual_stage", successful_visual_stage)
    monkeypatch.setattr(run_module, "run_voice_stage", successful_voice_stage)
    monkeypatch.setattr(run_module, "run_veo_stage", successful_veo_stage)
    monkeypatch.setattr(run_module, "run_stills_stage", successful_stills_stage)
    monkeypatch.setattr(run_module, "run_delivery_stage", successful_delivery_stage)

    exit_code = run_module.main([str(tmp_path / "source_images")])

    assert exit_code == 0
    assert stage_order == ["screenshot", "narration", "visual", "voice", "veo", "stills", "delivery"]
    assert not list(tmp_path.glob("failure_*.json"))

    async def failed_veo_stage(settings, manifest):
        return 1

    monkeypatch.setattr(run_module, "run_veo_stage", failed_veo_stage)
    assert run_module.main([str(tmp_path / "source_images")]) == 1


def test_check_remotion_delivery_toolchain_missing_npx(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.preflight.shutil.which", lambda _: None)
    result = check_remotion_delivery_toolchain()
    assert result is not None
    assert result.passed is False
    assert result.failed_check == "remotion"
    assert "npx" in (result.reason or "")


def test_check_remotion_delivery_toolchain_missing_package_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.preflight.shutil.which", lambda _: "/usr/bin/npx")
    result = check_remotion_delivery_toolchain()
    assert result is not None
    assert result.failed_check == "remotion"
    assert "package.json" in (result.reason or "").lower() or "Remotion project" in (result.reason or "")


def test_check_remotion_delivery_toolchain_missing_node_modules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from orchestrator.preflight import REMOTION_DIR

    REMOTION_DIR.mkdir(parents=True)
    (REMOTION_DIR / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("orchestrator.preflight.shutil.which", lambda _: "/usr/bin/npx")
    result = check_remotion_delivery_toolchain()
    assert result is not None
    assert result.failed_check == "remotion"
    assert "node_modules" in (result.reason or "")
