"""Instagram cover generation: `python -m orchestrator.cover --project <name>`.

A command of its own, deliberately outside `orchestrator/run.py`'s seven-stage
chain. The reel is made first and watched; only if it is worth posting is a
cover drawn. Nothing in the reel pipeline reads anything written here, and
re-running the reel neither triggers nor invalidates a cover.

The split follows the same line as the rest of the repo. Claude judges what
cannot be measured -- which frame is most arresting, which of the reel's own
lines hooks hardest -- and Codex designs the finished cover, headline included.
Deterministic Python does what has a correct answer: resolving the frame and
verifying the hook really came from the narration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

from orchestrator.agents.cover_agent import plan_cover
from orchestrator.contracts.cover import (
    COVER_DIR,
    COVER_PLAN_FILE,
    MAX_HOOK_WORDS,
    CoverPlanContract,
)
from orchestrator.contracts.final_story_plan import FinalStoryPlanContract
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.progress import banner, format_duration, log, start_run
from orchestrator.workspace import ProjectError, enter_project, resolve_project
from orchestrator.settings import SettingsError, load_settings
from orchestrator.state.production_assets import PRODUCTION_ASSETS_FILE, load_production_assets
from orchestrator.state.run_manifest import (
    RUN_STATE_DIR,
    _atomic_write_json,
    build_failure_report,
    write_failure_report,
)
from orchestrator.tools.codex_image_tools import run_codex_image_edit
from orchestrator.tools.cover_tools import extract_video_frame, write_grid_preview
from orchestrator.tools.deterministic_tools import normalize_word, tokenize
from orchestrator.tools.gemini_tools import resolve_character_references

FINAL_STORY_PLAN_FILE = Path("metadata/final_story_plan.json")
VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")

COVER_FILE = COVER_DIR / "cover.png"
COVER_GRID_FILE = COVER_DIR / "cover_grid.png"
COVER_BASE_FRAME_FILE = COVER_DIR / "base_frame.png"

MAX_COVER_ATTEMPTS = 3


def _halt(reason: str, *, attempt_count: int = 0, partial: Optional[list[str]] = None) -> int:
    report = build_failure_report(
        "cover_agent", "CoverPlanContract", reason,
        attempt_count=attempt_count, partial_artifact_paths=partial,
    )
    path = write_failure_report(report, output_dir=RUN_STATE_DIR)
    log(f"STOPPED — {reason}", error=True)
    log(f"Details of what went wrong: {path}", error=True)
    return 1


def hook_is_from_narration(hook_text: str, narration_script: str) -> bool:
    """True when every word of the hook already appears in the narration.

    The cover and the opening seconds of voiceover have to agree, so the hook
    must be the reel's own words -- quoted or lightly compressed -- rather than
    marketing copy the agent invented. Checked here rather than trusted,
    because an invented promise is exactly what an eager writer produces.
    """

    spoken = {normalize_word(word) for word in tokenize(narration_script)}
    hook_words = [normalize_word(word) for word in tokenize(hook_text)]
    if not hook_words:
        return False
    return all(word in spoken for word in hook_words)


def _approved_sequences() -> dict[int, dict]:
    """Shots whose artwork actually exists, keyed by sequence."""

    try:
        assets = load_production_assets(path=PRODUCTION_ASSETS_FILE)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    approved: dict[int, dict] = {}
    for sequence, entry in (assets.shots or {}).items():
        payload = entry.model_dump(mode="json") if hasattr(entry, "model_dump") else dict(entry)
        if payload.get("approved") and Path(str(payload.get("local_path", ""))).is_file():
            approved[int(sequence)] = payload
    return approved


def resolve_base_frame(entry: dict) -> Path:
    """A PNG to draw the cover from, taking a frame out of a clip if needed."""

    local_path = Path(str(entry["local_path"]))
    if local_path.suffix.lower() == ".png":
        return local_path
    log(f"Taking a still frame out of {local_path.name}", indent=2)
    return extract_video_frame(local_path, COVER_BASE_FRAME_FILE)


def build_cover_prompt(plan: CoverPlanContract, shot_goal: str) -> str:
    """The design brief for Codex, which draws the headline as well as the art.

    The shape of this brief is the `metaphor_hero` variant from
    `test/try_cover.py`, which beat the alternatives in a side-by-side sweep.
    The instruction that made the difference was not about typography at all:
    it was making the story's symbolic object the hero and shrinking the
    character, which is what makes the cover readable as a thumbnail.
    """

    return (
        "Design a scroll-stopping social media cover from the supplied frame. "
        "Push in so the frame's central symbolic object -- the thing carrying the idea, "
        "not the furniture -- dominates the upper half of the image and reads instantly "
        "at thumbnail size. Keep the character present but smaller and lower. "
        f'The headline is: "{plan.hook_text}". '
        "Render the headline EXACTLY as written, spelled letter for letter, with no extra "
        "words, no duplicated words and no invented text anywhere in the frame. It must be "
        "the only text in the image -- no captions, signage, logos or watermarks. Set it in "
        "a clean, heavy, modern sans-serif, large enough to read as a small thumbnail, on "
        "an area quiet enough that every letter stays legible; darken or simplify that area "
        "if you need to. Do not cover the character's face with it. Compose the picture and "
        "the headline together as one designed layout, the way a film poster does -- the "
        "headline should feel placed by a designer, not pasted on afterwards. "
        f"The moment this frame carries: {shot_goal} "
        f"Art direction: {plan.art_direction} "
        "The output must be a single vertical 9:16 portrait image, 1080x1920. Keep the "
        "supplied frame's art style, palette, line quality and lighting -- this is a "
        "recomposition of that artwork, not a new illustration and not a restyle, and it "
        "must not drift toward photorealism. "
        "PLACEMENT, and this matters more than it sounds: the ENTIRE headline -- first "
        "word to closing punctuation -- must sit inside a horizontal band running from "
        "one-fifth of the way down the image to halfway down. Not touching the top edge, "
        "and never in the bottom third. The band is bounded at both ends for a reason: "
        "the app lays its own username, caption and audio strip over the bottom, and the "
        "profile grid crops the tall image to a portrait slice around its middle, so a "
        "headline set too low loses its last line and one set too high loses its first. "
        "Both look perfectly fine in the file and broken everywhere the cover is actually "
        "seen. Put the symbolic object and the character below the headline, inside the "
        "same middle region."
    )


async def _plan_with_retries(
    *, story_plan: FinalStoryPlanContract, visual_plan: VisualPlanContract,
    approved: dict[int, dict], budget_usd: float,
) -> tuple[Optional[CoverPlanContract], float, str]:
    """Ask the agent for a plan until it is valid or the ceiling is reached."""

    shots = [
        {
            "sequence": shot.sequence,
            "generation_mode": shot.generation_mode,
            "shot_goal": shot.shot_goal,
            "frame_composition": shot.frame_composition,
        }
        for shot in visual_plan.shots
    ]
    story_arc = story_plan.story_arc.model_dump(mode="json")
    narration = story_plan.narration_script
    spent = 0.0
    correction = ""

    for attempt in range(1, MAX_COVER_ATTEMPTS + 1):
        log(f"Try {attempt} of {MAX_COVER_ATTEMPTS} · choosing the frame and the hook")
        result = await plan_cover(
            story_arc=story_arc,
            narration_script=narration,
            shots=shots,
            approved_sequences=sorted(approved),
            max_budget_usd=max(budget_usd - spent, 0.01),
            correction=correction,
        )
        cost = result.total_cost_usd
        if cost is not None and math.isfinite(cost) and cost >= 0:
            spent += cost

        try:
            if result.is_error:
                raise ValueError(f"Claude failed ({result.subtype}): {result.errors or result.result}")
            plan = CoverPlanContract.model_validate(result.structured_output)
            if plan.base_shot_sequence not in approved:
                raise ValueError(
                    f"Shot {plan.base_shot_sequence} has no approved artwork. "
                    f"Choose one of: {sorted(approved)}"
                )
            if plan.hook_word_count() > MAX_HOOK_WORDS:
                raise ValueError(
                    f"hook_text is {plan.hook_word_count()} words; keep it to "
                    f"{MAX_HOOK_WORDS} or fewer"
                )
            if not hook_is_from_narration(plan.hook_text, narration):
                raise ValueError(
                    "hook_text uses words that are not in the narration. Quote a phrase "
                    "from the narration or compress one; do not invent new copy."
                )
        except ValueError as exc:
            correction = str(exc)
            log(f"Try {attempt} didn't work: {correction}", error=True)
            continue
        return plan, spent, ""

    return None, spent, correction


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.cover",
        description="Generate an Instagram cover for the reel already in this directory.",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Name of the folder under projects/ holding the reel to make a cover for.",
    )
    parser.add_argument("--hook", default="", help="Use this exact hook text instead of asking Claude.")
    parser.add_argument("--shot", type=int, default=None, help="Force the cover to use this shot's artwork.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if a cover plan already exists.")
    args = parser.parse_args(argv)

    try:
        project = enter_project(resolve_project(args.project))
    except ProjectError as exc:
        print(f"STOPPED — {exc}", file=sys.stderr)
        return 1

    log_path = start_run(RUN_STATE_DIR)
    banner(f"Making the Instagram cover for project '{args.project}'")
    log(f"Everything this run writes stays in {project}")
    log(f"A copy of everything printed here is being saved to {log_path}")

    try:
        settings = load_settings()
    except SettingsError as exc:
        return _halt(str(exc))

    try:
        story_plan = FinalStoryPlanContract.model_validate_json(
            FINAL_STORY_PLAN_FILE.read_text(encoding="utf-8")
        )
        visual_plan = VisualPlanContract.model_validate_json(
            VISUAL_PLAN_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _halt(f"Cannot read this reel's plan; make the reel first: {exc}")

    approved = _approved_sequences()
    if not approved:
        return _halt("No approved shot artwork found, so there is nothing to build a cover from")
    log(f"{len(approved)} approved shot(s) available: {sorted(approved)}")

    if COVER_FILE.is_file() and not args.force:
        log(f"A cover already exists at {COVER_FILE} — pass --force to replace it")
        return 0

    plan: Optional[CoverPlanContract] = None
    spent = 0.0
    if args.hook and args.shot is not None:
        # Both choices supplied, so there is no judgment left to delegate.
        if args.shot not in approved:
            return _halt(f"Shot {args.shot} has no approved artwork; available: {sorted(approved)}")
        plan = CoverPlanContract(
            base_shot_sequence=args.shot,
            hook_text=args.hook,
            art_direction="Operator-specified frame and hook; keep the frame as drawn.",
            rationale="Chosen by the operator.",
        )
        log("Using the frame and hook you supplied — skipping the planning step")
    else:
        plan, spent, failure = asyncio.run(
            _plan_with_retries(
                story_plan=story_plan, visual_plan=visual_plan,
                approved=approved, budget_usd=settings.max_budget_usd,
            )
        )
        if plan is None:
            return _halt(f"Retry ceiling exhausted: {failure}", attempt_count=MAX_COVER_ATTEMPTS)
        if args.shot is not None:
            if args.shot not in approved:
                return _halt(f"Shot {args.shot} has no approved artwork; available: {sorted(approved)}")
            plan = plan.model_copy(update={"base_shot_sequence": args.shot})
        if args.hook:
            plan = plan.model_copy(update={"hook_text": args.hook})

    _atomic_write_json(COVER_PLAN_FILE, plan.model_dump(mode="json"))
    log(f"Cover plan: shot {plan.base_shot_sequence} · \"{plan.hook_text}\"")
    log(f"Why: {plan.rationale}", indent=2)

    entry = approved[plan.base_shot_sequence]
    shot_goal = next(
        (shot.shot_goal for shot in visual_plan.shots if shot.sequence == plan.base_shot_sequence),
        "",
    )
    try:
        base_frame = resolve_base_frame(entry)
        image, note = run_codex_image_edit(
            source_image_path=base_frame,
            prompt=build_cover_prompt(plan, shot_goal),
            reference_image_paths=resolve_character_references(),
            timeout_seconds=settings.codex_image_timeout_seconds,
            label="Cover",
        )
        COVER_FILE.parent.mkdir(parents=True, exist_ok=True)
        image.save(COVER_FILE, format="PNG", optimize=True)
        if note:
            log(note[:200], indent=2)
        log(f"Cover written to {COVER_FILE} ({image.size[0]}x{image.size[1]})", indent=2)
        write_grid_preview(COVER_FILE, COVER_GRID_FILE)
    except Exception as exc:
        return _halt(
            f"Cover artwork failed: {type(exc).__name__}: {exc}",
            partial=[str(COVER_PLAN_FILE)],
        )

    log(f"Finished! Your cover is at {COVER_FILE}")
    log(f"Check {COVER_GRID_FILE} to see what survives the profile grid crop", indent=2)
    log(f"Claude cost for this cover: ${spent:.2f}", indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
