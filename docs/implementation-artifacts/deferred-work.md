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
- source_spec: `docs/implementation-artifacts/spec-2-2-data-driven-renderer-transform-none-enforcement.md`
  summary: A shot whose `toFrame(end_seconds) - toFrame(start_seconds)` rounds to 0 frames would hit an unrelated Remotion internal error in `BookReel.tsx` instead of a clear message.
  evidence: Not reachable by any current data -- `timeline_converter.py`'s `build_timeline_data` already rejects `end_seconds <= start_seconds`; only a hypothetical future reel with a genuinely sub-frame-length shot could trigger it.
- source_spec: `docs/implementation-artifacts/spec-2-2-data-driven-renderer-transform-none-enforcement.md`
  summary: Neither `remotion/src/components/Subtitles.tsx` nor the new `BookReel.tsx` data-fetch checks `response.ok` before `.json()`; a 404 (e.g. missing JSON file) produces an opaque `SyntaxError` instead of a clear error message.
  evidence: `Subtitles.tsx`'s identical gap predates this story; fixing only the new `BookReel.tsx` call site would be inconsistent with the established (if imperfect) fetch pattern this story was told to reuse.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: `orchestrator/tools/veo_tools.py::generate_veo_clip`'s `Shot.model_validate(args["shot"])` isn't wrapped in a try/except; a malformed shot payload crashes the tool instead of returning a structured error.
  evidence: Matches the existing repo-wide convention of no defensive args validation in any `@tool` handler (e.g. `ingest` in `deterministic_tools.py`, `build_timeline`'s args handling from Story 2.1) -- not unique to this story.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: `orchestrator/run.py::run_veo_stage` only forwards the first of a shot's possibly-multiple `Shot.source_asset_ids` to seed generation/QA; any additional cited assets are silently dropped.
  evidence: Not exercised by any current data (every shot in `metadata/visual_plan.json` cites exactly one source asset today); correct handling of multiple cited assets is a design question (Gemini image recomposition may only ever accept one source image), not a clear bug.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: On retry-ceiling exhaustion, `run_veo_stage`'s halt report shows a generic "Veo retry ceiling exhausted" message rather than the last attempt's actual recorded failure reason.
  evidence: Consistent with the existing minimal common halt envelope (stage id, failed contract name, attempt count, timestamp, partial-artifact paths) established since Epic 1 -- diagnostic detail beyond that envelope is already documented as agent-specific/deferred, not a regression unique to this story.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: `orchestrator/tools/gemini_tools.py::extract_generated_image` only scans the first inline image and stops, so a multi-candidate/multi-part Gemini response could truncate `model_text_response` or ignore a later/better image.
  evidence: Uncertain real-world likelihood for this single-image-recomposition use case; no observed real response has more than one candidate/part.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: No test drives `run_veo_stage`'s cross-check validation of a mismatched/malformed `veo_agent` outcome (wrong shot, uncited seed asset, over-ceiling cost).
  evidence: Verification-gap review's own suggested disposition: a defensive check against an already-scoped, tool-restricted agent's own structured output, lower priority than the exception-path and poll-timeout gaps already patched.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: `orchestrator/state/production_assets.py::upsert_production_asset` has no protection against two concurrent orchestrator invocations both upserting around the same time (last-write-wins, no snapshot/mtime check).
  evidence: Already an explicitly-documented, accepted v1 architectural limitation (architecture spine: "v1 assumes sequential agent handoff... so AD-10's single-writer rule has no concurrent writers to serialize yet") -- not a defect introduced by this story.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: A `veo_agent` retry triggered only by a clip QA failure (seed already approved) still pays for a redundant `generate_veo_seed` call, since each attempt is a stateless fresh Claude session with no plumbing in `run.py` to reuse a prior seed object across attempts.
  evidence: The prompt was clarified to skip re-litigating an already-approved seed's QA on such a retry, but fully eliminating the paid regeneration needs cross-attempt seed-reuse plumbing in `run.py`, out of scope for a prompt-only fix.

- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: Persist an explicit paid-call reservation before awaiting the Veo agent so a process crash after an accepted request cannot repeat spend on resume.
  evidence: `run_veo_stage` persists the attempt count before the call and charges the reserved ceiling when an exception returns, but a hard process crash between the SDK request and exception handling still leaves no durable reservation; proving and recovering that window needs a crash-recovery design beyond this patch.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: Return a structured seed-tool failure contract when the Gemini client or source-image read fails directly inside the MCP tool.
  evidence: The orchestrator catches agent/tool failures and writes a bounded halt report, but direct callers of `generate_veo_seed` still receive SDK/filesystem exceptions; exposing a seed-specific failure schema requires an additional agent/tool protocol decision.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: Validate generated media bytes with a real MP4/image decoder before approval.
  evidence: Contract checks enforce non-empty bytes, hashes, operation provenance, and generated preview files; decoder validation would add a new runtime dependency and was not exercised by the SDK-shaped fake media used in this story.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: Preserve and validate all cited source assets and subtitle cue IDs when constructing a Veo prompt.
  evidence: The current stage chooses the first available cited asset and omits unknown cue IDs, which is deterministic but can under-represent a malformed multi-source plan; resolving the intended prompt semantics belongs in the visual-plan/story contract.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: Add approved-result resume behavior to the standalone `generate_veo_clips.py` harness.
  evidence: The orchestrator has paid-call-free keyed resume; the canonical harness remains an explicitly selected-shot operator tool and rewrites its per-shot result entry, so making it resumable needs a separate CLI contract.
- source_spec: `docs/implementation-artifacts/spec-2-3-autonomous-veo-generation-production-asset-manifest-structured-failure-inspection.md`
  summary: Make seed and clip MCP tools enforce a per-attempt maximum of one paid call each.
  evidence: The scoped agent prompt limits the two registered tools and the orchestrator reserves one image plus one Veo estimate, but SDK tool-call counting would require an additional runtime wrapper around Claude Agent SDK events.
