- source_spec: `docs/implementation-artifacts/spec-1-1-orchestrator-scaffold-preflight-budget.md`
  summary: Add a `.gitignore` entry for `orchestrator_runs/` (and, ideally, the repo's other untracked generated directories).
  evidence: The repo has no `.gitignore` at all today, so `metadata/`, `generated/`, etc. are already untracked the same way. Real risk, but pre-existing and not caused by Story 1.1.
- source_spec: `docs/implementation-artifacts/spec-1-1-orchestrator-scaffold-preflight-budget.md`
  summary: Update AGENTS.md — the "Conventions" line stating config is hardcoded per-script (now false, since `orchestrator/settings.py` centralizes it), the dependency list omitting `google-cloud-storage`/`pytest`, and the "Where things are" section omitting `orchestrator_runs/`.
  evidence: Confirmed stale/incomplete against the current codebase (`AGENTS.md:26,32`). Fix requires editing an agent-context file, which routes to defer regardless of severity.
- source_spec: `docs/implementation-artifacts/spec-1-3-narration-writing-review-revision.md`
  summary: Root-level `CLAUDE.md` (generic behavioral-guidelines boilerplate) was already staged before this story's baseline commit and shows up in its diff, unrelated to the narration-agent feature.
  evidence: Pre-existing, not caused by Story 1.3. Fix requires editing an agent-context file, which routes to defer regardless of severity.
- source_spec: `docs/implementation-artifacts/spec-1-3-narration-writing-review-revision.md`
  summary: `run_bounded_agent_stage`'s retry loop sends `"Previous output:\nnull"` to the first live call after a persisted-but-invalid contract is found (feedback is set from the persisted-file validation error, but `previous_output` stays `None`).
  evidence: Inherited unchanged from Story 1.2's `run_screenshot_stage`, which this story's shared helper preserves verbatim per the spec's own instruction to reuse that exact retry pattern. Not caused by this story.
- source_spec: `docs/implementation-artifacts/spec-1-4-visual-shot-mode-planning.md`
  summary: Update AGENTS.md's "Pipeline runs in strict order" line (still describes the legacy Gemini-script chain, never mentions `asset_analyst`/`story_agent`/`visual_agent`) and reconcile its "locked artifacts... never rewritten without the user's explicit direction" line with the epic's own established carve-out (self-correction within an unapproved epic is not a violation, per `epic-1-context.md`'s Technical Decisions).
  evidence: Stale since Story 1.2/1.3, not caused by Story 1.4. Fix requires editing an agent-context file, which routes to defer regardless of severity.
- source_spec: `docs/implementation-artifacts/spec-1-4-visual-shot-mode-planning.md`
  summary: `metadata/visual_plan.json` is the same path three pre-existing standalone scripts (`prepare_remotion_stills.py`, `prepare_veo_seed_images.py`, `generate_veo_clips.py`) read, indexing shots by `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids` — fields the new `visual_agent`-produced `VisualPlanContract` deliberately doesn't include (no timing/subtitle source exists this early in the new pipeline). Those scripts would `KeyError` if run against a `visual_agent`-produced file.
  evidence: Real integration hazard for whichever future story wires the new pipeline into those scripts (likely Epic 2), but modifying those scripts is explicitly out of Story 1.4's scope per its frozen Never section. None of the three scripts have existing test coverage.
  resolution: Resolved by Story 2.1 (`spec-2-1-timeline-json-converter-regression-harness.md`), which added `start_seconds`/`end_seconds`/`primary_subtitle_cue_ids` to `VisualPlanContract.Shot` additively.
- source_spec: `docs/implementation-artifacts/spec-1-5-voice-word-timing-subtitle-cues-full-chain-resume.md`
  summary: Add a dependency manifest (`requirements.txt`/`pyproject.toml`/`environment.yml`) pinning `google-genai`/`google-auth` and the rest of the `kayak-video` conda env's packages — none exists anywhere in the repo today.
  evidence: Pre-existing repo-wide gap; these packages were already used by the legacy root scripts (`generate_voice.py`, `extract_word_timing.py`) Story 1.5 ported from, not newly introduced by it.
- source_spec: `docs/implementation-artifacts/spec-1-5-voice-word-timing-subtitle-cues-full-chain-resume.md`
  summary: `extract_word_timing`'s `extract_recognition` would raise `TypeError` on a `"results": null` STT response instead of a clear alignment-failure message; `parse_offset` doesn't guard a non-numeric word-offset string.
  evidence: Both ported byte-for-byte from the pre-existing `extract_word_timing.py`; pre-existing characteristics, not new regressions, and outside this story's scope (only the reel-specific phrase-sourcing bug was renegotiated for a fix).
- source_spec: `docs/implementation-artifacts/spec-1-5-voice-word-timing-subtitle-cues-full-chain-resume.md`
  summary: `SubtitleCuesContract`'s `Cue` shape drops `style_hint`/`duration_seconds`/`start_word_index`/`end_word_index`/`duration_warning`, which the pre-existing `remotion/src/components/Subtitles.tsx` requires (it reads a separate `remotion/public/data/subtitle_cues.json` and uses `style_hint` to pick rendering variants, e.g. an `IMPACT` typography path).
  evidence: No consumer wires `metadata/subtitle_cues.json` to that Remotion file yet, so not a gap in this diff — but worth tracking for whichever future story (likely Epic 2) connects `voice_agent`'s output to rendering.
- source_spec: `docs/implementation-artifacts/spec-1-5-voice-word-timing-subtitle-cues-full-chain-resume.md`
  summary: Auth/billing failures (e.g. misconfigured ADC, exhausted Google Cloud quota) in `voice_agent`'s TTS/STT calls currently retry like any other transient failure, burning the full ceiling (3 attempts) on an error class that retrying can never fix.
  evidence: Real concern raised by external review, but distinguishing "this class of error can never be fixed by retrying" in general (vs. the narrowly-detectable `impact_text`-not-found case, which was fixed) is meaningful new scope beyond this patch round; the existing ceiling of 3 already bounds the wasted cost.
- source_spec: `docs/implementation-artifacts/spec-2-1-timeline-json-converter-regression-harness.md`
  summary: `timeline_converter.py`'s `build_timeline` tool handler has no defensive validation of malformed/missing `shot_assets` keys (raw `KeyError`/`ValueError` instead of a clear tool error).
  evidence: Matches the existing repo-wide convention of no defensive args validation in any `@tool` handler (e.g. `ingest` in `deterministic_tools.py`); not unique to this story, and fixing it here alone would be inconsistent with every other tool.
- source_spec: `docs/implementation-artifacts/spec-2-1-timeline-json-converter-regression-harness.md`
  summary: `regression_harness.py`'s core measurement/hashing logic (`measure_video`, `extract_frames`, `compute_phash`) is only exercised by tests `skipif`-guarded on the gitignored `remotion/out/book_reel_v1.mp4` being present locally, so a bug there would go undetected on a fresh checkout or in CI.
  evidence: Consistent with this story's own "one-time proof against the existing reference reel, not a per-run production gate" framing (spec Boundaries); wiring a full video fixture into the standard test run may be a deliberate future tradeoff, not an oversight.
- source_spec: `docs/implementation-artifacts/spec-2-1-timeline-json-converter-regression-harness.md`
  summary: `regression_harness.py` adds `numpy`/`scipy` as new hard dependencies with no dependency manifest anywhere in the repo to declare them.
  evidence: Compounds the already-tracked "no dependency manifest exists" gap logged above (from Story 1.5); adding a manifest is out of this story's scope.
- source_spec: `docs/implementation-artifacts/spec-2-1-timeline-json-converter-regression-harness.md`
  summary: `test_regression_harness.py`'s `requires_ffmpeg` skipif checks only that `ffprobe`/`ffmpeg` are present on `PATH` (`shutil.which`), not that they actually run; a broken-but-present binary (as in this sandbox's base conda env, missing `libintl.8.dylib`) hard-fails the tests instead of skipping them.
  evidence: Pre-existing environment issue (the base conda env's ffmpeg/ffprobe are already broken, unrelated to this story); AGENTS.md already mandates the working `kayak-video` env for running tests.
