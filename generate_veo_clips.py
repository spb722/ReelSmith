"""Thin manual harness for the orchestrator's contracted Veo tool.

There is deliberately no source-screenshot fallback. A generated clip starts
only from a VeoSeedContract under generated/veo_seeds/.
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

from orchestrator.contracts.veo import VeoSeedContract
from orchestrator.contracts.visual_plan import VisualPlanContract
from orchestrator.settings import DEFAULT_GCS_BUCKET_URI, load_settings
from orchestrator.state.run_manifest import _atomic_write_json
from orchestrator.tools.veo_tools import build_video_prompt, run_veo_generation


VISUAL_PLAN_FILE = Path("metadata/visual_plan.json")
SEED_RESULTS_FILE = Path("metadata/veo_seed_results.json")
MANIFEST_FILE = Path("metadata/veo_generation_manifest.json")
RESULTS_FILE = Path("metadata/veo_generation_results.json")
STATUS_FILE = Path("metadata/veo_generation_status.txt")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(text: str) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(text, encoding="utf-8")


def build_manifest() -> dict:
    visual_plan = VisualPlanContract.model_validate_json(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    seed_data = json.loads(SEED_RESULTS_FILE.read_text(encoding="utf-8"))
    seeds = {
        int(item["shot_sequence"]): VeoSeedContract.model_validate(
            {key: value for key, value in item.items() if key != "model_text_response"}
        )
        for item in seed_data.get("results", [])
    }
    clips: list[dict] = []
    for shot in visual_plan.shots:
        if shot.generation_mode != "VEO":
            continue
        seed = seeds.get(shot.sequence)
        if seed is None:
            raise ValueError(f"VEO shot {shot.sequence} has no contracted recomposed seed")
        if not seed.approved:
            raise ValueError(
                f"VEO shot {shot.sequence}'s seed is not approved; inspect it and rerun with --approve-seed"
            )
        seed.validate_for_shot(shot)
        prompt, negative_prompt = build_video_prompt(shot, seed)
        clips.append({
            "shot": shot.model_dump(mode="json"),
            "seed": seed.model_dump(mode="json"),
            "prompt": prompt,
            "negative_prompt": negative_prompt,
        })
    return {"generated_at_utc": now_utc(), "clips": clips}


def _approve_selected_seed(shot_sequence: int) -> None:
    seed_data = json.loads(SEED_RESULTS_FILE.read_text(encoding="utf-8"))
    matches = [
        item for item in seed_data.get("results", [])
        if int(item.get("shot_sequence", -1)) == shot_sequence
    ]
    if not matches:
        raise ValueError(f"No contracted seed result exists for shot {shot_sequence}")
    if len(matches) > 1:
        raise ValueError(
            f"Multiple contracted seed results exist for shot {shot_sequence}; refusing ambiguous approval"
        )
    matches[0]["approved"] = True
    matches[0]["qa_summary"] = "Approved by operator after visual inspection via canonical CLI."
    _atomic_write_json(SEED_RESULTS_FILE, seed_data)


def _approve_selected_clip(shot_sequence: int) -> None:
    if not RESULTS_FILE.exists():
        raise ValueError(f"No Veo generation results exist yet for shot {shot_sequence}")
    results_data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    outcomes = results_data.get("outcomes", {})
    outcome = outcomes.get(str(shot_sequence))
    if outcome is None or not outcome.get("result"):
        raise ValueError(f"No successful Veo clip result exists for shot {shot_sequence} to approve")
    outcome["result"]["approved"] = True
    outcome["result"]["qa_summary"] = "Approved by operator after visual inspection via canonical CLI."
    _atomic_write_json(RESULTS_FILE, results_data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Veo clips from approved contracted seeds.")
    parser.add_argument("--plan-only", action="store_true", help="Write the clip plan without generation.")
    parser.add_argument("--shot", type=int, help="Generate one VEO-mode shot.")
    parser.add_argument(
        "--approve-seed",
        action="store_true",
        help="Record explicit operator approval for the selected --shot before generation.",
    )
    parser.add_argument(
        "--approve-clip",
        action="store_true",
        help="Record explicit operator approval for the previously generated --shot's clip, "
        "without another paid Veo call.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.approve_seed:
        if args.shot is None:
            raise ValueError("--approve-seed requires --shot N")
        _approve_selected_seed(args.shot)
    if args.approve_clip:
        if args.shot is None:
            raise ValueError("--approve-clip requires --shot N")
        _approve_selected_clip(args.shot)
        write_status(f"SUCCESS\nApproved shot {args.shot} at {now_utc()}\nResults: {RESULTS_FILE}\n")
        print(f"Veo clip for shot {args.shot} approved: {RESULTS_FILE}")
        return
    settings = load_settings()
    manifest = build_manifest()
    _atomic_write_json(MANIFEST_FILE, manifest)
    if args.plan_only:
        write_status(f"SUCCESS\nManifest only.\nOutput: {MANIFEST_FILE}\n")
        print(f"Veo generation manifest created: {MANIFEST_FILE}")
        return
    if args.shot is None:
        raise ValueError("Choose --shot N for a paid Veo generation run")
    clips = [item for item in manifest["clips"] if item["shot"]["sequence"] == args.shot]
    if not clips:
        raise ValueError("No matching VEO-mode shot selected")
    if not settings.gcs_bucket_uri or settings.gcs_bucket_uri == DEFAULT_GCS_BUCKET_URI:
        raise ValueError(
            "GCS_BUCKET_URI must be explicitly configured (not empty or the default placeholder) "
            "before a paid Veo generation call"
        )

    client = genai.Client(
        vertexai=True,
        project=settings.project_id,
        location=settings.location,
        http_options=types.HttpOptions(api_version="v1"),
    )
    clip = clips[0]
    visual_plan = VisualPlanContract.model_validate_json(VISUAL_PLAN_FILE.read_text(encoding="utf-8"))
    shot = next(item for item in visual_plan.shots if item.sequence == args.shot)
    outcome = run_veo_generation(
        client,
        shot=shot,
        seed=VeoSeedContract.model_validate(clip["seed"]),
        project_id=settings.project_id,
        model=settings.veo_model,
        gcs_output_uri=settings.gcs_bucket_uri,
        resolution=settings.veo_resolution,
        duration_seconds=settings.veo_duration_seconds,
        poll_seconds=settings.veo_poll_seconds,
        max_poll_seconds=settings.veo_max_poll_seconds,
        attempt=1,
        cost_usd=settings.veo_call_cost_usd,
    )
    previous: dict = {}
    if RESULTS_FILE.exists():
        previous = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    outcomes = dict(previous.get("outcomes", {}))
    outcomes[str(args.shot)] = outcome.model_dump(mode="json")
    _atomic_write_json(RESULTS_FILE, {"generated_at_utc": now_utc(), "outcomes": outcomes})
    if outcome.status == "FAILURE":
        raise RuntimeError(outcome.failure.reason)
    if not outcome.result or not outcome.result.approved:
        raise RuntimeError("Veo clip requires explicit semantic QA approval before SUCCESS")
    write_status(f"SUCCESS\nFinished: {now_utc()}\nResults: {RESULTS_FILE}\n")
    print(f"Veo generation complete: {RESULTS_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        write_status("FAILED\n\n" + error)
        print(f"Veo generation failed; see {STATUS_FILE}", file=sys.stderr)
        sys.exit(1)
