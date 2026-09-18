"""Thin manual harness for the orchestrator's contracted stills tool.

Unlike the legacy script this replaces, choosing neither `--shot` nor `--all`
is now a hard error rather than an implicit "generate shot 1" default.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from orchestrator.contracts.analyzed_assets import AnalyzedAssetsContract
from orchestrator.contracts.subtitle_cues import SubtitleCuesContract
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.settings import load_settings
from orchestrator.state.run_manifest import _atomic_write_json
from orchestrator.tools.gemini_tools import build_still_spec, generate_still_image


VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")
MANIFEST_FILE = Path("metadata/remotion_still_manifest.json")
RESULTS_FILE = Path("metadata/remotion_still_results.json")
STATUS_FILE = Path("metadata/remotion_still_status.txt")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(text: str) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(text, encoding="utf-8")


def _is_current_schema_result(item: dict) -> bool:
    """A pre-rewrite `remotion_still_results.json` entry (legacy
    `output_image_path` under `generated/remotion_stills/`, no
    `image_sha256`/`approved`) must never be carried forward into a merge
    under the current `StillResultContract` shape -- drop it instead.
    """
    local_path = item.get("local_image_path")
    return (
        isinstance(local_path, str)
        and local_path.startswith("generated/stills/")
        and "image_sha256" in item
        and "approved" in item
    )


def build_manifest() -> dict:
    visual_plan = VisualPlanContract.model_validate_json(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    analyzed = AnalyzedAssetsContract.model_validate_json(ANALYZED_ASSETS_FILE.read_text(encoding="utf-8"))
    subtitle_cues = SubtitleCuesContract.model_validate_json(SUBTITLE_CUES_FILE.read_text(encoding="utf-8"))
    assets = {asset.asset_id: asset.model_dump(mode="json") for asset in analyzed.assets}
    cues = {cue.cue_id: cue.text for cue in subtitle_cues.cues}
    specs: list[dict] = []
    for shot in visual_plan.shots:
        if shot.generation_mode != "STILL":
            continue
        asset = next((assets[asset_id] for asset_id in shot.source_asset_ids if asset_id in assets), None)
        if asset is None:
            raise ValueError(f"STILL shot {shot.sequence} has no analyzed source asset")
        subtitle_text = " ".join(cues[cue_id] for cue_id in shot.primary_subtitle_cue_ids if cue_id in cues)
        specs.append(build_still_spec(shot.model_dump(mode="json"), asset, subtitle_text))
    return {"generated_at_utc": now_utc(), "shots": specs}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate contracted Remotion still images with Gemini.")
    parser.add_argument("--plan-only", action="store_true", help="Write the still plan without generation.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--shot", type=int, help="Generate one STILL-mode shot.")
    selection.add_argument("--all", action="store_true", help="Generate every STILL-mode shot.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    manifest = build_manifest()
    _atomic_write_json(MANIFEST_FILE, manifest)
    if args.plan_only:
        write_status(f"SUCCESS\nManifest only.\nOutput: {MANIFEST_FILE}\n")
        print(f"Remotion still manifest created: {MANIFEST_FILE}")
        return

    selected = manifest["shots"]
    if args.shot is not None:
        selected = [item for item in selected if item["shot_sequence"] == args.shot]
    elif not args.all:
        raise ValueError("Choose --shot N or --all for a paid image-generation run")
    if not selected:
        raise ValueError("No matching STILL-mode shots selected")

    client = genai.Client(
        vertexai=True,
        project=settings.project_id,
        location=settings.location,
        http_options=types.HttpOptions(api_version="v1"),
    )
    existing_by_shot: dict[int, dict] = {}
    if RESULTS_FILE.exists():
        existing = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        existing_by_shot = {
            int(item["shot_sequence"]): item
            for item in existing.get("results", [])
            if _is_current_schema_result(item)
        }
    results = {"generated_at_utc": now_utc(), "results": list(existing_by_shot.values())}
    write_status(f"RUNNING\nStarted: {now_utc()}\nItems: {len(selected)}\n")
    for spec in selected:
        result, model_text = generate_still_image(
            client,
            spec,
            model=settings.image_model,
            cost_usd=settings.image_call_cost_usd,
        )
        payload = result.model_dump(mode="json")
        payload["model_text_response"] = model_text
        existing_by_shot[result.shot_sequence] = payload
        results["results"] = [existing_by_shot[key] for key in sorted(existing_by_shot)]
        _atomic_write_json(RESULTS_FILE, results)
    write_status(f"SUCCESS\nFinished: {now_utc()}\nResults: {RESULTS_FILE}\n")
    print(f"Still generation complete: {RESULTS_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        write_status("FAILED\n\n" + error)
        print(f"Still generation failed; see {STATUS_FILE}", file=sys.stderr)
        sys.exit(1)
