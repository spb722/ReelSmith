"""Playground for the one Gemini image-edit call the pipeline makes.

Edit the CONFIG block, run it, look at the picture. Nothing here touches the
pipeline or its artifacts -- output goes to test/out/ only.

    python test/try_seed.py                  # run the config below
    python test/try_seed.py --shot 3         # override the shot
    python test/try_seed.py --list           # what shots/assets exist
    python test/try_seed.py --sweep          # run every EXPERIMENT below

Every run saves <label>.png plus <label>.txt holding the exact prompt that
produced it, so an image is always traceable back to its prompt.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PIL import Image  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from orchestrator.settings import load_settings  # noqa: E402
from orchestrator.tools.gemini_tools import (  # noqa: E402
    blocked_reason,
    build_seed_spec,
    build_still_spec,
    extract_generated_image,
)

# ============================== CONFIG ==============================
# Change these, re-run, compare. That's the whole loop.

SHOT = 6                      # which shot from metadata/visual_plan.json
MODE = "SEED"                 # "SEED" (video shots) or "STILL"

MODEL = "gemini-3.1-flash-image"
# others to try:
#   "gemini-2.5-flash-image"
#   "gemini-3.1-pro-image"          (if your project has access)

SEND_CHARACTER = True         # send assets/character/character.png
SEND_FACE = False             # also send character_face.png (blocks more often)

# None  -> use the prompt the pipeline itself would send (printed below, so you
#          can copy it here and start editing)
# "..." -> your own prompt, used verbatim
PROMPT = None

# ============================ EXPERIMENTS ============================
# Used by --sweep. Each row: (label, overrides-dict)

EXPERIMENTS = [
    ("baseline_with_character", {"SEND_CHARACTER": True}),
    ("no_character",            {"SEND_CHARACTER": False}),
    ("with_face_crop",          {"SEND_CHARACTER": True, "SEND_FACE": True}),
]

# =====================================================================

OUT = Path(__file__).resolve().parent / "out"
PLAN = REPO / "metadata" / "visual_plan.json"
ASSETS = REPO / "metadata" / "analyzed_assets.json"


def load_shot(sequence: int, mode: str) -> tuple[dict, dict]:
    if not PLAN.is_file() or not ASSETS.is_file():
        sys.exit(f"Need {PLAN} and {ASSETS}. Run the pipeline far enough to create them.")
    plan = json.loads(PLAN.read_text())
    assets = {a["asset_id"]: a for a in json.loads(ASSETS.read_text())["assets"]}
    try:
        shot = next(s for s in plan["shots"] if s["sequence"] == sequence)
    except StopIteration:
        sys.exit(f"No shot {sequence}. Try --list.")
    shot = {**shot, "generation_mode": "VEO" if mode == "SEED" else "STILL"}
    asset = assets[shot["source_asset_ids"][0]]
    return shot, asset


def list_shots() -> None:
    plan = json.loads(PLAN.read_text())
    assets = {a["asset_id"]: a for a in json.loads(ASSETS.read_text())["assets"]}
    print(f"{'shot':<5} {'mode':<6} {'secs':<6} {'figures':<8} source")
    for s in plan["shots"]:
        a = assets[s["source_asset_ids"][0]]
        vis = a["analysis"]["visual"]
        figs = len(vis.get("figure_descriptions") or [])
        secs = s.get("end_seconds", 0) - s.get("start_seconds", 0)
        print(f"{s['sequence']:<5} {s['generation_mode']:<6} {secs:<6.2f} {figs:<8} "
              f"{Path(a['source_path']).name}")
        print(f"      goal: {s['shot_goal'][:88]}")


def references(send_character: bool, send_face: bool) -> list[Path]:
    if not send_character:
        return []
    settings = load_settings()
    out = []
    body = Path(settings.character_reference_path or "")
    if body.is_file():
        out.append(body)
    if send_face:
        face = body.parent / "character_face.png"
        if face.is_file():
            out.append(face)
    return out


def run(label: str, shot_seq: int, mode: str, model: str,
        send_character: bool, send_face: bool, prompt_override: str | None) -> bool:
    shot, asset = load_shot(shot_seq, mode)
    builder = build_seed_spec if mode == "SEED" else build_still_spec
    spec = builder(shot, asset, "Narration context.")
    prompt = prompt_override if prompt_override is not None else spec["prompt"]

    refs = references(send_character, send_face)
    source = Path(spec["source_image_path"])
    images = [Image.open(source).convert("RGB")]
    images += [Image.open(r).convert("RGB") for r in refs]

    print(f"\n--- {label} ---")
    print(f"  shot {shot_seq} ({mode})   model={model}")
    print(f"  source: {source.name}")
    print(f"  references: {[r.name for r in refs] or 'none'}")
    print(f"  prompt: {len(prompt)} chars{'  (CUSTOM)' if prompt_override else ''}")

    settings = load_settings()
    client = genai.Client(
        vertexai=True, project=settings.project_id, location=settings.location,
        http_options=types.HttpOptions(api_version="v1"),
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=[*images, prompt],
            config=types.GenerateContentConfig(
                response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
            ),
        )
    except Exception as exc:
        print(f"  RESULT: API ERROR  {type(exc).__name__}: {exc}")
        return False

    image, model_text = extract_generated_image(response)
    blocked = blocked_reason(response)

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    base = OUT / f"shot{shot_seq:02d}_{label}_{stamp}"
    base.with_suffix(".txt").write_text(
        f"shot={shot_seq} mode={mode} model={model}\n"
        f"references={[str(r) for r in refs]}\n"
        f"blocked={blocked or 'no'}\n"
        f"model_text={model_text!r}\n\n{prompt}\n",
        encoding="utf-8",
    )

    if image is None:
        print(f"  RESULT: NO IMAGE   blocked={blocked or 'not blocked'}")
        if model_text:
            print(f"  model said: {model_text[:200]!r}")
        print(f"  prompt saved -> {base.with_suffix('.txt')}")
        return False

    image.save(base.with_suffix(".png"))
    print(f"  RESULT: OK  {image.size[0]}x{image.size[1]}")
    print(f"  -> {base.with_suffix('.png')}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini image-edit playground")
    ap.add_argument("--shot", type=int, default=SHOT)
    ap.add_argument("--mode", choices=["SEED", "STILL"], default=MODE)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--list", action="store_true", help="list shots and exit")
    ap.add_argument("--sweep", action="store_true", help="run every EXPERIMENT")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the pipeline's prompt and exit (no API call)")
    args = ap.parse_args()

    if args.list:
        list_shots()
        return 0

    if args.show_prompt:
        shot, asset = load_shot(args.shot, args.mode)
        builder = build_seed_spec if args.mode == "SEED" else build_still_spec
        print(builder(shot, asset, "Narration context.")["prompt"])
        return 0

    if args.sweep:
        results = []
        for label, over in EXPERIMENTS:
            ok = run(label, args.shot, args.mode, over.get("MODEL", args.model),
                     over.get("SEND_CHARACTER", SEND_CHARACTER),
                     over.get("SEND_FACE", SEND_FACE),
                     over.get("PROMPT", PROMPT))
            results.append((label, ok))
        print("\n===== SWEEP =====")
        for label, ok in results:
            print(f"  {'OK     ' if ok else 'BLOCKED'}  {label}")
        return 0

    run("manual", args.shot, args.mode, args.model, SEND_CHARACTER, SEND_FACE, PROMPT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
