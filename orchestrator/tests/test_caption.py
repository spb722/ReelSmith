from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from orchestrator import caption
from orchestrator.contracts.caption import CaptionPlanContract, caption_problems

# The worked example from the instagram-caption skill.
EXAMPLE = dict(
    hook="12 emails. 3 errands. 1 dentist booking. And the project that mattered? Untouched. 😮‍💨",
    concept="That's not laziness. It's pre-crastination: the urge to start anything right away, "
            "even when it costs you more.",
    story=[
        "In one study, people had to carry one of two heavy buckets down a hallway.",
        "One bucket sat right beside them. The other sat much closer to the finish, so it was the easier carry.",
        "Almost everyone grabbed the near one and lugged it the whole way, just to start a few seconds sooner. 🪣",
    ],
    turn="Your to-do list works the same way. You count tasks instead of weighing them. "
         "Being busy is not the same as getting things done.",
    action="Before you start tomorrow, ask: what am I actually trying to win today?",
    save_share='Save this for your next "busy but nothing done" day, and send it to the friend '
               "who clears their inbox first thing.",
    question="What's the easy task you always grab first?",
    hashtags=["#productivitytips", "#precrastination", "#timemanagement", "#deepwork", "#bookreels"],
    search_phrases=["pre-crastination", "to-do list", "getting things done"],
)


def plan_for(**overrides) -> CaptionPlanContract:
    return CaptionPlanContract(**{**EXAMPLE, **overrides})


# --- the parts are joined in the skill's template order ----------------------


def test_the_worked_example_passes_every_check():
    assert plan_for().problems() == []


def test_assemble_follows_the_template():
    text = plan_for().assemble()
    blocks = text.split("\n\n")
    assert blocks[0].startswith("12 emails.")
    assert blocks[2].count("\n") == 2  # the story, one line per item
    assert blocks[4].startswith("✅ ") and blocks[5].startswith("📌 ") and blocks[6].startswith("💬 ")
    assert blocks[-1] == "#productivitytips #precrastination #timemanagement #deepwork #bookreels"


def test_the_plan_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CaptionPlanContract(**EXAMPLE, extra="x")


# --- the hard limits ---------------------------------------------------------


@pytest.mark.parametrize(("overrides", "expected"), [
    ({"hashtags": EXAMPLE["hashtags"][:4]}, "there are 4 hashtags"),
    ({"hashtags": ["#a", "#b", "#c", "#bookreels", "#d"]}, "the last hashtag must be #bookreels"),
    ({"hashtags": ["#a", "#a", "#b", "#c", "#bookreels"]}, "repeated"),
    ({"hashtags": ["#Deep_Work", "#b", "#c", "#d", "#bookreels"]}, "lowercase"),
    ({"hook": "x" * 126}, "first line is 126 characters"),
    ({"story": ["y" * 2200]}, "the limit is 2200"),
    ({"question": "Which task do you grab first."}, "question mark"),
    ({"search_phrases": ["pre-crastination", "to-do list"]}, "give 3-5 search_phrases"),
    ({"search_phrases": ["pre-crastination", "to-do list", "deep work"]}, "do not appear"),
    ({"turn": "It is **not** laziness."}, "markdown"),
    ({"concept": "It's #precrastination."}, "only in `hashtags`"),
])
def test_each_broken_limit_is_named(overrides, expected):
    problems = plan_for(**overrides).problems()
    assert any(expected in p for p in problems), problems


def test_a_hand_edited_caption_is_checked_as_text():
    text = plan_for().assemble()
    assert caption_problems(text) == []
    assert any("put the hashtags together" in p for p in caption_problems(text + "\n\nOne more line."))
    assert any("#bookreels is missing" in p for p in caption_problems(text.replace("#bookreels", "#books")))


# --- the command -------------------------------------------------------------


def agent_result(output, cost=0.02):
    return SimpleNamespace(is_error=False, subtype="success", errors=None, result="",
                           total_cost_usd=cost, structured_output=output)


