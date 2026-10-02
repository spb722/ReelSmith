"""Instagram caption writing: `python -m orchestrator.caption --project <name>`.

A command of its own, outside `orchestrator/run.py`'s stage chain and apart
from the cover. It needs only the reel's final narration, so it can run as
soon as the narration is written. Nothing else reads what it writes.

Claude writes the caption in parts (the `instagram-caption` skill says how);
Python checks the limits that have a correct answer and joins the parts.
Every earlier caption is kept as `caption_v<N>.txt`, so nothing is lost to a
fresh caption, a revision, a hand edit or a restore.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import signal
import sys
from pathlib import Path
from typing import Optional, Sequence

from orchestrator.agents.caption_agent import write_caption
from orchestrator.contracts.caption import CAPTION_DIR, CAPTION_FILE, CAPTION_PLAN_FILE, CaptionPlanContract
from orchestrator.progress import banner, log, run_status, start_run
from orchestrator.settings import SettingsError, load_dotenv_file, load_settings
from orchestrator.state.run_manifest import (
    RUN_STATE_DIR,
    _atomic_write_json,
    build_failure_report,
    write_failure_report,
)
from orchestrator.workspace import ProjectError, enter_project, resolve_project

FINAL_STORY_PLAN_FILE = Path("metadata/final_story_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
CAPTION_HISTORY_FILE = CAPTION_DIR / "history.json"
# Read by the UI (ui/CONTRACT.md section 5).
CAPTION_STATUS_FILE = RUN_STATE_DIR / "caption_status.json"

MAX_CAPTION_ATTEMPTS = 3


def _halt(reason: str, *, attempt_count: int = 0) -> int:
    report = build_failure_report("caption_agent", "CaptionPlanContract", reason, attempt_count=attempt_count)
    path = write_failure_report(report, output_dir=RUN_STATE_DIR)
    run_status.halt(reason)
    log(f"STOPPED — {reason}", error=True)
    log(f"Details of what went wrong: {path}", error=True)
    return 1


def keep_current_version(project: Path, change: str, feedback: Optional[str] = None) -> Optional[Path]:
    """Copy the live caption to the next caption_v<N>.txt and record what replaced it.

    `change` is "fresh", "feedback", "edit" or "restore". Used by this command
    and by the UI server (hand edits and restores), hence the explicit folder.
    """

    current = project / CAPTION_FILE
    if not current.is_file():
        return None
    n = 1
    while (project / CAPTION_DIR / f"caption_v{n}.txt").exists():
        n += 1
    kept = project / CAPTION_DIR / f"caption_v{n}.txt"
    shutil.copy2(current, kept)
    history_path = project / CAPTION_HISTORY_FILE
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        history = []
    history.append({"previous_caption": kept.name, "change": change, "feedback": feedback})
    _atomic_write_json(history_path, history)
    return kept


def source_text() -> list[dict]:
    """Each screenshot's heading, text and named entities, for crediting the book."""

    try:
        assets = json.loads(ANALYZED_ASSETS_FILE.read_text(encoding="utf-8")).get("assets") or []
    except (FileNotFoundError, ValueError, OSError):
        return []
    out = []
    for asset in assets:
        analysis = asset.get("analysis") or {}
        text = analysis.get("source_text") or {}
        out.append({
            "heading": text.get("heading", ""),
            "body_text": text.get("body_text", ""),
            "prominent_quote": text.get("prominent_quote", ""),
            "named_entities": [
                {"name": e.get("name"), "entity_type": e.get("entity_type")}
                for e in analysis.get("named_entities") or []
            ],
        })
    return out


