"""The narration checkpoint, the rewrite switch, and `orchestrator/ui_hooks.py`.

No vendor calls: stages and agents are fakes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import orchestrator.run as run
from orchestrator import ui_hooks
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.state.run_manifest import load_run_manifest, save_run_manifest
from orchestrator.tests.test_full_chain_resume import (  # noqa: F401 - chain is a fixture
    NARRATION_SCRIPT, chain, final_story_plan_for, sdk_result,
)
from orchestrator.tests.test_preflight import make_settings
from orchestrator.tests.test_status_file import project, read_status  # noqa: F401 - project is a fixture


# --- the pause flag -------------------------------------------------------

def narration_that_writes_a_plan(calls):
    async def stage(settings, manifest):
        calls.append("narration")
        run.FINAL_STORY_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        run.FINAL_STORY_PLAN_FILE.write_text(json.dumps({"fresh": len(calls)}), encoding="utf-8")
        return 0
    return stage


def test_the_flag_pauses_after_a_freshly_written_narration(project):
    path, calls, monkeypatch = project
    monkeypatch.setenv("REELSMITH_UI", "1")
    monkeypatch.setattr(run, "run_narration_stage", narration_that_writes_a_plan(calls))
    flag = ui_hooks.pause_flag_path(path)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()

    assert run.main(["--project", path.name]) == 0
    assert calls == ["screenshots", "narration"]
    status = read_status(path)
    assert status["state"] == "paused_for_narration"
    assert status["step"] == 2
    assert [step["state"] for step in status["steps"][:3]] == ["done", "waiting_for_you", "waiting"]
    log = sorted((path / "orchestrator_runs").glob("run_*.log"))[-1].read_text(encoding="utf-8")
    assert "Paused after the narration — waiting for you" in log


def test_running_again_carries_on_from_step_3(project):
    """The resume skips step 2 (its plan is saved), so the same flag doesn't pause it again."""
    path, calls, monkeypatch = project
    monkeypatch.setenv("REELSMITH_UI", "1")
    monkeypatch.setattr(run, "run_narration_stage", narration_that_writes_a_plan(calls))
    flag = ui_hooks.pause_flag_path(path)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    assert run.main(["--project", path.name]) == 0

    async def skipped_narration(settings, manifest):
        calls.append("narration skipped")
        return 0

    monkeypatch.setattr(run, "run_narration_stage", skipped_narration)
    calls.clear()
    assert run.main(["--project", path.name]) == 0
    assert calls == ["screenshots", "narration skipped", "visual", "voice", "production", "delivery"]
    assert read_status(path)["state"] == "finished"


def test_a_terminal_run_ignores_a_leftover_flag(project):
    path, calls, monkeypatch = project
    monkeypatch.delenv("REELSMITH_UI", raising=False)
    monkeypatch.setattr(run, "run_narration_stage", narration_that_writes_a_plan(calls))
    flag = ui_hooks.pause_flag_path(path)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    assert run.main(["--project", path.name]) == 0
    assert calls[-1] == "delivery"


def test_no_flag_no_pause(project):
    path, calls, monkeypatch = project
    monkeypatch.setattr(run, "run_narration_stage", narration_that_writes_a_plan(calls))
    assert run.main(["--project", path.name]) == 0
    assert calls[-1] == "delivery"


# --- STORY_REWRITE_INSTRUCTIONS -------------------------------------------