@pytest.fixture
def project(tmp_path, monkeypatch):
    from orchestrator.tests.test_preflight import make_settings

    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata" / "final_story_plan.json").write_text(json.dumps({
        "story_title": "Busy is not done", "narration_script": "Twelve emails. Untouched.",
        "story_arc": {"opening_tension": "busy"},
    }))
    monkeypatch.setattr(caption, "resolve_project", lambda name: tmp_path)
    monkeypatch.setattr(caption, "enter_project", lambda path: (monkeypatch.chdir(path), path)[1])
    monkeypatch.setattr(caption, "load_dotenv_file", lambda: None)
    monkeypatch.setattr(caption, "load_settings", lambda: make_settings())
    calls = []

    def fake(outputs):
        async def write_caption(**kwargs):
            calls.append(kwargs)
            return agent_result(outputs[min(len(calls), len(outputs)) - 1])
        monkeypatch.setattr(caption, "write_caption", write_caption)

    return SimpleNamespace(dir=tmp_path, calls=calls, fake=fake)


def test_a_caption_is_written_from_the_narration(project):
    project.fake([plan_for().model_dump()])
    assert caption.main(["--project", "p"]) == 0
    text = (project.dir / caption.CAPTION_FILE).read_text()
    assert text.strip() == plan_for().assemble()
    assert project.calls[0]["narration_script"] == "Twelve emails. Untouched."
    assert json.loads((project.dir / caption.CAPTION_PLAN_FILE).read_text())["produced_by"] == "caption_agent"
    status = json.loads((project.dir / caption.CAPTION_STATUS_FILE).read_text())
    assert status["kind"] == "caption" and status["state"] == "finished"


def test_a_broken_limit_is_sent_back_and_retried(project):
    project.fake([plan_for(hashtags=EXAMPLE["hashtags"][:4]).model_dump(), plan_for().model_dump()])
    assert caption.main(["--project", "p"]) == 0
    assert len(project.calls) == 2
    assert "there are 4 hashtags" in project.calls[1]["correction"]


def test_the_run_stops_after_three_bad_tries(project):
    project.fake([plan_for(question="No question mark").model_dump()])
    assert caption.main(["--project", "p"]) == 1
    assert len(project.calls) == caption.MAX_CAPTION_ATTEMPTS
    assert not (project.dir / caption.CAPTION_FILE).exists()


def test_an_existing_caption_is_kept_unless_forced(project):
    project.fake([plan_for().model_dump()])
    (project.dir / caption.CAPTION_DIR).mkdir(parents=True)
    (project.dir / caption.CAPTION_FILE).write_text("old caption\n")
    assert caption.main(["--project", "p"]) == 0
    assert project.calls == []
    assert caption.main(["--project", "p", "--force"]) == 0
    assert (project.dir / caption.CAPTION_DIR / "caption_v1.txt").read_text() == "old caption\n"
    [entry] = json.loads((project.dir / caption.CAPTION_HISTORY_FILE).read_text())
    assert entry == {"previous_caption": "caption_v1.txt", "change": "fresh", "feedback": None}


def test_feedback_revises_the_current_caption(project):
    project.fake([plan_for(question="What do you grab first?").model_dump()])
    (project.dir / caption.CAPTION_DIR).mkdir(parents=True)
    (project.dir / caption.CAPTION_FILE).write_text("my hand-edited caption\n")
    assert caption.main(["--project", "p", "--feedback", "friendlier question"]) == 0
    [call] = project.calls
    assert call["current_caption"] == "my hand-edited caption\n" and call["feedback"] == "friendlier question"
    assert "💬 What do you grab first?" in (project.dir / caption.CAPTION_FILE).read_text()
    [entry] = json.loads((project.dir / caption.CAPTION_HISTORY_FILE).read_text())
    assert entry["change"] == "feedback" and entry["feedback"] == "friendlier question"


def test_feedback_needs_an_existing_caption(project):
    project.fake([plan_for().model_dump()])
    assert caption.main(["--project", "p", "--feedback", "shorter"]) == 1
    assert project.calls == []


def test_no_narration_stops_the_run(project):
    (project.dir / "metadata" / "final_story_plan.json").unlink()
    project.fake([plan_for().model_dump()])
    assert caption.main(["--project", "p"]) == 1
