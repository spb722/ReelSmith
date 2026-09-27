"""Story 4.2: the paid-tool budget guard and vendor-limit classification.

No live vendor calls. Agent calls are replaced by fakes that drive the guard's
hooks the way the CLI would, dispatching by each `HookMatcher`'s tool name.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest
from claude_agent_sdk import ResultMessage
from pydantic import BaseModel

import orchestrator.agents.stills_agent as stills_agent_module
import orchestrator.agents.veo_agent as veo_agent_module
import orchestrator.run as run
from orchestrator.agents._guard import STILL_TOOL, VEO_CLIP_TOOL, VEO_SEED_TOOL, PaidToolGuard
from orchestrator.agents._skills import PLUGIN_DIR
from orchestrator.contracts.visual_plan import Shot
from orchestrator.state.production_assets import load_production_assets
from orchestrator.state.run_manifest import FailureReport, RunManifest, load_run_manifest
from orchestrator.tests.test_full_chain_resume import (  # noqa: F401 -- `chain` is a fixture
    approved_outcome_for,
    approved_still_outcome_for,
    chain,
    failed_outcome_for,
    failed_still_outcome_for,
    still_visual_plan_for,
    subtitle_cues_for,
    veo_visual_plan_for,
)
from orchestrator.tests.test_preflight import make_settings


# --- helpers ---------------------------------------------------------------

def result_message(output=None, *, cost=0.1, is_error=False, subtype="success",
                   result=None, errors=None, api_error_status=None) -> ResultMessage:
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
        num_turns=1, session_id="s", total_cost_usd=cost, result=result,
        structured_output=output, errors=errors, api_error_status=api_error_status,
    )


async def fire(hooks: dict, event: str, tool_name: str, **fields) -> list[dict]:
    """Call every hook bound to `event` for this tool, as the CLI would."""

    payload = {"hook_event_name": event, "tool_name": tool_name, "cwd": "/tmp", **fields}
    outputs = []
    for matcher in hooks.get(event, []):
        if matcher.matcher in (None, tool_name):
            for hook in matcher.hooks:
                outputs.append(await hook(payload, "tool-use-id", None))
    return outputs


def denied(outputs: list[dict]) -> bool:
    return any(
        (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny" for out in outputs
    )


def blocks(payload: dict) -> list[dict]:
    """`tool_response` as spike 4.1 (g) recorded it: a list of content blocks."""

    return [{"type": "text", "text": json.dumps(payload)}]


async def call_paid(guard: PaidToolGuard, tool: str, response: dict, *, tool_input=None) -> bool:
    """One paid call: PreToolUse, then PostToolUse if it was allowed."""

    hooks = guard.hooks()
    if denied(await fire(hooks, "PreToolUse", tool, tool_input=tool_input or {})):
        return False
    await fire(hooks, "PostToolUse", tool, tool_input=tool_input or {}, tool_response=blocks(response))
    return True


SEED_OK = {"shot_sequence": 1, "local_path": "generated/veo_seeds/shot_01_seed.png"}
CLIP_OK = {"status": "SUCCESS", "result": {"local_video_path": "generated/veo/shot_01.mp4"}}


def clip_failure(code: str, reason: str, *, dump: str | None = None) -> dict:
    return {"status": "FAILURE", "failure": {"code": code, "reason": reason, "operation_dump_path": dump}}


def image_failure(reason: str) -> dict:
    return {"failure": {"code": "SEED_GENERATION_FAILED", "reason": reason}}


def reports(agent: str) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(run.RUN_STATE_DIR.glob(f"failure_{agent}_*.json"))
    ]


def setup_veo(asset_id: str):
    plan = veo_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    return plan.shots[0]


def setup_stills(asset_id: str):
    plan = still_visual_plan_for(asset_id)
    run.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")
    return plan.shots[0]


# --- the classifier --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "You've hit your usage limit. Upgrade to Pro",
    "usage_limit_reached",
    "You've hit your session limit · resets 3am",
    "You've hit your org's monthly spend limit",
    "service_spend_limit_reached",
    "429 RESOURCE_EXHAUSTED. Quota exceeded for aiplatform.googleapis.com",
    "Invalid credentials supplied",
    "401 Unauthorized",
])
def test_hard_markers(text):
    assert run.classify_vendor_refusal(text) == "hard"


@pytest.mark.parametrize("text", ["Rate limit reached for requests", "rate_limit_error"])
def test_transient_markers(text):
    assert run.classify_vendor_refusal(text) == "transient"


@pytest.mark.parametrize("status,expected", [(429, "transient"), (401, "hard"), (403, "hard"), (500, "none")])
def test_http_statuses(status, expected):
    assert run.classify_vendor_refusal("something went wrong", status) == expected


def test_google_shared_capacity_429_is_transient_despite_saying_quota():
    text = (
        "SEED_GENERATION_FAILED: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
        "'message': 'Resource has been exhausted (e.g. check quota).', 'status': 'RESOURCE_EXHAUSTED'}}"
    )
    assert run.classify_vendor_refusal(text, 429) == "transient"
    # a spent fixed quota stays hard
    assert run.classify_vendor_refusal("429 RESOURCE_EXHAUSTED. Quota exceeded for aiplatform", 429) == "hard"


def test_a_hard_marker_beats_a_429():
    # Usage and session limits arrive as 429s; they must not be retried.
    assert run.classify_vendor_refusal("You've hit your session limit", 429) == "hard"


@pytest.mark.parametrize("text", [
    "Veo safety filtering rejected one or more generated media outputs",
    "Codex returned a 1080x1920 image but the reel needs vertical 9:16",
    "The seed kept the app UI",
    "",
    None,
])
def test_non_matching_text_is_none(text):
    assert run.classify_vendor_refusal(text) == "none"


# --- the guard itself ------------------------------------------------------

def test_guard_denies_a_second_seed_the_budget_cannot_cover():
    guard = PaidToolGuard(limit_usd=0.05, costs={VEO_SEED_TOOL: 0.04, VEO_CLIP_TOOL: 1.2})

    async def scenario():
        first = await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        second = await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        return first, second

    assert asyncio.run(scenario()) == (True, False)
    assert guard.allowed_usd == pytest.approx(0.04)
    assert guard.billed_usd == pytest.approx(0.04)
    assert "already allowed" in guard.denial


def test_guard_allows_calls_the_budget_covers_and_ignores_other_tools():
    guard = PaidToolGuard(limit_usd=1.24, costs={VEO_SEED_TOOL: 0.04, VEO_CLIP_TOOL: 1.2})

    async def scenario():
        other = await fire(guard.hooks(), "PreToolUse", "Read", tool_input={"file_path": "/x"})
        return other, await call_paid(guard, VEO_SEED_TOOL, SEED_OK), await call_paid(guard, VEO_CLIP_TOOL, CLIP_OK)

    other, seed, clip = asyncio.run(scenario())
    assert other == [] and seed and clip
    assert guard.denial is None
    assert guard.billed_usd == pytest.approx(1.24)


def test_post_tool_use_reads_the_failure_contract_from_content_blocks():
    guard = PaidToolGuard(limit_usd=5.0, costs={STILL_TOOL: 0.0})
    asyncio.run(call_paid(
        guard, STILL_TOOL, image_failure("RuntimeError: ERROR: You've hit your usage limit."),
        tool_input={"image_provider": "codex"},
    ))
    [failure] = guard.failures
    assert (failure.code, failure.provider, failure.billed) == ("SEED_GENERATION_FAILED", "codex", False)
    assert "usage limit" in failure.reason


def test_veo_submit_failure_is_free_and_poll_failure_is_billed():
    guard = PaidToolGuard(limit_usd=5.0, costs={VEO_CLIP_TOOL: 1.2})
    asyncio.run(call_paid(guard, VEO_CLIP_TOOL, clip_failure("SDK_ERROR", "Veo request failed: 403 PERMISSION_DENIED")))
    assert guard.billed_usd == 0.0
    asyncio.run(call_paid(guard, VEO_CLIP_TOOL, clip_failure(
        "SDK_ERROR", "Veo polling failed: 403 PERMISSION_DENIED", dump="metadata/veo_operations/x.json",
    )))
    assert guard.billed_usd == pytest.approx(1.2)
    assert [f.billed for f in guard.failures] == [False, True]


def test_a_crashed_paid_handler_is_recorded_and_charged():
    guard = PaidToolGuard(limit_usd=5.0, costs={VEO_SEED_TOOL: 0.04})
    asyncio.run(fire(guard.hooks(), "PostToolUseFailure", VEO_SEED_TOOL, tool_input={}, error="boom"))
    assert guard.billed_usd == pytest.approx(0.04)
    assert guard.failures[0].code == "HANDLER_CRASH"


@pytest.mark.parametrize("module,generate,tools", [
    (veo_agent_module, "generate_veo_asset", [VEO_SEED_TOOL, VEO_CLIP_TOOL]),
    (stills_agent_module, "generate_still_asset", [STILL_TOOL]),
])
def test_the_paid_guard_merges_with_the_plugin_read_guard_and_both_fire(monkeypatch, module, generate, tools):
    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        yield result_message({"status": "FAILURE"})

    monkeypatch.setattr(module, "query", fake_query)
    guard = PaidToolGuard(limit_usd=0.0, costs={tool: 0.5 for tool in tools})
    asyncio.run(getattr(module, generate)(
        shot={"sequence": 1}, asset={}, subtitle_text="", settings={}, attempt=1,
        max_budget_usd=1.0, guard=guard,
    ))
    hooks = captured["options"].hooks
    assert [m.matcher for m in hooks["PreToolUse"]] == ["Read", *tools]
    assert [m.matcher for m in hooks["PostToolUse"]] == tools

    async def both():
        read_outside = await fire(hooks, "PreToolUse", "Read", tool_input={"file_path": "/etc/passwd"})
        read_plugin = await fire(
            hooks, "PreToolUse", "Read", tool_input={"file_path": str(PLUGIN_DIR / "x.md")},
        )
        paid = await fire(hooks, "PreToolUse", tools[0], tool_input={})
        return read_outside, read_plugin, paid

    read_outside, read_plugin, paid = asyncio.run(both())
    assert denied(read_outside) and not denied(read_plugin)
    assert denied(paid) and guard.denial


def test_without_a_guard_only_the_read_guard_is_bound(monkeypatch):
    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        yield result_message({"status": "FAILURE"})

    monkeypatch.setattr(veo_agent_module, "query", fake_query)
    asyncio.run(veo_agent_module.generate_veo_asset(
        shot={}, asset={}, subtitle_text="", settings={}, attempt=1, max_budget_usd=1.0,
    ))
    assert set(captured["options"].hooks) == {"PreToolUse"}


# --- the budget-denial halt ------------------------------------------------

def test_cumulative_denial_halts_with_budget_exhausted_whatever_the_agent_returned(chain, monkeypatch):
    _, asset_id = chain
    shot = setup_veo(asset_id)
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs)
        guard = kwargs["guard"]
        # Story 4.3: the guard's limit is the attempt's reservation less the
        # Claude cap (seed + clip). Shrink it so the second seed can't be
        # covered, then claim success anyway.
        assert guard.limit_usd == pytest.approx(0.04 + 1.2)
        guard.limit_usd = 0.05
        assert await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        assert not await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        return result_message(approved_outcome_for(shot).model_dump(mode="json"), cost=0.2)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 1
    assert len(calls) == 1
    assert not run.PRODUCTION_ASSETS_FILE.exists()
    [report] = reports("veo_agent")
    assert report["reason_code"] == "budget_exhausted" and report["resumable"] is True
    assert "MAX_BUDGET_USD" in report["reason"]
    # The Claude cost plus the one seed that really ran; no conservative charge.
    assert manifest.budget_spent_usd == pytest.approx(0.24)
    assert list(run.RUN_STATE_DIR.glob("veo_agent_shot_1_attempt_1_budget_denied.json"))


# --- hard refusals on every route -----------------------------------------

class TinyContract(BaseModel):
    value: str

    def validate_sources(self, _):
        return None


def bounded(tmp_path, call_agent, manifest=None):
    manifest = manifest or RunManifest(max_attempts={"tiny": 3})
    code = asyncio.run(run.run_bounded_agent_stage(
        stage_id="tiny", failed_contract_name="TinyContract", contract_cls=TinyContract,
        persisted_file=tmp_path / "tiny.json", validate_contract=lambda c: None,
        max_attempts=3, settings=make_settings(), manifest=manifest,
        base_partial_paths=[], attempt_file_prefix="tiny_attempt", call_agent=call_agent,
    ))
    return code, manifest


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir()
    return tmp_path


def test_bounded_stage_exception_vendor_limit_gives_the_attempt_back(state):
    calls = []

    async def call_agent(feedback, previous, budget):
        calls.append(1)
        raise RuntimeError("Claude Code: You've hit your org's monthly spend limit")

    code, manifest = bounded(state, call_agent)
    assert code == 1 and len(calls) == 1
    assert manifest.iteration_counts["tiny"] == 0
    assert manifest.budget_spent_usd == 0.0
    [report] = reports("tiny")
    assert report["reason_code"] == "vendor_limit" and report["resumable"] is True
    assert report["attempt_count"] == 0


@pytest.mark.parametrize("fields", [
    {"result": "You've hit your session limit · resets 3am"},
    {"result": "API Error", "api_error_status": 401},
    # A 429 already went through the Claude CLI's own retries: no second round.
    {"result": "API Error: Too many requests", "api_error_status": 429},
])
def test_bounded_stage_is_error_vendor_limit_charges_only_claude_cost(state, fields):
    calls = []

    async def call_agent(feedback, previous, budget):
        calls.append(1)
        return result_message(None, cost=0.03, is_error=True, **fields)

    code, manifest = bounded(state, call_agent)
    assert code == 1 and len(calls) == 1
    assert manifest.iteration_counts["tiny"] == 0
    assert manifest.budget_spent_usd == pytest.approx(0.03)
    assert reports("tiny")[0]["reason_code"] == "vendor_limit"
    assert (state / "state" / "tiny_attempt_1_vendor_limit.json").exists()
    assert not (state / "state" / "tiny_attempt_1.json").exists()


def test_bounded_stage_non_vendor_failures_keep_todays_behaviour(state):
    calls = []

    async def call_agent(feedback, previous, budget):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("socket closed")
        return result_message(None, cost=0.01, is_error=True, result="model refused the schema")

    code, manifest = bounded(state, call_agent)
    assert code == 1 and len(calls) == 3
    assert manifest.iteration_counts["tiny"] == 3
    assert reports("tiny")[0]["reason_code"] == "retry_ceiling"


def test_resume_after_a_vendor_limit_redoes_the_same_attempt(state):
    async def refused(feedback, previous, budget):
        raise RuntimeError("usage limit reached")

    _, manifest = bounded(state, refused)

    seen = []

    async def works(feedback, previous, budget):
        seen.append(manifest.iteration_counts["tiny"])
        return result_message({"value": "ok"}, cost=0.01)

    code, _ = bounded(state, works, manifest)
    assert code == 0 and seen == [1]


@pytest.mark.parametrize("agent,setup,generate,contract", [
    ("veo_agent", setup_veo, "generate_veo_asset", "VeoOutcomeContract"),
    ("stills_agent", setup_stills, "generate_still_asset", "StillOutcomeContract"),
])
def test_shot_exception_vendor_limit_skips_the_conservative_charge(chain, monkeypatch, agent, setup, generate, contract):
    _, asset_id = chain
    setup(asset_id)

    async def raising(**kwargs):
        raise RuntimeError("Claude: You've hit your session limit · resets 3am")

    monkeypatch.setattr(run, generate, raising)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    stage = run.run_veo_stage if agent == "veo_agent" else run.run_stills_stage
    assert asyncio.run(stage(make_settings(), manifest)) == 1
    # Today's exception path would have charged the whole remaining budget.
    assert manifest.budget_spent_usd == 0.0
    assert manifest.iteration_counts[f"{agent}_shot_1"] == 0
    [report] = reports(agent)
    assert (report["reason_code"], report["failed_contract_name"]) == ("vendor_limit", contract)


@pytest.mark.parametrize("agent,setup,generate", [
    ("veo_agent", setup_veo, "generate_veo_asset"),
    ("stills_agent", setup_stills, "generate_still_asset"),
])
def test_shot_is_error_vendor_limit_charges_claude_cost_only(chain, monkeypatch, agent, setup, generate):
    _, asset_id = chain
    setup(asset_id)

    async def failing(**kwargs):
        return result_message(None, cost=0.05, is_error=True, result="Invalid API key", api_error_status=401)

    monkeypatch.setattr(run, generate, failing)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    stage = run.run_veo_stage if agent == "veo_agent" else run.run_stills_stage
    assert asyncio.run(stage(make_settings(), manifest)) == 1
    assert manifest.budget_spent_usd == pytest.approx(0.05)
    assert manifest.iteration_counts[f"{agent}_shot_1"] == 0
    assert reports(agent)[0]["reason_code"] == "vendor_limit"
    assert list(run.RUN_STATE_DIR.glob(f"{agent}_shot_1_attempt_1_vendor_limit.json"))


def test_tool_failure_contract_vendor_limit_halts_whatever_the_outcome_says(chain, monkeypatch):
    """The Codex usage limit that burned 2 of shot 3's 3 attempts."""

    _, asset_id = chain
    shot = setup_stills(asset_id)
    calls = []

    async def agent(**kwargs):
        calls.append(1)
        await call_paid(
            kwargs["guard"], STILL_TOOL, image_failure("RuntimeError: You've hit your usage limit."),
            tool_input={"image_provider": "codex"},
        )
        # The agent hands back a normal retryable failure, as it did live.
        return result_message(failed_still_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.07)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 1
    assert len(calls) == 1
    assert manifest.iteration_counts["stills_agent_shot_1"] == 0
    assert manifest.budget_spent_usd == pytest.approx(0.07)
    [report] = reports("stills_agent")
    assert report["reason_code"] == "vendor_limit" and "usage limit" in report["reason"]