async def _write_with_retries(
    *, story_plan: dict, budget_usd: float, current_caption: str, feedback: str,
) -> tuple[Optional[CaptionPlanContract], float, str]:
    """Ask the agent for a caption until it meets every limit or the ceiling is reached."""

    sources = source_text()
    spent = 0.0
    correction = ""
    for attempt in range(1, MAX_CAPTION_ATTEMPTS + 1):
        log(f"Try {attempt} of {MAX_CAPTION_ATTEMPTS} · writing the caption")
        result = await write_caption(
            story_title=story_plan.get("story_title", ""),
            narration_script=story_plan["narration_script"],
            story_arc=story_plan.get("story_arc") or {},
            source_text=sources,
            max_budget_usd=max(budget_usd - spent, 0.01),
            current_caption=current_caption,
            feedback=feedback,
            correction=correction,
        )
        cost = result.total_cost_usd
        if cost is not None and math.isfinite(cost) and cost >= 0:
            spent += cost
        try:
            if result.is_error:
                raise ValueError(f"Claude failed ({result.subtype}): {result.errors or result.result}")
            plan = CaptionPlanContract.model_validate(result.structured_output)
            problems = plan.problems()
            if problems:
                raise ValueError("; ".join(problems))
        except ValueError as exc:
            correction = str(exc)
            log(f"Try {attempt} didn't work: {correction}", error=True)
            continue
        return plan, spent, ""
    return None, spent, correction


def _stop_by_operator(signum, frame) -> None:
    run_status.halt("Stopped by you", stop_kind="operator")
    raise SystemExit(128 + signum)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The caption run, with its `caption_status.json` kept current for the UI."""

    try:
        previous_sigterm = signal.signal(signal.SIGTERM, _stop_by_operator)
    except ValueError:  # not the main thread
        previous_sigterm = None
    exit_code = None
    try:
        exit_code = _main(argv)
        return exit_code
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        run_status.close(exit_code)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.caption",
        description="Write the Instagram caption and hashtags for a project's reel.",
    )
    parser.add_argument("--project", required=True, help="Name of the folder under projects/.")
    parser.add_argument("--force", action="store_true", help="Write a fresh caption even if one exists.")
    parser.add_argument(
        "--feedback", default="",
        help='Revise the current caption, e.g. "shorter hook". The current one is kept as caption_v<N>.txt.',
    )
    args = parser.parse_args(argv)
    load_dotenv_file()

    try:
        project = enter_project(resolve_project(args.project))
    except ProjectError as exc:
        print(f"STOPPED — {exc}", file=sys.stderr)
        return 1

    log_path = start_run(RUN_STATE_DIR)
    run_status.begin(CAPTION_STATUS_FILE, kind="caption")
    banner(f"Writing the Instagram caption for project '{args.project}'")
    log(f"Everything this run writes stays in {project}")
    log(f"A copy of everything printed here is being saved to {log_path}")

    try:
        settings = load_settings()
    except SettingsError as exc:
        return _halt(str(exc))

    try:
        story_plan = json.loads(FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8"))
        if not str(story_plan.get("narration_script") or "").strip():
            raise ValueError("it has no narration_script")
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt(f"Cannot read this reel's narration; write the narration first: {exc}")

    feedback = args.feedback.strip()
    current_caption = ""
    if feedback:
        if not CAPTION_FILE.is_file():
            return _halt("There is no caption to revise yet; write one first without --feedback")
        current_caption = CAPTION_FILE.read_text(encoding="utf-8")
        log(f'Revising the current caption: "{feedback}"')
    elif CAPTION_FILE.is_file() and not args.force:
        log(f"A caption already exists at {CAPTION_FILE} — pass --force to write a fresh one")
        return 0

    run_status.set_percent(10)
    plan, spent, failure = asyncio.run(_write_with_retries(
        story_plan=story_plan, budget_usd=settings.max_budget_usd,
        current_caption=current_caption, feedback=feedback,
    ))
    if plan is None:
        return _halt(f"Retry ceiling exhausted: {failure}", attempt_count=MAX_CAPTION_ATTEMPTS)

    kept = keep_current_version(Path.cwd(), "feedback" if feedback else "fresh", feedback or None)
    if kept is not None:
        log(f"Kept the previous caption as {CAPTION_DIR / kept.name}")
    CAPTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = plan.assemble()
    CAPTION_FILE.write_text(text + "\n", encoding="utf-8")
    _atomic_write_json(CAPTION_PLAN_FILE, plan.model_dump(mode="json"))
    log(f"Caption written to {CAPTION_FILE} ({len(text)} characters)")
    log(f"Hashtags: {' '.join(plan.hashtags)}", indent=2)
    log(f"Claude cost for this caption: ${spent:.2f}", indent=2)
    log(f"Finished! Your caption is at {CAPTION_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
