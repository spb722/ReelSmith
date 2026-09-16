- source_spec: `docs/implementation-artifacts/spec-1-1-orchestrator-scaffold-preflight-budget.md`
  summary: Add a `.gitignore` entry for `orchestrator_runs/` (and, ideally, the repo's other untracked generated directories).
  evidence: The repo has no `.gitignore` at all today, so `metadata/`, `generated/`, etc. are already untracked the same way. Real risk, but pre-existing and not caused by Story 1.1.
- source_spec: `docs/implementation-artifacts/spec-1-1-orchestrator-scaffold-preflight-budget.md`
  summary: Update AGENTS.md — the "Conventions" line stating config is hardcoded per-script (now false, since `orchestrator/settings.py` centralizes it), the dependency list omitting `google-cloud-storage`/`pytest`, and the "Where things are" section omitting `orchestrator_runs/`.
  evidence: Confirmed stale/incomplete against the current codebase (`AGENTS.md:26,32`). Fix requires editing an agent-context file, which routes to defer regardless of severity.
