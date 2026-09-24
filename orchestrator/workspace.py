"""Where one reel's files live, and how the orchestrator gets there.

Every per-reel path in this repo is relative -- `metadata/visual_plan.json`,
`generated/stills/`, `audio/narration.wav` and twenty more. That used to mean
one reel at a time: making a second one required deleting the first, and a
partial delete was worse than none, because leftover files read as "already
done" and got skipped into a hybrid reel.

Switching the working directory into a project folder makes all of those
resolve inside it, without editing any of them. That is not a trick bolted on
from outside: fourteen test modules already isolate themselves with
`monkeypatch.chdir(tmp_path)`, so relative-paths-against-cwd is the behaviour
the code already has.

Only what is genuinely shared between reels stays pinned to the repo root --
the Remotion renderer, the regression harness's reference clip, and the
default character sheet. `repo_path()` is how those are addressed.
"""

from __future__ import annotations

import os
from pathlib import Path

# Absolute, and derived from this file rather than the working directory,
# precisely because the working directory is about to move.
REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"
SOURCE_IMAGES_DIR_NAME = "source_images"

# Set by `enter_project`. The shared-asset fallback is scoped to a live project
# on purpose: outside one -- a test in a tmp directory, a script run from
# anywhere -- "this file is missing" must keep meaning missing, not silently
# resolve to whatever the repo happens to hold.
_active_project: Path | None = None


class ProjectError(Exception):
    """The requested project cannot be used, with a message saying why."""


def repo_path(*parts: str) -> Path:
    """A path to something shared by every reel, not owned by one."""

    return REPO_ROOT.joinpath(*parts)


def list_projects() -> list[str]:
    """Names of every project folder, whether or not it has screenshots yet."""

    if not PROJECTS_DIR.is_dir():
        return []
    return sorted(entry.name for entry in PROJECTS_DIR.iterdir() if entry.is_dir())


def _known_projects_hint() -> str:
    names = list_projects()
    if not names:
        return (
            f"No projects exist yet. Make one with:\n"
            f"  mkdir -p projects/<name>/{SOURCE_IMAGES_DIR_NAME}\n"
            f"then put this reel's screenshots in it."
        )
    return "Projects that exist: " + ", ".join(names)


def resolve_project(name: str) -> Path:
    """The folder for `name`, checked to be usable before anything runs.

    Deliberately strict and never inferred. Writing a reel's output into the
    wrong project would overwrite finished work, so a typo has to fail here
    rather than resolve to something plausible.
    """

    cleaned = (name or "").strip().strip("/")
    if not cleaned:
        raise ProjectError(f"A project name is required. {_known_projects_hint()}")
    if cleaned != Path(cleaned).name:
        raise ProjectError(
            f"Project name {name!r} must be a single folder name, not a path."
        )

    project = PROJECTS_DIR / cleaned
    if not project.is_dir():
        raise ProjectError(f"No project folder at {project}. {_known_projects_hint()}")

    source_images = project / SOURCE_IMAGES_DIR_NAME
    if not source_images.is_dir():
        raise ProjectError(
            f"{project} has no {SOURCE_IMAGES_DIR_NAME}/ folder. Create it and put "
            "this reel's screenshots in it."
        )
    return project


def enter_project(project: Path) -> Path:
    """Move into the project so its relative paths become the live ones."""

    global _active_project
    os.chdir(project)
    _active_project = project
    return project


def active_project() -> Path | None:
    """The project this process is working in, if any."""

    return _active_project


def resolve_shared_asset(relative_path: str) -> Path | None:
    """A project's own copy of an asset if it has one, else the shared default.

    This is what makes the character sheet "shared unless overridden": after
    the chdir, a bare `assets/character/character.png` is the project's own,
    and the same relative path under the repo root is the one every reel gets
    when it does not bring its own. Returns None when neither exists, which is
    the documented "feature off" state.
    """

    raw = (relative_path or "").strip()
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    if candidate.is_file():
        return candidate
    if _active_project is None:
        return None
    shared = repo_path(raw)
    return shared if shared.is_file() else None
