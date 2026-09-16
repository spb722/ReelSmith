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