def test_a_saved_plan_skips_step_2_without_instructions(chain, monkeypatch):
    async def refuse(*args, **kwargs):
        raise AssertionError("the saved plan must be reused")

    monkeypatch.setattr(run, "write_story", refuse)
    assert asyncio.run(run.run_narration_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 0


def rewritten_plan(asset_id: str) -> dict:
    plan = final_story_plan_for(asset_id)
    script = NARRATION_SCRIPT.replace("word5 ", "rewritten ")
    plan["narration_script"] = plan["hook"] = plan["scenes"][0]["narration"] = script
    return plan


def test_instructions_rewrite_the_saved_plan_even_at_the_ceiling(chain, monkeypatch):
    source, asset_id = chain
    old_plan = json.loads(run.FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8"))
    run.VISUAL_PLAN_FILE.write_text("{}", encoding="utf-8")  # made from the old script
    manifest = load_run_manifest(run.RUN_STATE_DIR)
    manifest.iteration_counts["story_agent"] = manifest.attempts_allowed("story_agent")
    save_run_manifest(manifest, state_dir=run.RUN_STATE_DIR)
    seen = {}

    async def write_story(assets, *, max_budget_usd, feedback, previous_output):
        seen.update(feedback=feedback, previous_output=previous_output)
        return sdk_result(rewritten_plan(asset_id), cost=0.2)

    monkeypatch.setattr(run, "write_story", write_story)
    monkeypatch.setenv("STORY_REWRITE_INSTRUCTIONS", "Make the ending warmer.")
    assert asyncio.run(run.run_narration_stage(make_settings(), manifest)) == 0

    assert "Make the ending warmer." in seen["feedback"]
    assert seen["previous_output"] == old_plan
    saved = json.loads(run.FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8"))
    assert "rewritten" in saved["narration_script"]
    assert not run.VISUAL_PLAN_FILE.exists()
    assert list(Path("_archive").glob("narration_rewrite_*/metadata/visual_plan.json"))
    assert manifest.iteration_counts["story_agent"] == manifest.attempts_allowed("story_agent") + 1


def test_a_failed_rewrite_puts_the_old_plan_back(chain, monkeypatch):
    before = run.FINAL_STORY_PLAN_FILE.read_bytes()

    async def broken(*args, **kwargs):
        return sdk_result({"not": "a plan"}, cost=0.0)

    monkeypatch.setattr(run, "write_story", broken)
    monkeypatch.setenv("STORY_REWRITE_INSTRUCTIONS", "Shorter.")
    assert asyncio.run(run.run_narration_stage(make_settings(), load_run_manifest(run.RUN_STATE_DIR))) == 1
    assert run.FINAL_STORY_PLAN_FILE.read_bytes() == before


# --- ui_hooks -------------------------------------------------------------

def two_scene_plan() -> dict:
    plan = final_story_plan_for("asset_1")
    words = NARRATION_SCRIPT.split()
    first, second = " ".join(words[:50]), " ".join(words[50:])
    scene = plan["scenes"][0]
    plan["scenes"] = [
        {**scene, "sequence": 1, "role": "HOOK", "narration": first, "estimated_duration_seconds": 22.5},
        {**scene, "sequence": 2, "role": "ENDING", "narration": second, "estimated_duration_seconds": 22.5,
         "impact_text": "word60 word61"},
    ]
    plan["hook"] = first
    plan["core_thesis"] = "Acceptance beats striving."
    plan["story_arc"]["viewer_reflection"] = "What would you stop chasing?"
    return plan


@pytest.fixture
def reel(tmp_path):
    """A project with a saved two-scene plan and outputs from every later step."""

    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata/final_story_plan.json").write_text(json.dumps(two_scene_plan()), encoding="utf-8")
    for rel in ("metadata/visual_plan.json", "metadata/subtitle_cues.json", "metadata/narration_audio.json",
                "metadata/production_assets.json", "metadata/timeline.json", "audio/narration.wav",
                "generated/stills/shot_01.png", "generated/veo/shot_02.mp4", "book_reel.mp4",
                "orchestrator_runs/visual_agent_attempt_1.json",
                "orchestrator_runs/stills_agent_shot_1_attempt_1.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    runs = tmp_path / "orchestrator_runs"
    (runs / "config.json").write_text(json.dumps({
        "limits": {"max_attempts": {"story_agent": 4}},
        "usage": {"budget_spent_usd": 7.5, "attempts_used": {
            "asset_analyst": 1, "story_agent": 2, "visual_agent": 1, "voice_agent": 1,
            "stills_agent_shot_1": 1, "veo_agent_shot_2": 3,
        }, "session_id": "s"},
    }), encoding="utf-8")
    (runs / "story_agent_attempt_1_review.json").write_text(json.dumps({"structured_output": {
        "produced_by": "story_critic", "verdict": "REVISE",
        "issues": [{"dimension": "hook_strength", "evidence": "Flat opening.", "fix": "Lead with the twist."}],
    }}), encoding="utf-8")
    (runs / "story_agent_attempt_2_review.json").write_text(json.dumps({"structured_output": {
        "produced_by": "story_critic", "verdict": "APPROVE", "issues": [],
    }}), encoding="utf-8")
    return tmp_path


def test_pause_flag_path(tmp_path):
    assert ui_hooks.pause_flag_path(tmp_path) == tmp_path / "orchestrator_runs" / "pause_after_narration.flag"


def test_read_narration(reel):
    narration = ui_hooks.read_narration(reel)
    assert narration["script"] == NARRATION_SCRIPT
    assert narration["word_count"] == 100
    assert narration["critic"] == {"verdict": "APPROVE", "issues": []}  # the latest review
    assert narration["outline"] == {
        "hook": " ".join(NARRATION_SCRIPT.split()[:50]),
        "main_point": "Acceptance beats striving.",
        "ending": "What would you stop chasing?",
    }
    assert narration["story_skill_limits"] == {"min_words": 90, "max_words": 115, "min_s": 40, "max_s": 50}


def test_read_narration_uses_the_projects_story_skill_and_critic_issues(reel):
    (reel / "ui_settings.json").write_text(json.dumps({"story_skill": "story-craft-easy"}), encoding="utf-8")
    (reel / "orchestrator_runs/story_agent_attempt_2_review.json").unlink()
    narration = ui_hooks.read_narration(reel)
    assert narration["story_skill_limits"]["max_words"] == 140
    assert narration["critic"] == {
        "verdict": "REVISE", "issues": ["hook_strength: Flat opening. Fix: Lead with the twist."],
    }


def test_read_narration_before_any_plan(tmp_path):
    narration = ui_hooks.read_narration(tmp_path)
    assert narration["script"] == ""
    assert narration["critic"] == {"verdict": None, "issues": []}


def test_downstream_done(reel, tmp_path_factory):
    assert ui_hooks.downstream_done(reel)
    empty = tmp_path_factory.mktemp("fresh")
    (empty / "generated/stills").mkdir(parents=True)
    assert not ui_hooks.downstream_done(empty)


def test_write_narration_updates_the_plan_and_clears_what_came_after(reel, monkeypatch):
    words = NARRATION_SCRIPT.split()
    words[3] = "changed"             # an edit inside scene 1
    words[60:62] = ["new", "words"]  # scene 2's impact phrase edited away
    edited = " ".join(words + ["an", "extra", "ending"])
    ui_hooks.write_narration(reel, edited)

    plan = json.loads((reel / "metadata/final_story_plan.json").read_text(encoding="utf-8"))
    assert plan["narration_script"] == edited
    first, second = (scene["narration"] for scene in plan["scenes"])
    assert first == " ".join(words[:50])  # the boundary stayed where it was
    assert second.endswith("an extra ending")
    assert plan["scenes"][1]["impact_text"] == ""
    assert plan["hook"] == first
    assert plan["target_word_count"] == 103
    assert 40 <= plan["target_duration_seconds"] <= 50

    # Nothing after step 2 is left for a resume to trip over; it was moved, not deleted.
    assert not ui_hooks.downstream_done(reel)
    assert not (reel / "orchestrator_runs/visual_agent_attempt_1.json").exists()
    archived = next((reel / "_archive").glob("narration_edit_*"))
    assert (archived / "book_reel.mp4").is_file()
    assert (archived / "generated/stills/shot_01.png").is_file()
    config = json.loads((reel / "orchestrator_runs/config.json").read_text(encoding="utf-8"))
    assert config["usage"]["budget_spent_usd"] == 7.5
    assert config["usage"]["attempts_used"] == {"asset_analyst": 1, "story_agent": 2}

    # A resume validates and reuses the edited plan instead of redoing step 2.
    monkeypatch.chdir(reel)
    FinalStoryPlanContract.model_validate_json((reel / "metadata/final_story_plan.json").read_text())


def test_write_narration_refuses_a_script_of_the_wrong_length(reel):
    before = (reel / "metadata/final_story_plan.json").read_bytes()
    with pytest.raises(ValueError, match="needs 90 to 115"):
        ui_hooks.write_narration(reel, "far too short")
    assert (reel / "metadata/final_story_plan.json").read_bytes() == before
    assert ui_hooks.downstream_done(reel)


def test_write_narration_without_a_change_keeps_everything(reel):
    ui_hooks.write_narration(reel, "  " + NARRATION_SCRIPT + "\n")
    assert ui_hooks.downstream_done(reel)
    assert not (reel / "_archive").exists()
