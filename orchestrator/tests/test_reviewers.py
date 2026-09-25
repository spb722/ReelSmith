"""Story 4.5: a fresh-context story critic and visual-QA reviewer approve work
that another agent produced. No live calls: every query is mocked."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage

import orchestrator.agents.story_critic as critic_module
import orchestrator.agents.visual_reviewer as reviewer_module
import orchestrator.run as run
from orchestrator.agents._guard import PaidToolGuard
from orchestrator.agents._skills import PLUGIN, PLUGIN_DIR, SkillIsolationError
from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.review import StoryCritiqueContract, VisualQAVerdictContract
from orchestrator.settings import SettingsError, load_settings
from orchestrator.state.production_assets import load_production_assets
from orchestrator.state.run_manifest import load_run_manifest
from orchestrator.tests.test_full_chain_resume import (  # noqa: F401 -- `chain` is a fixture
    approved_outcome_for,
    approved_still_outcome_for,
    chain,
    still_visual_plan_for,
    subtitle_cues_for,
    veo_visual_plan_for,
)
from orchestrator.tests.test_guard_hooks import fire, result_message
from orchestrator.tests.test_preflight import make_settings as _make_settings
from orchestrator.tests.test_story_agent import stage  # noqa: F401 -- fixture
from orchestrator.tools.gemini_tools import describe_ladder_outcome

REVIEW_CAP = 0.5
SHOT_CAP = 1.0


def make_settings(**overrides):
    return _make_settings(**{
        "claude_reviewer_cap_usd": REVIEW_CAP, "claude_shot_attempt_cap_usd": SHOT_CAP, **overrides,
    })


def critique(verdict="APPROVE", issues=(), *, cost=0.2, **kwargs) -> ResultMessage:
    return result_message(
        {"produced_by": "story_critic", "verdict": verdict, "issues": list(issues)}, cost=cost, **kwargs,
    )


ISSUE = {"dimension": "hook", "evidence": "\"In this story...\" opens flat.", "fix": "Open on the conflict."}


def verdict(approved=True, *, stage=None, code=None, reasons=(), summary="Looks right.", cost=0.1):
    return result_message({
        "produced_by": "visual_reviewer", "approved": approved, "failure_stage": stage,
        "failure_code": code, "reasons": list(reasons), "summary": summary,
    }, cost=cost)


# --- settings and contracts ---------------------------------------------------

def test_reviewer_settings_default_to_a_generous_cap_and_the_claude_model(monkeypatch):
    for name in ("CLAUDE_REVIEWER_CAP_USD", "REVIEWER_MODEL", "CRITIC_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test")
    settings = load_settings()
    assert settings.claude_reviewer_cap_usd == 1.0
    assert settings.reviewer_model == settings.critic_model == "claude-test"


def test_reviewer_settings_are_env_overridable(monkeypatch):
    monkeypatch.setenv("CLAUDE_REVIEWER_CAP_USD", "0.75")
    monkeypatch.setenv("REVIEWER_MODEL", "reviewer-model")
    monkeypatch.setenv("CRITIC_MODEL", "critic-model")
    settings = load_settings()
    assert (settings.claude_reviewer_cap_usd, settings.reviewer_model, settings.critic_model) == (
        0.75, "reviewer-model", "critic-model",
    )
    monkeypatch.setenv("CLAUDE_REVIEWER_CAP_USD", "0")
    with pytest.raises(SettingsError):
        load_settings()


def test_revise_needs_issues_and_a_rejection_needs_its_stage_code_and_reason():
    with pytest.raises(ValueError):
        StoryCritiqueContract.model_validate({"produced_by": "story_critic", "verdict": "REVISE", "issues": []})
    with pytest.raises(ValueError):
        VisualQAVerdictContract.model_validate({
            "produced_by": "visual_reviewer", "approved": False, "failure_stage": "seed_qa",
            "failure_code": "X", "reasons": [], "summary": "s",
        })
    with pytest.raises(ValueError):
        VisualQAVerdictContract.model_validate({
            "produced_by": "visual_reviewer", "approved": True, "failure_stage": "seed_qa", "summary": "s",
        })


def test_a_production_entry_written_before_the_reviewer_still_validates(chain):
    source, asset_id = chain
    shot = veo_visual_plan_for(asset_id).shots[0]
    entry = ProductionAssetEntry.from_veo_result(approved_outcome_for(shot).result, [asset_id])
    old = entry.model_dump(mode="json")
    old.pop("reviewer_summary")
    assert ProductionAssetEntry.model_validate(old).reviewer_summary == ""


# --- the story critic in the bounded stage -------------------------------------

def run_narration(settings=None):
    return asyncio.run(run.run_narration_stage(settings or make_settings(), load_run_manifest(run.RUN_STATE_DIR)))


def story_writer(plan, calls, *, cost=0.3):
    async def write_story(assets, *, max_budget_usd, feedback=None, previous_output=None):
        calls.append({"max_budget_usd": max_budget_usd, "feedback": feedback, "previous_output": previous_output})
        return result_message(plan, cost=cost)

    return write_story


def story_critic(verdicts, calls):
    async def critique_story(draft_plan, analyzed_assets, *, max_budget_usd):
        calls.append({"draft_plan": draft_plan, "analyzed_assets": analyzed_assets, "max_budget_usd": max_budget_usd})
        item = verdicts[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return critique_story


def test_revise_feeds_the_next_attempt_and_only_approve_persists(stage, monkeypatch):
    source, asset_ids, plan = stage
    writes, reviews = [], []
    monkeypatch.setattr(run, "write_story", story_writer(plan, writes))
    monkeypatch.setattr(run, "critique_story", story_critic([critique("REVISE", [ISSUE]), critique()], reviews))

    assert run_narration() == 0

    assert len(writes) == 2 and len(reviews) == 2
    assert writes[0]["feedback"] is None
    assert "Open on the conflict." in writes[1]["feedback"] and "hook" in writes[1]["feedback"]
    assert writes[1]["previous_output"] == plan
    # Fresh context: only the draft and the analyzed assets.
    assert reviews[0]["draft_plan"]["scenes"] == plan["scenes"]
    assert [a["asset_id"] for a in reviews[0]["analyzed_assets"]] == asset_ids
    assert run.FINAL_STORY_PLAN_FILE.is_file()
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.iteration_counts["story_agent"] == 2
    record = json.loads((run.RUN_STATE_DIR / "story_agent_attempt_1_review.json").read_text())
    assert record["approved"] is False and record["structured_output"]["verdict"] == "REVISE"


def test_the_critic_never_sees_the_writers_own_quality_review(stage, monkeypatch):
    source, asset_ids, plan = stage
    assert "quality_review" in plan
    reviews = []
    monkeypatch.setattr(run, "write_story", story_writer(plan, []))
    monkeypatch.setattr(run, "critique_story", story_critic([critique()], reviews))

    assert run_narration() == 0

    assert "quality_review" not in reviews[0]["draft_plan"]


def test_revise_on_every_attempt_halts_with_retry_ceiling_and_persists_nothing(stage, monkeypatch):
    source, asset_ids, plan = stage
    writes, reviews = [], []
    monkeypatch.setattr(run, "write_story", story_writer(plan, writes))
    monkeypatch.setattr(run, "critique_story", story_critic([critique("REVISE", [ISSUE])] * 4, reviews))

    assert run_narration() == 1

    assert len(writes) == 4
    assert not run.FINAL_STORY_PLAN_FILE.exists()
    [report] = [json.loads(p.read_text()) for p in run.RUN_STATE_DIR.glob("failure_story_agent_*.json")]
    assert report["reason_code"] == "retry_ceiling"


def test_critic_cost_is_charged_and_budgets_hold_back_the_critic_cap(stage, monkeypatch):
    source, asset_ids, plan = stage
    writes, reviews = [], []
    monkeypatch.setattr(run, "write_story", story_writer(plan, writes, cost=0.3))
    monkeypatch.setattr(run, "critique_story", story_critic([critique(cost=0.2)], reviews))

    assert run_narration(make_settings(max_budget_usd=10.0)) == 0

    assert writes[0]["max_budget_usd"] == pytest.approx(10.0 - REVIEW_CAP)
    assert reviews[0]["max_budget_usd"] == REVIEW_CAP
    assert load_run_manifest(run.RUN_STATE_DIR).budget_spent_usd == pytest.approx(0.5)


def test_the_critic_never_runs_on_the_resume_skip_path(stage, monkeypatch):
    source, asset_ids, plan = stage
    run.FINAL_STORY_PLAN_FILE.write_text(json.dumps(plan), encoding="utf-8")
    writes, reviews = [], []
    monkeypatch.setattr(run, "write_story", story_writer(plan, writes))
    monkeypatch.setattr(run, "critique_story", story_critic([critique()], reviews))

    assert run_narration() == 0
    assert writes == [] and reviews == []


def test_the_critic_does_not_run_without_its_cap_left(stage, monkeypatch):
    source, asset_ids, plan = stage
    writes, reviews = [], []
    # 1.0 left: the writer gets 0.5, overshoots to 0.7, and 0.3 can't cover the critic.
    monkeypatch.setattr(run, "write_story", story_writer(plan, writes, cost=0.7))
    monkeypatch.setattr(run, "critique_story", story_critic([critique()], reviews))

    assert run_narration(make_settings(max_budget_usd=1.0)) == 1

    assert reviews == []
    assert not run.FINAL_STORY_PLAN_FILE.exists()
    [report] = [json.loads(p.read_text()) for p in run.RUN_STATE_DIR.glob("failure_story_agent_*.json")]
    assert report["reason_code"] == "budget_exhausted"


def test_no_story_attempt_starts_when_the_budget_cannot_also_hold_the_critic(stage, monkeypatch):
    source, asset_ids, plan = stage
    monkeypatch.setattr(run, "write_story", story_writer(plan, []))
    assert run_narration(make_settings(max_budget_usd=REVIEW_CAP)) == 1
    assert load_run_manifest(run.RUN_STATE_DIR).iteration_counts.get("story_agent", 0) == 0


def test_a_critic_vendor_limit_gives_the_attempt_back(stage, monkeypatch):
    source, asset_ids, plan = stage
    monkeypatch.setattr(run, "write_story", story_writer(plan, []))
    refused = critique(cost=0.0, is_error=True, subtype="error_during_execution",
                       result="You've hit your session limit", api_error_status=429)
    monkeypatch.setattr(run, "critique_story", story_critic([refused], []))

    assert run_narration() == 1

    assert load_run_manifest(run.RUN_STATE_DIR).iteration_counts["story_agent"] == 0
    [report] = [json.loads(p.read_text()) for p in run.RUN_STATE_DIR.glob("failure_story_agent_*.json")]
    assert report["reason_code"] == "vendor_limit"


def test_an_unusable_critic_verdict_is_not_an_approval(stage, monkeypatch):
    source, asset_ids, plan = stage
    writes = []
    monkeypatch.setattr(run, "write_story", story_writer(plan, writes))
    garbage = result_message({"produced_by": "story_critic", "verdict": "MAYBE"}, cost=0.1)
    monkeypatch.setattr(run, "critique_story", story_critic([garbage, RuntimeError("boom"), critique()], []))

    assert run_narration() == 0

    assert len(writes) == 3
    assert "no usable verdict" in writes[1]["feedback"]
    assert "review of try 2 failed" in writes[2]["feedback"]


# --- the visual reviewer in the shot tasks --------------------------------------

def production(settings=None):
    return asyncio.run(run.run_production_stage(settings or make_settings(), load_run_manifest(run.RUN_STATE_DIR)))


def write_plan(plan):
    run.VISUAL_PLAN_FILE.write_text(plan.model_dump_json(), encoding="utf-8")
    run.SUBTITLE_CUES_FILE.write_text(json.dumps(subtitle_cues_for()), encoding="utf-8")


def producer(builder, calls, *, cost=0.2):
    async def agent(**kwargs):
        calls.append(kwargs)
        return result_message(builder(kwargs).model_dump(mode="json"), cost=cost)

    return agent


def reviewer(verdicts, calls):
    async def review_visual_asset(**kwargs):
        calls.append(kwargs)
        item = verdicts[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return review_visual_asset


def attempt_record(agent, sequence, count):
    return json.loads((run.RUN_STATE_DIR / f"{agent}_shot_{sequence}_attempt_{count}.json").read_text())


def test_an_approved_veo_shot_is_upserted_with_the_reviewer_summary(chain, monkeypatch):
    source, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    produced, reviews = [], []
    monkeypatch.setattr(run, "generate_veo_asset", producer(lambda kw: approved_outcome_for(plan.shots[0]), produced))
    monkeypatch.setattr(run, "review_visual_asset", reviewer([verdict(summary="Seed and clip pass.")], reviews))

    assert production() == 0

    entry = load_production_assets(run.PRODUCTION_ASSETS_FILE).shots["1"]
    assert entry.reviewer_summary == "Seed and clip pass."
    assert entry.qa_summary == "Clip motion and scene fidelity passed."
    [review] = reviews
    assert review["generation_mode"] == "VEO"
    assert review["images_under_review"] == [
        "generated/veo_seeds/shot_01_seed.png", "generated/veo/previews/shot_01_01.jpg",
    ]
    assert review["max_budget_usd"] == REVIEW_CAP
    assert "qa_summary" not in json.dumps(review)  # the producer's own verdict stays out
    assert attempt_record("veo_agent", 1, 1)["review"]["verdict"]["approved"] is True


def test_an_approved_still_is_upserted_with_the_reviewer_summary(chain, monkeypatch):
    source, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    reviews = []
    monkeypatch.setattr(run, "generate_still_asset", producer(lambda kw: approved_still_outcome_for(plan.shots[0]), []))
    monkeypatch.setattr(run, "review_visual_asset", reviewer([verdict(summary="Still passes.")], reviews))

    assert production() == 0

    assert load_production_assets(run.PRODUCTION_ASSETS_FILE).shots["1"].reviewer_summary == "Still passes."
    assert reviews[0]["images_under_review"] == ["generated/stills/shot_01.png"]


def test_a_reviewer_likeness_rejection_of_a_seed_is_recorded_and_moves_the_ladder(chain, monkeypatch):
    source, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    produced, reviews = [], []
    monkeypatch.setattr(run, "generate_veo_asset", producer(lambda kw: approved_outcome_for(plan.shots[0], attempt=kw["attempt"]), produced))
    rejection = verdict(False, stage="seed_qa", code="CHARACTER_LIKENESS",
                        reasons=["The man is not the referenced character."], summary="Wrong face.")
    monkeypatch.setattr(run, "review_visual_asset", reviewer([rejection, verdict()], reviews))

    assert production() == 0

    assert [call["settings"]["seed_prompt_level"] for call in produced] == [0, 1]
    record = attempt_record("veo_agent", 1, 1)
    failure = record["structured_output"]["failure"]
    assert record["structured_output"]["status"] == "FAILURE"
    assert (failure["stage"], failure["code"], failure["produced_by"]) == ("seed_qa", "CHARACTER_LIKENESS", "orchestrator")
    assert "not the referenced character" in failure["reason"]
    assert record["producer_outcome"]["status"] == "SUCCESS"
    assert record["seed_prompt_level"] == 0
    assert "not the referenced character" in produced[1]["correction"]
    assert produced[1]["previous_outcome"]["status"] == "FAILURE"
    # A resume sees the rejection and starts on the next rung.
    assert run._prior_character_rejections(1) == 1
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert manifest.iteration_counts["veo_agent_shot_1"] == 2


def test_a_clip_qa_rejection_retries_without_moving_the_ladder(chain, monkeypatch):
    source, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    produced = []
    monkeypatch.setattr(run, "generate_veo_asset", producer(lambda kw: approved_outcome_for(plan.shots[0], attempt=kw["attempt"]), produced))
    rejection = verdict(False, stage="clip_qa", code="CHARACTER_LIKENESS", reasons=["Face drifts mid-clip."])
    monkeypatch.setattr(run, "review_visual_asset", reviewer([rejection, verdict()], []))

    assert production() == 0
    assert [call["settings"]["seed_prompt_level"] for call in produced] == [0, 0]


def test_a_still_rejection_is_recorded_and_simply_retries(chain, monkeypatch):
    source, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    produced = []
    monkeypatch.setattr(run, "generate_still_asset", producer(lambda kw: approved_still_outcome_for(plan.shots[0]), produced))
    rejection = verdict(False, stage="still_qa", code="CHARACTER_LIKENESS", reasons=["Generic person."])
    monkeypatch.setattr(run, "review_visual_asset", reviewer([rejection, verdict()], []))

    assert production() == 0

    assert len(produced) == 2
    assert "seed_prompt_level" not in produced[1]["settings"]  # stills have no ladder state
    failure = attempt_record("stills_agent", 1, 1)["structured_output"]["failure"]
    assert (failure["stage"], failure["code"]) == ("still_qa", "CHARACTER_LIKENESS")
    assert "Generic person." in produced[1]["correction"]


def test_reviewer_rejections_count_toward_the_shot_ceiling(chain, monkeypatch):
    source, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    monkeypatch.setattr(run, "generate_still_asset", producer(lambda kw: approved_still_outcome_for(plan.shots[0]), []))
    rejection = verdict(False, stage="still_qa", code="VISIBLE_TEXT", reasons=["A caption is drawn in."])
    monkeypatch.setattr(run, "review_visual_asset", reviewer([rejection] * 3, []))

    assert production() == 1

    assert not load_production_assets(run.PRODUCTION_ASSETS_FILE).shots
    [report] = [json.loads(p.read_text()) for p in run.RUN_STATE_DIR.glob("failure_stills_agent_*.json")]
    assert report["reason_code"] == "retry_ceiling"
    assert report["failed_shots"][0]["failure_stage"] == "still_qa"


def test_a_stage_that_does_not_fit_the_shot_is_an_invalid_verdict(chain, monkeypatch):
    source, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    monkeypatch.setattr(run, "generate_still_asset", producer(lambda kw: approved_still_outcome_for(plan.shots[0]), []))
    monkeypatch.setattr(run, "review_visual_asset", reviewer([verdict(False, stage="seed_qa", code="X", reasons=["r"])], []))

    assert production() == 1
    assert attempt_record("stills_agent", 1, 1)["review"]["status"] == "INVALID_VERDICT"


def test_the_shot_reservation_holds_one_review_and_each_query_gets_its_own_cap(chain, monkeypatch):
    source, asset_id = chain
    settings = make_settings()
    assert run.shot_attempt_reservation(settings, "VEO") == (SHOT_CAP + REVIEW_CAP, pytest.approx(0.04 + 1.2))
    assert run.shot_attempt_reservation(settings, "STILL") == (SHOT_CAP + REVIEW_CAP, pytest.approx(0.04))

    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    produced, reviews = [], []
    monkeypatch.setattr(run, "generate_veo_asset", producer(lambda kw: approved_outcome_for(plan.shots[0]), produced, cost=0.2))
    monkeypatch.setattr(run, "review_visual_asset", reviewer([verdict(cost=0.1)], reviews))

    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(settings, manifest)) == 0

    assert produced[0]["max_budget_usd"] == SHOT_CAP
    # The paid-tool guard can't spend the Claude shares, the reviewer's included.
    assert produced[0]["guard"].limit_usd == pytest.approx(0.04 + 1.2)
    assert reviews[0]["max_budget_usd"] == REVIEW_CAP
    assert manifest.budget_spent_usd == pytest.approx(0.2 + 0.04 + 1.2 + 0.1)


def test_planning_counts_the_review_in_each_first_attempt(chain):
    source, asset_id = chain
    plan = veo_visual_plan_for(asset_id)
    write_plan(plan)
    # Enough for a Veo attempt without its review, not with it: demoted to a still.
    settings = make_settings(max_budget_usd=SHOT_CAP + 0.04 + 1.2 + 0.1)
    assert asyncio.run(run.run_production_planning(settings, load_run_manifest(run.RUN_STATE_DIR))) == 0
    assert json.loads(run.VISUAL_PLAN_FILE.read_text())["shots"][0]["generation_mode"] == "STILL"


def test_a_reviewer_vendor_limit_gives_the_attempt_back_and_stops_the_run(chain, monkeypatch):
    source, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    monkeypatch.setattr(run, "generate_still_asset", producer(lambda kw: approved_still_outcome_for(plan.shots[0]), []))
    refused = result_message(None, cost=0.0, is_error=True, subtype="error_during_execution",
                             result="Claude usage limit reached", api_error_status=429)
    monkeypatch.setattr(run, "review_visual_asset", reviewer([refused], []))

    assert production() == 1

    assert load_run_manifest(run.RUN_STATE_DIR).iteration_counts["stills_agent_shot_1"] == 0
    assert not load_production_assets(run.PRODUCTION_ASSETS_FILE).shots
    [report] = [json.loads(p.read_text()) for p in run.RUN_STATE_DIR.glob("failure_stills_agent_*.json")]
    assert report["reason_code"] == "vendor_limit" and report["resumable"] is True


def test_a_reviewer_crash_is_charged_its_cap_and_is_systemic(chain, monkeypatch):
    source, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    monkeypatch.setattr(run, "generate_still_asset", producer(lambda kw: approved_still_outcome_for(plan.shots[0]), [], cost=0.2))
    monkeypatch.setattr(run, "review_visual_asset", reviewer([RuntimeError("boom")], []))

    manifest = load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(make_settings(), manifest)) == 1

    assert manifest.budget_spent_usd == pytest.approx(0.2 + 0.04 + REVIEW_CAP)
    [report] = [json.loads(p.read_text()) for p in run.RUN_STATE_DIR.glob("failure_stills_agent_*.json")]
    assert report["failed_shots"][0]["scope"] == "systemic"


def test_a_reviewer_skill_leak_gives_the_attempt_back(chain, monkeypatch):
    source, asset_id = chain
    plan = still_visual_plan_for(asset_id)
    write_plan(plan)
    monkeypatch.setattr(run, "generate_still_asset", producer(lambda kw: approved_still_outcome_for(plan.shots[0]), []))
    monkeypatch.setattr(run, "review_visual_asset", reviewer([SkillIsolationError("lossless-teacher")], []))

    assert production() == 1

    assert load_run_manifest(run.RUN_STATE_DIR).iteration_counts["stills_agent_shot_1"] == 0


@pytest.mark.parametrize("code, expected", [
    ("CHARACTER_LIKENESS", "CHARACTER_LIKENESS"),
    ("face_mismatch", "CHARACTER_LIKENESS"),
    (" LIKENESS ", "CHARACTER_LIKENESS"),
    ("VISIBLE_TEXT", "VISIBLE_TEXT"),
])
def test_any_likeness_code_is_pinned_to_the_one_the_ladder_reads(code, expected):
    assert run._reviewer_failure_code(code) == expected
    assert (expected == "CHARACTER_LIKENESS") == any(
        marker in expected.lower() for marker in run.SEED_QA_CHARACTER_MARKERS
    )


def test_the_reviewer_is_told_the_rung_the_image_was_really_drawn_at(chain, monkeypatch):
    source, asset_id = chain
    monkeypatch.setattr(run, "character_reference_paths_for_asset", lambda asset: [Path("/refs/turnaround.png")])
    guard = PaidToolGuard(limit_usd=5.0, costs={})
    asset = type("A", (), {"model_dump": lambda self, mode=None: {}})()

    assert run._reviewer_character_context(asset, guard, 0) == (
        ["/refs/turnaround.png"], describe_ladder_outcome({"level": 0}),
    )
    guard.image_notes.append(describe_ladder_outcome({"level": 1}))
    assert run._reviewer_character_context(asset, guard, 0)[1] == describe_ladder_outcome({"level": 1})
    # The no-character rung: nothing to compare the source's own figure to.
    guard.image_notes.append(describe_ladder_outcome({"level": 2}))
    assert run._reviewer_character_context(asset, guard, 0) == ([], describe_ladder_outcome({"level": 2}))


def test_the_guard_keeps_the_ladder_note_an_image_tool_returned():
    guard = PaidToolGuard(limit_usd=5.0, costs={"mcp__gemini__generate_still": 0.04})
    note = describe_ladder_outcome({"level": 1})
    asyncio.run(fire(guard.hooks(), "PostToolUse", "mcp__gemini__generate_still", tool_input={}, tool_response=[
        {"type": "text", "text": json.dumps({"shot_sequence": 1})},
        {"type": "image", "data": "", "mimeType": "image/png"},
        {"type": "text", "text": note},
    ]))
    assert guard.image_notes == [note]


# --- the two agents' own options -------------------------------------------------

def capture_options(module, monkeypatch, output):
    seen = {}

    async def fake_query(*, prompt, options):
        seen["prompt"], seen["options"] = prompt, options
        yield result_message(output)

    monkeypatch.setattr(module, "query", fake_query)
    return seen


def test_story_critic_options_are_isolated_with_its_two_skills(monkeypatch):
    monkeypatch.setenv("CRITIC_MODEL", "critic-model")
    seen = capture_options(critic_module, monkeypatch, {"produced_by": "story_critic", "verdict": "APPROVE"})
    asyncio.run(critic_module.critique_story({"narration": "draft"}, [{"asset_id": "a"}], max_budget_usd=0.5))
    options = seen["options"]
    assert options.skills == ["book-reels:story-craft", "book-reels:source-fidelity"]
    assert options.plugins == [PLUGIN] and Path(PLUGIN["path"]).is_absolute()
    assert options.tools == ["Skill", "Read"] and "Skill" not in options.allowed_tools
    assert options.setting_sources == [] and options.permission_mode == "dontAsk"
    assert options.max_budget_usd == 0.5 and options.model == "critic-model"
    assert options.output_format["schema"] == StoryCritiqueContract.model_json_schema()
    assert "draft" in seen["prompt"] and '"asset_id": "a"' in seen["prompt"]


def test_visual_reviewer_reads_only_its_skills_and_the_files_under_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVIEWER_MODEL", "reviewer-model")
    Path("generated/stills").mkdir(parents=True)
    Path("generated/stills/shot_01.png").write_bytes(b"x")
    seen = capture_options(reviewer_module, monkeypatch, {
        "produced_by": "visual_reviewer", "approved": True, "summary": "ok",
    })
    asyncio.run(reviewer_module.review_visual_asset(
        generation_mode="STILL", shot={"sequence": 1}, subtitle_text="",
        images_under_review=["generated/stills/shot_01.png"],
        character_reference_paths=["/refs/character.png"], ladder_note="", max_budget_usd=0.5,
    ))
    options = seen["options"]
    assert options.skills == ["book-reels:visual-qa", "book-reels:character-likeness-qa"]
    assert options.tools == ["Skill", "Read"] and options.setting_sources == []
    assert options.model == "reviewer-model" and options.max_budget_usd == 0.5
    assert options.output_format["schema"] == VisualQAVerdictContract.model_json_schema()
    still = (tmp_path / "generated/stills/shot_01.png").resolve()
    assert f"Read(/{still})" in options.allowed_tools

    async def decision(path):
        [output] = await fire(options.hooks, "PreToolUse", "Read", tool_input={"file_path": path}, cwd=str(tmp_path))
        return output.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    assert asyncio.run(decision(str(still))) == "allow"
    assert asyncio.run(decision("generated/stills/shot_01.png")) == "allow"
    assert asyncio.run(decision("/refs/character.png")) == "allow"
    assert asyncio.run(decision(str(PLUGIN_DIR / "skills/visual-qa/SKILL.md"))) == "allow"
    assert asyncio.run(decision(str(tmp_path / "metadata/production_assets.json"))) == "deny"
    assert asyncio.run(decision("/etc/passwd")) == "deny"


@pytest.mark.parametrize("module, call", [
    (critic_module, lambda m: m.critique_story({}, [], max_budget_usd=0.5)),
    (reviewer_module, lambda m: m.review_visual_asset(
        generation_mode="STILL", shot={}, subtitle_text="", images_under_review=[],
        character_reference_paths=[], ladder_note="", max_budget_usd=0.5,
    )),
])
def test_a_foreign_skill_in_init_halts_either_reviewer(module, call, monkeypatch):
    from orchestrator.tests.test_skills_plugin import init_message

    async def fake_query(*, prompt, options):
        yield init_message([*options.skills, "bmad-build"])
        yield result_message({})

    monkeypatch.setattr(module, "query", fake_query)
    with pytest.raises(SkillIsolationError):
        asyncio.run(call(module))
