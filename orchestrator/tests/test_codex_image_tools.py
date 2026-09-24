from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from orchestrator.tools import codex_image_tools as codex
from orchestrator.tools.gemini_tools import ImageEditRefused


@pytest.fixture(autouse=True)
def _pretend_codex_is_installed(monkeypatch):
    monkeypatch.setattr(codex.shutil, "which", lambda name: "/usr/local/bin/codex")


@pytest.fixture
def source_image(tmp_path) -> Path:
    path = tmp_path / "screenshot.png"
    Image.new("RGB", (360, 640), "blue").save(path)
    return path


@pytest.fixture
def character_sheet(tmp_path) -> Path:
    path = tmp_path / "character.png"
    Image.new("RGB", (200, 400), "green").save(path)
    return path


def workspace_of(command: list[str]) -> Path:
    return Path(command[command.index("--cd") + 1])


def fake_codex(*, status="generated", reason="drew it", write_image=True, size=(360, 640),
               returncode=0, recorder=None):
    """A stand-in for the Codex CLI that behaves like the real one: it writes
    its answer to files in the workspace rather than to stdout."""

    def run(command, **kwargs):
        if recorder is not None:
            recorder.append((command, kwargs))
        workspace = workspace_of(command)
        (workspace / codex.LAST_MESSAGE_FILENAME).write_text(
            json.dumps({"status": status, "reason": reason}), encoding="utf-8"
        )
        if write_image:
            Image.new("RGB", size, "red").save(workspace / codex.OUTPUT_FILENAME)
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="")

    return run


def test_returns_the_image_codex_wrote(monkeypatch, source_image):
    monkeypatch.setattr(subprocess, "run", fake_codex())
    image, note = codex.run_codex_image_edit(
        source_image_path=source_image, prompt="a quiet room", timeout_seconds=60
    )
    assert image.size == (360, 640)
    assert note == "drew it"


def test_refusal_raises_so_the_prompt_ladder_advances(monkeypatch, source_image):
    """The ladder escalates only on a refusal, so a refusal has to arrive as
    ImageEditRefused and not as a generic error."""
    monkeypatch.setattr(
        subprocess, "run",
        fake_codex(status="refused", reason="depicts a real person", write_image=False),
    )
    with pytest.raises(ImageEditRefused, match="depicts a real person"):
        codex.run_codex_image_edit(
            source_image_path=source_image, prompt="a face", timeout_seconds=60
        )


def test_refusal_is_not_retried(monkeypatch, source_image):
    calls = []
    monkeypatch.setattr(
        subprocess, "run",
        fake_codex(status="refused", reason="no", write_image=False, recorder=calls),
    )
    with pytest.raises(ImageEditRefused):
        codex.run_codex_image_edit(
            source_image_path=source_image, prompt="a face", timeout_seconds=60
        )
    assert len(calls) == 1


def test_claimed_success_with_no_file_is_retried_then_fails(monkeypatch, source_image):
    """Saying "generated" without writing anything is the agent slipping, not
    refusing, so it is worth one more try before giving up."""
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_codex(write_image=False, recorder=calls))
    with pytest.raises(RuntimeError, match="no image after 2 attempts"):
        codex.run_codex_image_edit(
            source_image_path=source_image, prompt="a room", timeout_seconds=60
        )
    assert len(calls) == codex.EMPTY_OUTPUT_MAX_ATTEMPTS


def test_non_zero_exit_reports_the_reason(monkeypatch, source_image):
    monkeypatch.setattr(
        subprocess, "run",
        fake_codex(returncode=2, reason="codex fell over", write_image=False),
    )
    with pytest.raises(RuntimeError, match="exited with code 2"):
        codex.run_codex_image_edit(
            source_image_path=source_image, prompt="a room", timeout_seconds=60
        )


def test_timeout_is_reported_in_plain_units(monkeypatch, source_image):
    def slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(RuntimeError, match="did not finish drawing within 1m00s"):
        codex.run_codex_image_edit(
            source_image_path=source_image, prompt="a room", timeout_seconds=60
        )


