-<!-- bmad:context -->
<!-- Verified 2026-09-15. No git commits exist yet in this repo (working tree only) — replace this line with the verified commit SHA on the first refresh after an initial commit. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## book_reels

Turns book/idea source screenshots into a 9:16 short-form "reel" video via a hybrid pipeline: AI for understanding/generation/creative transformation, deterministic code for timing, validation, composition, and final render. Today it's 14 manually-run root Python scripts with no orchestrator. Read `docs/book_reels_architecture.md`, `docs/book_reels_manual_interventions_and_automation_map.md`, and `docs/book_reels_troubleshooting_agent_playbook.md` before making pipeline changes — they are the primary source of truth for this project.

## Policy

- Never rewrite, retime, merge, split, or paraphrase the current reel's locked artifacts (`metadata/final_story_plan.json` narration, `metadata/subtitle_cues.json`, the 8-shot visual timing plan) without the user's explicit direction.
- Never feed raw screenshots directly to Veo — recompose through `prepare_veo_seed_images.py` into `generated/veo_seeds/` first.
- Never replace `build_subtitle_cues.py`'s deterministic segmentation with free-form LLM output — a prior LLM-grouping approach was unreliable and was deliberately replaced.
- Never create suffixed/versioned script copies (`_v2`, `_final`, etc.) — edit the canonical script in place; git holds history.
- Never modify the Conda `base` environment, or upgrade `google-genai` reflexively — diagnose the actual traceback first.
- Make one engineering change at a time and verify it before moving to the next pipeline stage.

## Where things are

- Pipeline runs in strict order, each stage's output required by the next: `ingest_assets` → `analyze_assets` → `story_director` → `story_quality_loop` (produces `final_story_plan.json` — not the standalone `review_story_plan`/`revise_story_plan`/`review_revised_story_plan`, which debug one stage manually and aren't in this chain) → `generate_voice` → `extract_word_timing` → `build_subtitle_cues` → `visual_director` → `prepare_veo_seed_images` / `prepare_remotion_stills` → `generate_veo_clips` → manual copy into `remotion/public/`.
- `metadata/` is the pipeline's generated database (one JSON per stage) — regenerate via the owning script, don't hand-edit.
- `generated/veo/` and `generated/veo_seeds/` mix production output with abandoned-experiment and test artifacts — verify a file against the approved production mapping in `metadata/` before reusing it; never assume the newest or largest file is the right one.
- `remotion/` is a separate npm/Remotion project, out of scope for this file.

## Running and verifying

- Activate the `kayak-video` conda environment before running any script — there is no `requirements.txt`/`pyproject.toml`/venv in this repo; it's the only place dependencies (`google-genai`, `google-auth`, `Pillow`) are installed.
- All scripts run from the repo root with no arguments, except `prepare_veo_seed_images.py`, `prepare_remotion_stills.py`, and `generate_veo_clips.py`, which take `--plan-only` / `--shot N` / `--all`.
- Auth is Google Cloud ADC against Vertex AI, not an API key — run `gcloud auth application-default login` if a script fails with an auth error.

## Conventions

- Config (`PROJECT_ID`, region, model name) is hardcoded per-script as a module constant, not centralized — expect the same literal repeated across files rather than a shared config module.

## Known pitfalls

- "Generation succeeded" is not "asset approved" — every generated image/clip needs visual QA before use downstream.
- Per-shot result files (`veo_seed_results.json`, `veo_generation_results.json`, `remotion_still_results.json`) are overwritten, not merged, on each single-shot run — don't assume an earlier shot's entry survived a later run for a different shot; check the actual on-disk asset instead.

<!-- /bmad:context -->
