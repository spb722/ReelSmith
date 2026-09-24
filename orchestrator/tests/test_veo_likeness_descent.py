from __future__ import annotations

import pytest

import orchestrator.run as run
from orchestrator.run import _is_likeness_rejection


class Failure:
    def __init__(self, code, operation_error=None, stage="veo_generation", reason=""):
        self.code = code
        self.operation_error = operation_error
        self.stage = stage
        self.reason = reason


def test_safety_codes_descend_the_ladder():
    assert _is_likeness_rejection(Failure("RAI_FILTERED"))
    assert _is_likeness_rejection(Failure("UNSAFE_SEED"))


def test_the_real_operation_error_from_the_halted_run_descends():
    """Verbatim from metadata/veo_operations/shot_03_attempt_02.json."""
    failure = Failure("OPERATION_ERROR", {
        "code": 3,
        "message": (
            "Veo could not generate videos because the input image violates Vertex AI's "
            "usage guidelines. If you think this was an error, send feedback. "
            "Support codes: 15236754"
        ),
    })
    assert _is_likeness_rejection(failure)


def test_an_ordinary_backend_fault_does_not_descend():
    """A redraw cannot fix a transient backend fault, so the wording must not
    be degraded for one."""
    assert not _is_likeness_rejection(
        Failure("OPERATION_ERROR", {"code": 13, "message": "Internal error, please retry"})
    )


def test_unrelated_codes_do_not_descend():
    for code in ("POLL_TIMEOUT", "SDK_ERROR", "MALFORMED_OUTPUT", "PREVIEW_EXTRACTION_FAILED"):
        assert not _is_likeness_rejection(Failure(code)), code


def test_a_missing_operation_error_is_handled():
    assert not _is_likeness_rejection(Failure("OPERATION_ERROR", None))


# --- resume remembers earlier rejections ------------------------------------


def write_attempt(state_dir, sequence, attempt, code, operation_error=None,
                  stage="veo_generation", reason="", level=None):
    import json

    record = {
        "structured_output": {
            "status": "FAILURE",
            "failure": {"code": code, "operation_error": operation_error,
                        "stage": stage, "reason": reason},
        }
    }
    if level is not None:
        record["seed_prompt_level"] = level
    path = state_dir / f"veo_agent_shot_{sequence}_attempt_{attempt}.json"
    path.write_text(json.dumps(record), encoding="utf-8")


def test_no_attempts_on_disk_starts_at_the_best_wording(tmp_path, monkeypatch):
    import orchestrator.run as run

    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    assert run._prior_character_rejections(3) == 0


def test_each_earlier_rejection_advances_the_starting_rung(tmp_path, monkeypatch):
    import orchestrator.run as run

    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    write_attempt(tmp_path, 3, 1, "RAI_FILTERED", level=0)
    write_attempt(tmp_path, 3, 2, "OPERATION_ERROR",
                  [{"code": 3, "message": "the input image violates Vertex AI's usage guidelines"}],
                  level=1)
    # Resume below the worst rung actually refused, not below the count.
    assert run._prior_character_rejections(3) == 2


def test_other_failures_do_not_advance_the_rung(tmp_path, monkeypatch):
    """A poll timeout says nothing about the face, so the wording must hold."""
    import orchestrator.run as run

    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    write_attempt(tmp_path, 3, 1, "POLL_TIMEOUT")
    write_attempt(tmp_path, 3, 2, "SDK_ERROR")
    assert run._prior_character_rejections(3) == 0


def test_only_this_shot_s_attempts_count(tmp_path, monkeypatch):
    import orchestrator.run as run

    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    write_attempt(tmp_path, 5, 1, "RAI_FILTERED", level=0)
    assert run._prior_character_rejections(3) == 0
    assert run._prior_character_rejections(5) == 1


def test_an_unreadable_attempt_record_is_skipped(tmp_path, monkeypatch):
    """A truncated record must not crash the stage before it starts."""
    import orchestrator.run as run

    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    (tmp_path / "veo_agent_shot_3_attempt_1.json").write_text("{not json", encoding="utf-8")
    write_attempt(tmp_path, 3, 2, "RAI_FILTERED", level=0)
    assert run._prior_character_rejections(3) == 1


def test_a_successful_attempt_does_not_count(tmp_path, monkeypatch):
    import json

    import orchestrator.run as run

    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    (tmp_path / "veo_agent_shot_3_attempt_1.json").write_text(
        json.dumps({"structured_output": {"status": "SUCCESS", "failure": None}}), encoding="utf-8"
    )
    assert run._prior_character_rejections(3) == 0


# --- our own seed QA can refuse the character too ---------------------------
# Handling only Veo's likeness filter cost a live run three identical attempts:
# the source drew the man in profile, level-0 wording demanded a face "clearly
# visible... never turned away", the image model kept the source pose, and the
# QA agent rejected the same contradiction three times over.


def qa_failure(code, reason=""):
    return Failure(code, stage="seed_qa", reason=reason)


def test_a_seed_qa_face_rejection_moves_the_ladder():
    """Verbatim code from the halted run."""
    assert run._forces_simpler_character_wording(
        qa_failure("SEED_CHARACTER_FACE_NOT_RECOGNISABLE_AND_COMPOSITION_OFF_SPEC")
    )
    assert run._forces_simpler_character_wording(
        qa_failure("SEED_REJECTED", reason="the presenter is drawn in near-strict left profile")
    )


def test_a_seed_qa_rejection_about_something_else_does_not():
    """Asking for less of the character cannot fix stray text or a duplicate
    subject -- it would quietly cost the character for an unrelated defect."""
    assert not run._forces_simpler_character_wording(
        qa_failure("SEED_HAS_VISIBLE_APP_UI", reason="the status bar is still present")
    )
    assert not run._forces_simpler_character_wording(
        qa_failure("SEED_DUPLICATE_SUBJECT", reason="two tombstones appear in frame")
    )


def test_veo_and_qa_rejections_are_both_honoured():
    assert run._forces_simpler_character_wording(Failure("RAI_FILTERED"))
    assert run._forces_simpler_character_wording(qa_failure("SEED_CHARACTER_FACE_HIDDEN"))
    assert not run._forces_simpler_character_wording(Failure("POLL_TIMEOUT"))


# --- resume uses the rung, not the tally -----------------------------------


def test_three_rejections_of_one_rung_advance_by_one(tmp_path, monkeypatch):
    """The exact shape of the halted run: every attempt refused at level 0.
    The next thing to try is level 1, not level 3 -- which would skip the rung
    most likely to work and silently drop the character."""
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    for attempt in (1, 2, 3):
        write_attempt(tmp_path, 1, attempt, "SEED_CHARACTER_FACE_NOT_RECOGNISABLE",
                      stage="seed_qa", level=0)

    assert run._prior_character_rejections(1) == 1


def test_records_without_a_rung_are_treated_as_level_zero(tmp_path, monkeypatch):
    """Attempts written before the rung was tracked all came from level 0."""
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    write_attempt(tmp_path, 1, 1, "SEED_CHARACTER_FACE_HIDDEN", stage="seed_qa")

    assert run._prior_character_rejections(1) == 1


def test_the_worst_rung_wins_regardless_of_order(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RUN_STATE_DIR", tmp_path)
    write_attempt(tmp_path, 1, 1, "RAI_FILTERED", level=1)
    write_attempt(tmp_path, 1, 2, "RAI_FILTERED", level=0)

    assert run._prior_character_rejections(1) == 2
