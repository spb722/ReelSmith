"""Move the reel sitting in the repo root into a project folder.

    python -m orchestrator.migrate <name>

A one-off for the reel that was made before projects existed. Everything
per-reel moves; the shared renderer, the shared character sheet and the code
stay where they are. The rendered MP4 is *copied* rather than moved, because
`remotion/out/` is scratch the next render overwrites and the project wants
its own keeping copy.

Refuses to run if the target project already holds a reel, so it can never
overwrite finished work.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional, Sequence

from orchestrator.workspace import (
    PROJECTS_DIR,
    SOURCE_IMAGES_DIR_NAME,
    ProjectError,
    repo_path,
)

# Everything one reel owns. `remotion/public/` is absent on purpose: it is
# staging that every render rewrites, not something a reel keeps.
PER_REEL_DIRS = ("metadata", "generated", "audio", "orchestrator_runs")
SOURCE_DIR = "source_images"
RENDERED_REEL = Path("remotion/out/book_reel.mp4")
FINISHED_REEL_NAME = "book_reel.mp4"


def plan_migration(name: str) -> tuple[Path, list[Path]]:
    """The destination and what exists to move, or a clear refusal."""

    cleaned = (name or "").strip().strip("/")
    if not cleaned or cleaned != Path(cleaned).name:
        raise ProjectError(f"Project name {name!r} must be a single folder name.")

    destination = PROJECTS_DIR / cleaned
    if destination.exists() and any(destination.iterdir()):
        raise ProjectError(
            f"{destination} already exists and is not empty. Pick another name rather "
            "than risking a finished reel."
        )

    movable = [repo_path(d) for d in (SOURCE_DIR, *PER_REEL_DIRS) if repo_path(d).is_dir()]
    if not movable:
        expected = ", ".join((SOURCE_DIR, *PER_REEL_DIRS))
        raise ProjectError(
            f"Nothing to migrate: none of {expected} exists at the repo root."
        )
    return destination, movable


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.migrate",
        description="Move the reel at the repo root into projects/<name>/.",
    )
    parser.add_argument("name", help="Project folder to create under projects/.")
    args = parser.parse_args(argv)

    try:
        destination, movable = plan_migration(args.name)
    except ProjectError as exc:
        print(f"STOPPED — {exc}", file=sys.stderr)
        return 1

    destination.mkdir(parents=True, exist_ok=True)
    for source in movable:
        target = destination / source.name
        shutil.move(str(source), str(target))
        print(f"  moved {source.name}/ -> {target}")

    rendered = repo_path(str(RENDERED_REEL))
    if rendered.is_file():
        shutil.copy2(rendered, destination / FINISHED_REEL_NAME)
        print(f"  copied {RENDERED_REEL} -> {destination / FINISHED_REEL_NAME}")

    source_images = destination / SOURCE_IMAGES_DIR_NAME
    if not source_images.is_dir():
        print(
            f"\nNote: {source_images} does not exist, so this project cannot be re-run "
            "until you put its screenshots back.",
            file=sys.stderr,
        )

    print(f"\nDone. The reel now lives in {destination}")
    print(f"Re-run it with:  python -m orchestrator.run --project {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