def test_wrong_shaped_image_is_rejected(monkeypatch, source_image):
    """Codex picks its own canvas. A landscape frame must fail here rather than
    reach Remotion and be silently letterboxed."""
    monkeypatch.setattr(subprocess, "run", fake_codex(size=(1920, 1080)))
    with pytest.raises(RuntimeError, match="vertical 9:16"):
        codex.run_codex_image_edit(
            source_image_path=source_image, prompt="a room", timeout_seconds=60
        )


def test_square_image_is_rejected(monkeypatch, source_image):
    monkeypatch.setattr(subprocess, "run", fake_codex(size=(800, 800)))
    with pytest.raises(RuntimeError, match="vertical 9:16"):
        codex.run_codex_image_edit(
            source_image_path=source_image, prompt="a room", timeout_seconds=60
        )


def test_missing_source_image_fails_before_calling_codex(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_codex(recorder=calls))
    with pytest.raises(FileNotFoundError):
        codex.run_codex_image_edit(
            source_image_path=tmp_path / "absent.png", prompt="x", timeout_seconds=60
        )
    assert calls == []


def test_missing_reference_image_fails_before_calling_codex(monkeypatch, source_image, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_codex(recorder=calls))
    with pytest.raises(FileNotFoundError):
        codex.run_codex_image_edit(
            source_image_path=source_image,
            prompt="x",
            reference_image_paths=[tmp_path / "absent.png"],
            timeout_seconds=60,
        )
    assert calls == []


def test_scene_and_references_are_staged_and_attached(monkeypatch, source_image, character_sheet):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_codex(recorder=calls))
    codex.run_codex_image_edit(
        source_image_path=source_image,
        prompt="a quiet room",
        reference_image_paths=[character_sheet],
        timeout_seconds=60,
    )
    command, kwargs = calls[0]
    workspace = workspace_of(command)
    attached = [command[i + 1] for i, part in enumerate(command) if part == "--image"]

    # Attached as real images, not merely named in the prompt, so the model
    # actually sees the character rather than having to open a filename.
    assert attached == [str(workspace / "scene.png"), str(workspace / "character_01.png")]
    assert kwargs["cwd"] == workspace
    # The prompt must ride in on stdin. `--image` is variadic, so a trailing
    # positional prompt is swallowed as one more image path and Codex exits
    # with "No prompt provided via stdin".
    assert command[-1] != codex.build_codex_prompt("a quiet room", ["character_01.png"])
    assert kwargs["input"].startswith("$imagegen")
    assert "a quiet room" in kwargs["input"]


def test_api_keys_are_stripped_so_codex_uses_the_subscription(monkeypatch, source_image):
    """A present OPENAI_API_KEY makes Codex bill per call against that key,
    which would quietly break the provider's zero cost estimate."""
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-passed")
    monkeypatch.setenv("CODEX_API_KEY", "also-not")
    monkeypatch.setattr(subprocess, "run", fake_codex(recorder=calls))
    codex.run_codex_image_edit(
        source_image_path=source_image, prompt="a room", timeout_seconds=60
    )
    env = calls[0][1]["env"]
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env


def test_prompt_opens_with_the_imagegen_directive(source_image):
    prompt = codex.build_codex_prompt("a quiet room", [])
    assert prompt.startswith("$imagegen")


def test_prompt_names_each_file_and_its_role():
    prompt = codex.build_codex_prompt("a quiet room", ["character_01.png"])
    assert "scene.png" in prompt
    assert "character_01.png" in prompt
    assert "character sheet" in prompt
    assert codex.OUTPUT_FILENAME in prompt
    assert "a quiet room" in prompt


def test_prompt_mentions_no_character_when_there_is_none():
    prompt = codex.build_codex_prompt("an empty street", [])
    assert "character sheet" not in prompt


def test_unparseable_closing_message_is_not_treated_as_refusal(monkeypatch, source_image):
    """Only an explicit refusal may advance the prompt ladder; anything
    ambiguous falls through to whether the file is actually there."""
    def run(command, **kwargs):
        workspace = workspace_of(command)
        (workspace / codex.LAST_MESSAGE_FILENAME).write_text("I drew it!", encoding="utf-8")
        Image.new("RGB", (360, 640), "red").save(workspace / codex.OUTPUT_FILENAME)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    image, note = codex.run_codex_image_edit(
        source_image_path=source_image, prompt="a room", timeout_seconds=60
    )
    assert image.size == (360, 640)
    assert note == "I drew it!"


