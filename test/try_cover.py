"""Playground for the Instagram cover, drawn by Codex.

The production command (`python -m orchestrator.cover`) has Codex draw a
text-free plate and composites the hook with Pillow. That is safe -- the words
can never be misspelled -- but it looks like type dropped on a picture rather
than a designed cover. This is where we find out whether letting Codex design
the whole thing, typography included, looks better.

Edit the CONFIG block, run it, look at the picture. Nothing here touches the
pipeline or its artifacts -- output goes to test/out/ only.

    python test/try_cover.py                 # run the config below
    python test/try_cover.py --shot 4        # build from a different shot
    python test/try_cover.py --list          # which shots have artwork
    python test/try_cover.py --sweep         # run every EXPERIMENT below

Every run saves <label>.png plus <label>.txt holding the exact prompt that
produced it, so an image is always traceable back to its prompt.

Codex images cost nothing (they run on the ChatGPT subscription), so a sweep
is free -- it just takes about two minutes per variant.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from orchestrator import progress  # noqa: E402
from orchestrator.settings import load_settings  # noqa: E402
from orchestrator.tools.codex_image_tools import run_codex_image_edit  # noqa: E402
from orchestrator.tools.cover_tools import extract_video_frame  # noqa: E402

# ============================== CONFIG ==============================
# Change these, re-run, compare. That's the whole loop.

SHOT = 6                      # which shot's artwork to build the cover from
HOOK = "Fear wearing a mask"  # the words on the cover

SEND_CHARACTER = True         # send assets/character/character.png

# Which design brief to use. See BRIEFS below, or set PROMPT to override
# everything with your own text.
BRIEF = "integrated_type"
PROMPT = None                 # "..." -> used verbatim, BRIEF ignored

# =====================================================================

SHARED_RULES = (
    "The output must be a single vertical 9:16 portrait image, 1080x1920. "
    "Keep the supplied frame's art style, palette, line quality and lighting -- this is "
    "a recomposition of that artwork, not a new illustration and not a restyle, and it "
    "must not drift toward photorealism. "
    "This is a cover thumbnail: it is first seen small, and cropped to a portrait slice "
    "around its middle, so keep the subject and the words well inside the central area "
    "and away from the extreme top and bottom edges. "
)

TYPE_RULES = (
    "Render the headline EXACTLY as written, spelled letter for letter, with no extra "
    "words, no duplicated words and no invented text anywhere in the frame. "
    "It must be the only text in the image. Set it in a clean, heavy, modern sans-serif. "
    "It must be large enough to read as a small thumbnail, and must sit on an area of the "
    "artwork quiet enough that every letter stays legible -- darken or simplify that area "
    "if you need to. Do not cover the character's face with it. "
)

BRIEFS = {
    # 1. Let Codex own the whole design, type included.
    "integrated_type": (
        "Design a scroll-stopping social media cover from the supplied frame. "
        f'The headline is: "{{hook}}". ' + TYPE_RULES +
        "Compose the picture and the headline together as one designed layout, the way a "
        "magazine cover or film poster does -- the headline should feel placed by a "
        "designer, not pasted on afterwards. " + SHARED_RULES
    ),
    # 2. Same, but the story's central metaphor is the hero rather than the room.
    "metaphor_hero": (
        "Design a scroll-stopping social media cover from the supplied frame. "
        "Push in so the frame's central symbolic object -- the thing carrying the idea, "
        "not the furniture -- dominates the upper half of the image and reads instantly "
        "at thumbnail size. Keep the character present but smaller and lower. "
        f'The headline is: "{{hook}}". ' + TYPE_RULES + SHARED_RULES
    ),
    # 3. Strong poster treatment: heavy contrast, simplified shapes.
    "poster": (
        "Design a bold poster-style social media cover from the supplied frame. "
        "Simplify the scene into strong flat shapes with high contrast and deep shadow, "
        "keeping the original palette. Drop background clutter that does not serve the "
        "idea. "
        f'The headline is: "{{hook}}". ' + TYPE_RULES + SHARED_RULES
    ),
    # 4. The control: a text-free plate, which is what production draws today.
    "plate_no_text": (
        "Recompose the supplied frame into a cover image. Leave the upper third calm and "
        "uncluttered -- no faces, no busy detail there -- because a line of typography is "
        "composited into that area afterwards. " + SHARED_RULES +
        "FINAL RULE, overriding anything above: render NO text of any kind -- no words, "
        "letters, numbers, captions, logos or watermarks anywhere in the frame."
    ),
}

# ============================ EXPERIMENTS ============================
# Used by --sweep. Each row: (label, overrides-dict)

EXPERIMENTS = [
    ("integrated_type", {"BRIEF": "integrated_type"}),
    ("metaphor_hero",   {"BRIEF": "metaphor_hero"}),
    ("poster",          {"BRIEF": "poster"}),
    ("plate_no_text",   {"BRIEF": "plate_no_text"}),
]

# =====================================================================

OUT = Path(__file__).resolve().parent / "out"
PRODUCTION_ASSETS = REPO / "metadata" / "production_assets.json"
VISUAL_PLAN = REPO / "metadata" / "visual_plan.json"
CHARACTER = REPO / "assets" / "character" / "character.png"


def approved_shots() -> dict[int, dict]:
    data = json.loads(PRODUCTION_ASSETS.read_text(encoding="utf-8"))
    return {
        int(sequence): entry
        for sequence, entry in data.get("shots", {}).items()
        if entry.get("approved")
    }


def shot_goals() -> dict[int, str]:
    data = json.loads(VISUAL_PLAN.read_text(encoding="utf-8"))
    return {shot["sequence"]: shot["shot_goal"] for shot in data.get("shots", [])}


def base_frame_for(sequence: int) -> Path:
    entry = approved_shots()[sequence]
    path = REPO / str(entry["local_path"])
    if path.suffix.lower() == ".png":
        return path
    OUT.mkdir(parents=True, exist_ok=True)
    frame = OUT / f"_frame_shot{sequence:02d}.png"
    return extract_video_frame(path, frame)


def build_prompt(shot: int, hook: str, brief: str, override: str | None) -> str:
    if override:
        return override
    goal = shot_goals().get(shot, "")
    body = BRIEFS[brief].replace("{hook}", hook)
    return f"{body} The moment this frame carries: {goal}"


def run_one(label: str, *, shot: int, hook: str, brief: str, override: str | None,
            send_character: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    slug = f"cover_shot{shot:02d}_{label}_{stamp}"
    prompt = build_prompt(shot, hook, brief, override)
    references = [CHARACTER] if send_character and CHARACTER.is_file() else []

    note = ""
    error = ""
    try:
        frame = base_frame_for(shot)
        image, note = run_codex_image_edit(
            source_image_path=frame,
            prompt=prompt,
            reference_image_paths=references,
            timeout_seconds=load_settings().codex_image_timeout_seconds,
            label=label,
        )
        image.save(OUT / f"{slug}.png", format="PNG", optimize=True)
        result = f"IMAGE {image.size[0]}x{image.size[1]} -> {slug}.png"
    except Exception as exc:  # noqa: BLE001 - a playground reports, never raises
        error = f"{type(exc).__name__}: {exc}"
        result = f"NO IMAGE   {error}"

    # The .txt is always written, including on a failure -- that is exactly
    # when you want to know what was sent.
    (OUT / f"{slug}.txt").write_text(
        f"RESULT: {result}\n"
        f"shot: {shot}\nhook: {hook}\nbrief: {brief}\n"
        f"references: {[str(r) for r in references]}\n"
        f"codex note: {note}\n\n--- PROMPT ---\n{prompt}\n",
        encoding="utf-8",
    )
    print(f"  {label:18} {result}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Cover-image playground (Codex).")
    parser.add_argument("--shot", type=int, default=SHOT)
    parser.add_argument("--hook", default=HOOK)
    parser.add_argument("--brief", default=BRIEF, choices=sorted(BRIEFS))
    parser.add_argument("--list", action="store_true", help="show which shots have artwork")
    parser.add_argument("--show-prompt", action="store_true", help="print the prompt and exit (free)")
    parser.add_argument("--sweep", action="store_true", help="run every EXPERIMENT")
    args = parser.parse_args()

    progress.start_run(None)

    if args.list:
        goals = shot_goals()
        for sequence, entry in sorted(approved_shots().items()):
            print(f"  shot {sequence}: {entry['local_path']}")
            print(f"      {goals.get(sequence, '')[:110]}")
        return 0

    if args.show_prompt:
        print(build_prompt(args.shot, args.hook, args.brief, PROMPT))
        return 0

    if args.sweep:
        print(f"sweeping {len(EXPERIMENTS)} briefs on shot {args.shot} — free, ~2 min each\n")
        for label, overrides in EXPERIMENTS:
            run_one(
                label, shot=args.shot, hook=args.hook,
                brief=overrides.get("BRIEF", args.brief), override=PROMPT,
                send_character=overrides.get("SEND_CHARACTER", SEND_CHARACTER),
            )
        print(f"\nlook in {OUT}")
        return 0

    run_one(
        args.brief, shot=args.shot, hook=args.hook, brief=args.brief,
        override=PROMPT, send_character=SEND_CHARACTER,
    )
    print(f"\nlook in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
