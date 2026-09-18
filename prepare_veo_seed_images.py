"""Thin manual harness for the orchestrator's contracted seed tool."""

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
from orchestrator.settings import DEFAULT_GCS_BUCKET_URI, load_settings
from orchestrator.state.run_manifest import _atomic_write_json
from orchestrator.tools.gemini_tools import build_seed_spec, generate_seed_image


VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
ANALYZED_ASSETS_FILE = Path("metadata/analyzed_assets.json")
SUBTITLE_CUES_FILE = Path("metadata/subtitle_cues.json")
MANIFEST_FILE = Path("metadata/veo_seed_manifest.json")
RESULTS_FILE = Path("metadata/veo_seed_results.json")
STATUS_FILE = Path("metadata/veo_seed_status.txt")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(text: str) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(text, encoding="utf-8")


def build_manifest() -> dict:
    visual_plan = VisualPlanContract.model_validate_json(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    analyzed = AnalyzedAssetsContract.model_validate_json(ANALYZED_ASSETS_FILE.read_text(encoding="utf-8"))
    subtitle_cues = SubtitleCuesContract.model_validate_json(SUBTITLE_CUES_FILE.read_text(encoding="utf-8"))
    assets = {asset.asset_id: asset.model_dump(mode="json") for asset in analyzed.assets}
    cues = {cue.cue_id: cue.text for cue in subtitle_cues.cues}
    specs: list[dict] = []
    for shot in visual_plan.shots:
        if shot.generation_mode != "VEO":
            continue
        asset = next((assets[asset_id] for asset_id in shot.source_asset_ids if asset_id in assets), None)
        if asset is None:
            raise ValueError(f"VEO shot {shot.sequence} has no analyzed source asset")
        subtitle_text = " ".join(cues[cue_id] for cue_id in shot.primary_subtitle_cue_ids if cue_id in cues)
        specs.append(build_seed_spec(shot.model_dump(mode="json"), asset, subtitle_text))
    return {"generated_at_utc": now_utc(), "shots": specs}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare contracted Veo seed images with Gemini.")
    parser.add_argument("--plan-only", action="store_true", help="Write the seed plan without generation.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--shot", type=int, help="Generate one VEO-mode shot.")
    selection.add_argument("--all", action="store_true", help="Generate every VEO-mode shot.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    manifest = build_manifest()
    _atomic_write_json(MANIFEST_FILE, manifest)
    if args.plan_only:
        write_status(f"SUCCESS\nManifest only.\nOutput: {MANIFEST_FILE}\n")
        print(f"Veo seed manifest created: {MANIFEST_FILE}")
        return

    selected = manifest["shots"]
    if args.shot is not None:
        selected = [item for item in selected if item["shot_sequence"] == args.shot]
    elif not args.all:
        raise ValueError("Choose --shot N or --all for a paid image-generation run")
    if not selected:
        raise ValueError("No matching VEO-mode shots selected")
    if not settings.gcs_bucket_uri or settings.gcs_bucket_uri == DEFAULT_GCS_BUCKET_URI:
        raise ValueError(
            "GCS_BUCKET_URI must be explicitly configured (not empty or the default placeholder) "
            "before a paid Gemini seed-generation call"
        )

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
            int(item["shot_sequence"]): item for item in existing.get("results", [])
        }
    results = {"generated_at_utc": now_utc(), "results": list(existing_by_shot.values())}
    write_status(f"RUNNING\nStarted: {now_utc()}\nItems: {len(selected)}\n")
    for spec in selected:
        seed, model_text = generate_seed_image(
            client,
            spec,
            model=settings.image_model,
            cost_usd=settings.image_call_cost_usd,
        )
        payload = seed.model_dump(mode="json")
        payload["model_text_response"] = model_text
        existing_by_shot[seed.shot_sequence] = payload
        results["results"] = [existing_by_shot[key] for key in sorted(existing_by_shot)]
        _atomic_write_json(RESULTS_FILE, results)
    write_status(f"SUCCESS\nFinished: {now_utc()}\nResults: {RESULTS_FILE}\n")
    print(f"Seed generation complete: {RESULTS_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        write_status("FAILED\n\n" + error)
        print(f"Seed image generation failed; see {STATUS_FILE}", file=sys.stderr)
        sys.exit(1)