def test_missing_codex_cli_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(codex.shutil, "which", lambda name: None)
    with pytest.raises(codex.CodexNotAvailableError, match="IMAGE_PROVIDER=gemini"):
        codex.codex_executable()


# --- the scene must not claim the person -----------------------------------
# A wrapper line reading "Everything about the art style comes from the scene"
# beat the character block that says to draw the person from the sheet, and
# Codex drew a generic face in the scene's flat style. See shot_03_seed.png.


def test_the_scene_does_not_claim_the_person():
    prompt = codex.build_codex_prompt("a quiet room", ["character_01.png"])
    scene_line = next(l for l in prompt.splitlines() if l.startswith('- "scene.png"'))
    assert "everything except the person" in scene_line
    # The absolute claim is what overrode the character block.
    assert "Everything about" not in scene_line


def test_the_character_sheet_is_named_as_the_exception():
    prompt = codex.build_codex_prompt("a quiet room", ["character_01.png"])
    sheet_line = next(l for l in prompt.splitlines() if l.startswith('- "character_01.png"'))
    assert "the person is the exception" in sheet_line
    assert "come from here, not from the scene" in sheet_line
    # The sheet is identity only -- its own framing must not leak in.
    assert "Never copy this sheet's own background" in sheet_line


CHARACTER_BLOCK = (
    "CHARACTER SUBSTITUTION. The FIRST image is the source scene described above. "
    "The SECOND image is a character reference. Redraw that one figure. "
    "FINAL RULE, overriding anything above: render NO text of any kind."
)


def test_positional_image_names_become_filenames():
    """"The SECOND image" means nothing to Codex, which was handed named files
    -- and that phrase is what the character's face instruction hangs off."""
    prompt = codex.build_codex_prompt(CHARACTER_BLOCK, ["character_01.png"])
    assert "The FIRST image" not in prompt
    assert "The SECOND image" not in prompt
    assert '"scene.png" is the source scene described above' in prompt
    assert '"character_01.png" is a character reference' in prompt


def test_a_face_close_up_reference_is_named_too():
    two_refs = (
        "CHARACTER SUBSTITUTION. The FIRST image is the source scene described above. "
        "The SECOND image is a full-body character reference. The THIRD image is a "
        "close-up of that same character's face and is the authority on their facial "
        "features -- follow it exactly for the face. Redraw that one figure."
    )
    prompt = codex.build_codex_prompt(two_refs, ["character_01.png", "character_02.png"])
    assert "The SECOND image" not in prompt and "The THIRD image" not in prompt
    assert '"character_01.png" is a full-body character reference' in prompt
    assert '"character_02.png" is a close-up' in prompt


def test_the_character_requirement_gets_its_own_heading():
    """It was buried mid-way through one 500-word block, behind the scene
    description, which is where it kept getting lost."""
    prompt = codex.build_codex_prompt(CHARACTER_BLOCK, ["character_01.png"])
    assert "THE SCENE TO DRAW:" in prompt
    assert "WHO THE PERSON IS" in prompt
    assert prompt.index("THE SCENE TO DRAW:") < prompt.index("WHO THE PERSON IS")


def test_the_no_text_rule_still_lands_last():
    """It overrides everything above it, so it must stay after the character
    block rather than being reordered ahead of it."""
    prompt = codex.build_codex_prompt(CHARACTER_BLOCK, ["character_01.png"])
    assert prompt.index("WHO THE PERSON IS") < prompt.index("FINAL RULE, overriding")
    assert prompt.index("FINAL RULE, overriding") < prompt.index("Save the finished image")


def test_the_detailed_face_demand_survives_the_rewrite():
    """Deliberately kept: the operator wants the detailed face, with the Veo
    ladder as the fallback if it gets refused."""
    block = (
        "CHARACTER SUBSTITUTION. The FIRST image is the source scene described above. "
        "The SECOND image is a character reference. The face must be clearly visible, "
        "in focus, and detailed enough to recognise."
    )
    prompt = codex.build_codex_prompt(block, ["character_01.png"])
    assert "clearly visible, in focus, and detailed enough to recognise" in prompt
