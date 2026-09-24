"""Draw a shot's image by shelling out to the local Codex CLI.

The alternative backend to `run_gemini_image_edit`, selected by
`IMAGE_PROVIDER=codex`. It returns the same `(PIL.Image, model_text)` pair, so
the prompt ladder, the contracts, and the agents' visual QA are all unchanged
-- only the thing that puts pixels on disk differs.

Codex is a coding agent, not an image endpoint: it draws when the prompt opens
with the `$imagegen` directive, and it writes the result to a file rather than
returning it in a response body. So a call here is a small staged workspace --
the scene and the character sheet copied in under predictable names, the agent
pointed at it with `--cd`, and the finished PNG read back out.

Two flags do the work that Gemini's structured response used to:
  --output-schema   forces the agent's closing message into
                    {"status": "generated"|"refused", "reason": ...}, which is
                    what tells a refusal (advance the prompt ladder) apart from
                    a transient miss (retry the same rung).
  -o                captures that closing message without parsing the
                    human-readable transcript on stdout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Sequence

from PIL import Image

from orchestrator.progress import format_duration, heartbeat, log

# Reuse the Gemini path's exception so `run_image_edit_ladder` needs no
# provider-specific branch: a refusal is a refusal whoever issued it.
from orchestrator.tools.gemini_tools import ImageEditRefused

SCENE_FILENAME = "scene.png"
OUTPUT_FILENAME = "final_image.png"
SCHEMA_FILENAME = "outcome_schema.json"
LAST_MESSAGE_FILENAME = "outcome.json"

# A "generated" claim with no file on disk is the agent slipping, not refusing,
# so it is worth one more go. A refusal is not retried -- it will not change.
EMPTY_OUTPUT_MAX_ATTEMPTS = 2

# The reel is 1080x1920. Gemini returns 768x1376 (0.558); 9:16 is 0.5625.
# Codex picks its own canvas, so an off-shape frame is caught here rather than
# silently letterboxed by Remotion three stages later.
TARGET_ASPECT_RATIO = 9 / 16
ASPECT_RATIO_TOLERANCE = 0.08

OUTCOME_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["generated", "refused"],
            "description": "generated if the image file was written, refused if you declined.",
        },
        "reason": {
            "type": "string",
            "description": "Why you refused, or a one-line note on what you drew.",
        },
    },
    "required": ["status", "reason"],
    "additionalProperties": False,
}


class CodexNotAvailableError(RuntimeError):
    """The Codex CLI is not installed or not on PATH."""


def codex_executable() -> str:
    """Absolute path to the Codex CLI, or raise with a fixable message."""

    found = shutil.which("codex")
    if found is None:
        raise CodexNotAvailableError(
            "IMAGE_PROVIDER=codex needs the Codex CLI on PATH, and `codex` was not found. "
            "Install it, or set IMAGE_PROVIDER=gemini."
        )
    return found


def _codex_environment() -> dict[str, str]:
    """Environment for a Codex call, with API keys removed.

    A present OPENAI_API_KEY makes Codex bill per call against that key instead
    of the subscription the operator is already logged in with. Dropping it
    keeps the provider free, which is the premise its zero cost estimate rests on.
    """

    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    return env


# Where the shared prompt's own sections begin. Splitting on them lets the
# Codex prompt present each as its own heading instead of one 500-word wall,
# in which the character requirement was reliably the part that got dropped.
CHARACTER_MARKER = "CHARACTER SUBSTITUTION."
FINAL_RULE_MARKER = "FINAL RULE, overriding anything above:"


def _retarget_positional_references(
    prompt: str, reference_filenames: Sequence[str]
) -> str:
    """Rewrite "The FIRST/SECOND image" as the filenames Codex actually has.

    The shared character block addresses images by position, which is how
    Gemini receives them. Codex receives named files, so left alone the one
    sentence carrying the character's face -- "The SECOND image is a character
    reference" -- points at nothing it can identify.
    """

    prompt = prompt.replace(
        "The FIRST image is the source scene described above.",
        f'"{SCENE_FILENAME}" is the source scene described above.',
    )
    if len(reference_filenames) >= 2:
        prompt = prompt.replace(
            "The SECOND image is a full-body character reference. The THIRD image is a "
            "close-up of that same character's face",
            f'"{reference_filenames[0]}" is a full-body character reference. '
            f'"{reference_filenames[1]}" is a close-up of that same character\'s face',
        )
    elif reference_filenames:
        prompt = prompt.replace(
            "The SECOND image is a character reference.",
            f'"{reference_filenames[0]}" is a character reference.',
        )
    return prompt


def build_codex_prompt(
    prompt: str, reference_filenames: Sequence[str], *, output_filename: str = OUTPUT_FILENAME
) -> str:
    """Wrap a shot's prompt in the file-role framing Codex needs.

    The shot prompts were written for an image-edit API that takes the plate as
    the first positional image. Codex gets files by name, so the roles that
    were positional have to be said out loud -- and the scene's claim on the
    image has to stop short of the person, or the character never arrives.
    """

    retargeted = _retarget_positional_references(prompt, reference_filenames)
    before_character, has_character, after_character = retargeted.partition(CHARACTER_MARKER)
    if has_character:
        scene_part = before_character
        character_part, _, final_rule = after_character.partition(FINAL_RULE_MARKER)
    else:
        scene_part, _, final_rule = before_character.partition(FINAL_RULE_MARKER)
        character_part = ""

    lines = [
        "$imagegen",
        "",
        "Create one new image from the image files in this directory.",
        "",
        f'- "{SCENE_FILENAME}" is the scene. Reproduce its composition, art style, palette,'
        " lighting, background and props -- everything except the person in it.",
    ]
    for filename in reference_filenames:
        lines.append(
            f'- "{filename}" is the character sheet, and the person is the exception:'
            f' whoever appears in "{SCENE_FILENAME}" must be redrawn as this character.'
            " Their face, hair, beard, build, colouring and clothing come from here, not"
            " from the scene. Never copy this sheet's own background, standing pose, crop"
            " or framing."
        )
    lines += ["", "THE SCENE TO DRAW:", scene_part.strip()]
    if character_part:
        lines += [
            "",
            "WHO THE PERSON IS -- the requirement most often missed, do not skip it:",
            CHARACTER_MARKER + " " + character_part.strip(),
        ]
    if final_rule:
        lines += ["", FINAL_RULE_MARKER + " " + final_rule.strip()]
    lines += [
        "",
        f'Save the finished image as "{output_filename}" in this directory.',
        "It must be a vertical 9:16 portrait image.",
        "Do not write any explanation, commentary or progress notes as your final message --"
        " reply only with the required JSON object.",
    ]
    return "\n".join(lines)


def _stage_workspace(
    workspace: Path, source_image_path: Path, reference_image_paths: Sequence[Path]
) -> list[str]:
    """Copy the scene and references in under predictable names."""

    if not source_image_path.is_file():
        raise FileNotFoundError(f"Source image does not exist: {source_image_path}")
    Image.open(source_image_path).convert("RGB").save(workspace / SCENE_FILENAME, format="PNG")

    reference_filenames: list[str] = []
    for index, reference_path in enumerate(reference_image_paths, start=1):
        reference_path = Path(reference_path)
        if not reference_path.is_file():
            raise FileNotFoundError(f"Reference image does not exist: {reference_path}")
        filename = f"character_{index:02d}.png"
        Image.open(reference_path).convert("RGB").save(workspace / filename, format="PNG")
        reference_filenames.append(filename)
    return reference_filenames


def _read_outcome(workspace: Path) -> dict:
    """Parse the agent's closing message, tolerating a non-JSON reply.

    An unparseable message is not treated as a refusal: only an explicit
    "refused" advances the prompt ladder, so anything ambiguous falls through
    to the file check, where the image either exists or it does not.
    """

    path = workspace / LAST_MESSAGE_FILENAME
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"reason": text}
    return parsed if isinstance(parsed, dict) else {"reason": text}


def _validate_aspect_ratio(image: Image.Image) -> None:
    width, height = image.size
    if height == 0:
        raise RuntimeError("Codex wrote an image with zero height")
    ratio = width / height
    if abs(ratio - TARGET_ASPECT_RATIO) > ASPECT_RATIO_TOLERANCE:
        raise RuntimeError(
            f"Codex returned a {width}x{height} image (ratio {ratio:.3f}); the reel needs "
            f"vertical 9:16 (~{TARGET_ASPECT_RATIO:.3f})"
        )


def run_codex_image_edit(
    *,
    source_image_path: Path,
    prompt: str,
    reference_image_paths: Sequence[Path] = (),
    timeout_seconds: float,
    label: str = "image",
    max_attempts: int = EMPTY_OUTPUT_MAX_ATTEMPTS,
) -> tuple[Image.Image, str]:
    """Run one Codex image generation and return the image plus its closing note."""

    executable = codex_executable()
    source_image_path = Path(source_image_path)
    diagnosis = ""

    for attempt in range(1, max_attempts + 1):
        with tempfile.TemporaryDirectory(prefix="codex_image_") as raw_workspace:
            workspace = Path(raw_workspace)
            reference_filenames = _stage_workspace(
                workspace, source_image_path, reference_image_paths
            )
            (workspace / SCHEMA_FILENAME).write_text(
                json.dumps(OUTCOME_SCHEMA), encoding="utf-8"
            )
            codex_prompt = build_codex_prompt(prompt, reference_filenames)

            command = [
                executable,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(workspace),
                "--output-schema",
                str(workspace / SCHEMA_FILENAME),
                "--output-last-message",
                str(workspace / LAST_MESSAGE_FILENAME),
            ]
            # Attaching the files makes the model actually see them, rather
            # than only reading a filename and having to open it itself.
            for filename in [SCENE_FILENAME, *reference_filenames]:
                command += ["--image", str(workspace / filename)]
            # The prompt goes in on stdin, not as the trailing argument:
            # `--image` takes a variadic <FILE>..., so a positional prompt
            # after it is parsed as one more image path and Codex then exits
            # with "No prompt provided via stdin".

            started = time.monotonic()
            log(f"{label}: asking Codex to draw it (try {attempt} of {max_attempts})", indent=2)
            try:
                with heartbeat(f"{label}: Codex is still drawing"):
                    completed = subprocess.run(
                        command,
                        input=codex_prompt,
                        capture_output=True,
                        text=True,
                        env=_codex_environment(),
                        cwd=workspace,
                        timeout=timeout_seconds,
                    )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Codex did not finish drawing within {format_duration(timeout_seconds)}"
                ) from exc

            elapsed = format_duration(time.monotonic() - started)
            outcome = _read_outcome(workspace)
            status = str(outcome.get("status", "")).strip().lower()
            note = str(outcome.get("reason", "")).strip()

            if completed.returncode != 0:
                raise RuntimeError(
                    f"Codex exited with code {completed.returncode} after {elapsed}. "
                    f"{note or (completed.stderr or '').strip()[-600:]}"
                )

            if status == "refused":
                raise ImageEditRefused(
                    "Codex declined to draw this request and will keep declining it, "
                    f"so it was not retried. {note}",
                    reason=note or "refused",
                )

            output_path = workspace / OUTPUT_FILENAME
            if output_path.is_file():
                image = Image.open(output_path).convert("RGB")
                _validate_aspect_ratio(image)
                log(f"{label}: Codex finished in {elapsed}", indent=2)
                return image, note

            diagnosis = (
                f"Codex reported {status or 'nothing'} but wrote no {OUTPUT_FILENAME} "
                f"after {elapsed}. {note}"
            )
            log(f"{label}: {diagnosis}", indent=2, error=True)

    raise RuntimeError(
        f"Codex produced no image after {max_attempts} attempts. {diagnosis}"
    )
