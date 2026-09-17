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