@pytest.mark.parametrize("dump,expected_spend", [
    (None, 0.1 + 0.04),                               # failed submit: the clip is free
    ("metadata/veo_operations/x.json", 0.1 + 1.24),  # poll failure after submit: billed
])
def test_veo_sdk_error_hard_refusal_charges_the_clip_only_after_a_submit(chain, monkeypatch, dump, expected_spend):
    _, asset_id = chain
    shot = setup_veo(asset_id)

    async def agent(**kwargs):
        guard = kwargs["guard"]
        await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        await call_paid(guard, VEO_CLIP_TOOL, clip_failure(
            "SDK_ERROR", "Veo request failed: ClientError: 403 PERMISSION_DENIED. {...}", dump=dump,
        ))
        return result_message(failed_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 1
    assert manifest.budget_spent_usd == pytest.approx(expected_spend)
    assert manifest.iteration_counts["veo_agent_shot_1"] == 0
    assert reports("veo_agent")[0]["reason_code"] == "vendor_limit"


def test_veo_sdk_error_without_a_vendor_marker_keeps_todays_retry(chain, monkeypatch):
    _, asset_id = chain
    shot = setup_veo(asset_id)
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        if len(calls) == 1:
            await call_paid(kwargs["guard"], VEO_CLIP_TOOL, clip_failure("SDK_ERROR", "Veo request failed: boom"))
            return result_message(failed_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.1)
        return result_message(approved_outcome_for(shot, attempt=kwargs["attempt"]).model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    assert asyncio.run(run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0
    assert calls == [1, 2]


# --- transient backoff -----------------------------------------------------

@pytest.fixture
def no_wait(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(run.asyncio, "sleep", fake_sleep)
    return waits


def test_transient_veo_refusal_backs_off_then_escalates_to_hard(chain, monkeypatch, no_wait):
    _, asset_id = chain
    shot = setup_veo(asset_id)
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        guard = kwargs["guard"]
        await call_paid(guard, VEO_SEED_TOOL, SEED_OK)
        await call_paid(guard, VEO_CLIP_TOOL, clip_failure(
            "SDK_ERROR", "Veo request failed: ClientError: 429 RESOURCE_EXHAUSTED. Try again later.",
        ))
        return result_message(failed_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_veo_stage(make_settings(), manifest)) == 1
    # The first try plus 3 retries, all of them attempt 1.
    assert calls == [1, 1, 1, 1]
    assert no_wait == list(run.TRANSIENT_BACKOFF_SECONDS)
    assert sum(no_wait) <= 120
    assert manifest.iteration_counts["veo_agent_shot_1"] == 0
    # Every rerun redrew the seed and is charged for it.
    assert manifest.budget_spent_usd == pytest.approx(4 * (0.1 + 0.04))
    [report] = reports("veo_agent")
    assert report["reason_code"] == "vendor_limit" and "still failing after backoff" in report["reason"]
    assert len(list(run.RUN_STATE_DIR.glob("veo_agent_shot_1_attempt_1_transient_*.json"))) == 3


def test_transient_codex_refusal_that_clears_consumes_no_attempt(chain, monkeypatch, no_wait):
    _, asset_id = chain
    shot = setup_stills(asset_id)
    calls = []

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        if len(calls) == 1:
            await call_paid(
                kwargs["guard"], STILL_TOOL, image_failure("RuntimeError: Rate limit reached, slow down"),
                tool_input={"image_provider": "codex"},
            )
            return result_message(failed_still_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.1)
        return result_message(approved_still_outcome_for(shot).model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 0
    assert calls == [1, 1] and no_wait == [run.TRANSIENT_BACKOFF_SECONDS[0]]
    assert manifest.iteration_counts["stills_agent_shot_1"] == 1
    assert list(load_production_assets(run.PRODUCTION_ASSETS_FILE).shots) == ["1"]


def test_gemini_image_contention_is_backed_off_and_consumes_no_attempt(chain, monkeypatch, no_wait):
    """The image path's own minute of backoff is too short for Google's
    shared-capacity 429, which halted two live runs; the stage backs off too."""

    _, asset_id = chain
    shot = setup_stills(asset_id)
    calls = []
    contention = (
        "ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
        "'Resource has been exhausted (e.g. check quota).', 'status': 'RESOURCE_EXHAUSTED'}}"
    )

    async def agent(**kwargs):
        calls.append(kwargs["attempt"])
        if len(calls) == 1:
            await call_paid(
                kwargs["guard"], STILL_TOOL, image_failure(contention),
                tool_input={"image_provider": "gemini"},
            )
            return result_message(failed_still_outcome_for(shot, retryable=True).model_dump(mode="json"), cost=0.1)
        return result_message(approved_still_outcome_for(shot).model_dump(mode="json"), cost=0.1)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 0
    assert calls == [1, 1] and no_wait == [run.TRANSIENT_BACKOFF_SECONDS[0]]
    assert manifest.iteration_counts["stills_agent_shot_1"] == 1


# --- failure report --------------------------------------------------------

def test_a_pre_change_failure_report_dict_still_builds():
    old = {
        "stage_id": "veo_agent", "failed_contract_name": "VeoFailureContract", "reason": "x",
        "attempt_count": 2, "timestamp": "2026-09-18T14:24:34+00:00", "partial_artifact_paths": ["a"],
    }
    report = FailureReport(**old)
    assert (report.reason_code, report.resumable) == ("unclassified", False)
    assert {**old, "reason_code": "unclassified", "resumable": False, "failed_shots": []} == asdict(report)


@pytest.mark.parametrize("code,resumable", [
    ("vendor_limit", True), ("budget_exhausted", True), ("retry_ceiling", False), ("unclassified", False),
])
def test_resumable_follows_the_reason_code(code, resumable):
    from orchestrator.state.run_manifest import build_failure_report

    assert build_failure_report("s", "C", "r", 1, reason_code=code).resumable is resumable


# --- review follow-ups -----------------------------------------------------

def test_a_free_failure_is_not_counted_against_a_retry_in_the_same_session():
    guard = PaidToolGuard(limit_usd=0.05, costs={VEO_SEED_TOOL: 0.04})

    async def scenario():
        first = await call_paid(guard, VEO_SEED_TOOL, image_failure("empty image"))
        return first, await call_paid(guard, VEO_SEED_TOOL, SEED_OK)

    assert asyncio.run(scenario()) == (True, True)
    assert guard.denial is None and guard.billed_usd == pytest.approx(0.04)


def test_stills_denial_halts_with_budget_exhausted_whatever_the_agent_returned(chain, monkeypatch):
    _, asset_id = chain
    shot = setup_stills(asset_id)

    async def agent(**kwargs):
        guard = kwargs["guard"]
        guard.limit_usd = 0.0
        assert not await call_paid(guard, STILL_TOOL, {"local_image_path": "x.png"})
        return result_message(approved_still_outcome_for(shot).model_dump(mode="json"), cost=0.2)

    monkeypatch.setattr(run, "generate_still_asset", agent)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stills_stage(make_settings(), manifest)) == 1
    assert not run.PRODUCTION_ASSETS_FILE.exists()
    [report] = reports("stills_agent")
    assert report["reason_code"] == "budget_exhausted" and report["resumable"] is True
    assert manifest.budget_spent_usd == pytest.approx(0.2)
    assert list(run.RUN_STATE_DIR.glob("stills_agent_shot_1_attempt_1_budget_denied.json"))


def test_stills_pre_reservation_budget_halt_is_resumable_after_raising_the_ceiling(chain, monkeypatch):
    _, asset_id = chain
    setup_stills(asset_id)

    async def refuse(**kwargs):
        raise AssertionError("must not run")

    monkeypatch.setattr(run, "generate_still_asset", refuse)
    assert asyncio.run(run.run_stills_stage(make_settings(max_budget_usd=0.01), load_run_manifest(run.RUN_STATE_DIR))) == 1
    [report] = reports("stills_agent")
    assert report["reason_code"] == "budget_exhausted" and report["resumable"] is True
    assert "MAX_BUDGET_USD" in report["reason"]


def test_a_shot_that_fails_as_video_and_still_halts_not_resumable(chain, monkeypatch):
    _, asset_id = chain
    shot = setup_veo(asset_id)

    async def agent(**kwargs):
        return result_message(failed_outcome_for(shot, retryable=True, reason="QA keeps failing").model_dump(mode="json"))

    async def still_agent(**kwargs):
        still = Shot.model_validate(kwargs["shot"])
        return result_message(failed_still_outcome_for(still, retryable=True, reason="still QA keeps failing").model_dump(mode="json"))

    monkeypatch.setattr(run, "generate_veo_asset", agent)
    monkeypatch.setattr(run, "generate_still_asset", still_agent)
    assert asyncio.run(run.run_veo_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    [report] = reports("stills_agent")
    assert (report["reason_code"], report["resumable"]) == ("retry_ceiling", False)


@pytest.mark.parametrize("agent,setup,generate,tool,cost", [
    ("veo_agent", setup_veo, "generate_veo_asset", VEO_SEED_TOOL, 0.04),
    ("stills_agent", setup_stills, "generate_still_asset", STILL_TOOL, 0.04),
])
def test_exception_after_a_tool_refusal_charges_what_billed(chain, monkeypatch, agent, setup, generate, tool, cost):
    _, asset_id = chain
    setup(asset_id)

    async def raising(**kwargs):
        guard = kwargs["guard"]
        # Refused first: a still attempt reserves one image (Story 4.3), so a
        # second billed image after a billed one would be denied instead.
        await call_paid(guard, tool, image_failure("You've hit your usage limit."), tool_input={"image_provider": "codex"})
        assert await call_paid(guard, tool, SEED_OK)  # billed
        raise RuntimeError("socket closed")

    monkeypatch.setattr(run, generate, raising)
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    stage = run.run_veo_stage if agent == "veo_agent" else run.run_stills_stage
    assert asyncio.run(stage(make_settings(), manifest)) == 1
    assert manifest.budget_spent_usd == pytest.approx(cost)
    assert manifest.iteration_counts[f"{agent}_shot_1"] == 0
    assert reports(agent)[0]["reason_code"] == "vendor_limit"


def test_a_transient_tool_refusal_the_agent_recovered_from_keeps_the_asset(chain, monkeypatch, no_wait):
    _, asset_id = chain
    shot = setup_stills(asset_id)
    calls = []

    async def agent(**kwargs):
        calls.append(1)
        await call_paid(kwargs["guard"], STILL_TOOL, image_failure("Rate limit reached"), tool_input={"image_provider": "codex"})
        await call_paid(kwargs["guard"], STILL_TOOL, {"local_image_path": "x.png"}, tool_input={"image_provider": "codex"})
        return result_message(approved_still_outcome_for(shot).model_dump(mode="json"))

    monkeypatch.setattr(run, "generate_still_asset", agent)
    assert asyncio.run(run.run_stills_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0
    assert calls == [1] and no_wait == []


def test_bounded_stage_exception_vendor_limit_leaves_a_record(state):
    async def call_agent(feedback, previous, budget):
        raise RuntimeError("You've hit your session limit")

    bounded(state, call_agent)
    record = json.loads((state / "state" / "tiny_attempt_1_vendor_limit.json").read_text())
    assert record["status"] == "VENDOR_LIMIT" and "session limit" in record["error"]
