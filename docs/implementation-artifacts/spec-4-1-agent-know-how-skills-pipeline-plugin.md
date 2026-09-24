---
title: 'Story 4.1: Agent know-how as skills in a pipeline plugin (with SDK spike)'
type: 'refactor'
created: '2026-09-24'
status: 'done'
baseline_commit: '2b2dd98e5295104a6adcd83c5be5b42da231049b'
route: 'full'
review_loop_iteration: 0
context:
  - '{project-root}/docs/epics.md'
  - '{project-root}/docs/implementation-artifacts/spike-4-1-sdk-skills.md'
  - '{project-root}/docs/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Each agent's domain know-how is an inline Python string. The character-likeness QA block is duplicated between the Veo and stills agents, and nothing proves that pipeline agents are free of developer tooling (repo `CLAUDE.md`, `.claude/skills`).

**Approach:** Move the know-how into 8 skills in one local plugin, `book-reels`, at `orchestrator/skills_plugin/`.
- Each agent loads only its own skills.
- Each agent prompt shrinks to its role, its boundaries, the contract it returns, and an instruction to use its skills.
- The init message is checked, and the stage halts on any foreign skill.
- The implementation paths come from the spike report `spike-4-1-sdk-skills.md` (FR4 (a)–(g), done first).

## Boundaries & Constraints

**Always:**
- The story's AC text in `docs/epics.md:186-246` is binding.
- `setting_sources=[]`, `strict_mcp_config=True`, `permission_mode="dontAsk"`, `output_format` and `max_budget_usd` stay unchanged on every agent, and `setting_sources == []` is asserted in code.
- The plugin path is absolute, built from `__file__`.
- Skill text carries the moved prompt rules faithfully. No new rules are invented (NFR1 parity).
- The likeness ladder (`build_prompt_ladder`) stays in code; skills hold QA criteria only.
- NFR6 hygiene applies to every skill.

**Paths chosen from the spike (decisions):**
1. **Skill names:** the qualified form `book-reels:<skill>` goes in `skills=[...]`. Spike (b) showed that both forms work, and that the init message and the model use the qualified one.
2. **Init check: fallback variant.** Spike (f) found the init `skills` list unfiltered. So the check requires:
   - every listed skill is `book-reels:*` or the CLI built-in `doctor`;
   - `plugins` names are exactly `["book-reels"]`;
   - every skill in the agent's own list is present.
   The per-agent restriction itself is proven by spike (b).
3. **Bundled skills:** pipeline agents set `env={"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}`. The CLI's bundled skills (`debug`, `simplify`, …) would otherwise defeat the check in (2).
4. **Read scoping: fallback.** Spike (e) found that allow rules can't narrow Read, because the cwd and the temp dir stay readable.
   - `allowed_tools` gains the `Read(//<abs plugin>/**)` rule, without `add_dirs`.
   - A `PreToolUse` hook denies `Read` outside the plugin folder. It applies to story, visual, veo and stills.
   - `asset_analyst` keeps its unrestricted bare `Read`, which it needs for screenshots, and gets no hook.

**Never:**
- Touch `cover_agent`, the contracts, or the user's dirty-tree run output.
- Delegate through the Agent tool.
- Add bare `Skill` to `allowed_tools`.
- Run paid image, Veo or TTS calls in the live validation.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Clean init | Init lists only `book-reels:*` + `doctor`; plugins `[book-reels]` | Stage logs the agent's skills and the plugin, and proceeds | none |
| Foreign skill | Init lists `bmad-build` or `lossless-teacher` | `SkillIsolationError`, raised before any model turn | Bounded stage: halt with failure report, no retry. Veo/stills: halt, charge $0 |
| Missing own skill | The agent's skill is absent from init | Same as foreign skill | Same |
| Non-empty setting_sources | Options built with `["project"]` | `SkillIsolationError` at construction | Same halt path |
| Read outside plugin | story/visual/veo/stills Read a non-plugin path | Hook denies with a reason | The model sees the denial |

</frozen-after-approval>

## Code Map

- `orchestrator/agents/{asset_analyst,story_agent,visual_agent,veo_agent,stills_agent}.py`
  - `AgentDefinition` prompts are the source text for the skills.
  - Each builds `ClaudeAgentOptions` inline and loops over `query()`, which tests monkeypatch per module. Keep the loop in each module.
- `orchestrator/agents/_stream.py` -- `log_tool_uses`. Add plain names for `Skill` calls.
- `orchestrator/run.py:218` -- the bounded stage's `except Exception` retries. Add `except SkillIsolationError` → `halt()`.
- `orchestrator/run.py:1190,1505` -- the Veo and stills except paths charge the whole `remaining_budget`. Add a $0 isolation halt ahead of them.
- `orchestrator/workspace.py:96` -- `enter_project` chdirs into `projects/<name>`, so the plugin is outside the cwd.
- **Tests encoding the old shape, to update** (the story changes this shape on purpose):
  - `tools == allowed_tools`: `test_asset_analyst.py:162`, `test_story_agent.py:223`, `test_visual_agent.py:272`, `test_veo_agent.py:92-97`, `test_stills_agent.py:88-90`;
  - prompt-content checks, which move to the skill files: `test_veo_agent.py:103-109`, `test_stills_agent.py:96-102`.
- `orchestrator/tests/test_agent_lifecycle.py` -- the stream must finish in the calling task. Wrap `query()` in `contextlib.aclosing` so an init-check raise closes the stream in the same task.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/skills_plugin/.claude-plugin/plugin.json` and `skills/<8 skills>/SKILL.md` + `references/*.md` -- create the plugin. Content is moved from the agent prompts.
- [x] `orchestrator/agents/_skills.py` -- add:
  - `PLUGIN_DIR`, `PLUGIN`, `PIPELINE_ENV`;
  - `qualified()`, `plugin_read_rule()`, `plugin_read_guard()`;
  - `SkillIsolationError`, `assert_isolated(options)`, `check_init_message(message, skills)`, which logs.
- [x] The 5 agent modules -- shrink the prompts. Add `SKILLS`; split `tools`/`allowed_tools`; add `skills`, `plugins`, `env`, and the hook where applicable. Assert isolation, and check init inside an `aclosing` loop. Raise the Veo/stills `max_turns` to leave room for the skill loads.
- [x] `orchestrator/run.py` -- add the isolation halt at the three sites.
- [x] `orchestrator/agents/_stream.py` -- add a plain log line for Skill loads.
- [x] `orchestrator/tests/test_skills_plugin.py` -- test:
  - NFR6 frontmatter and line limits, and that reference files are one level deep and cited;
  - the exact skill set;
  - per-agent options (exact skills, absolute plugin path, split tools, env, hook);
  - the init check (clean, foreign, missing, wrong plugin);
  - the `setting_sources` assertion;
  - the Read guard;
  - the bounded-stage halt with no retry.
- [x] Update the old-shape tests listed in the Code Map.
- [x] `docs/architecture/.../ARCHITECTURE-SPINE.md` -- add AD-16, and correct AD-2's wording and diagram.

**Acceptance Criteria:**
- Given the spike report, when it is read, then (a)–(g) each have a yes/no answer with evidence, and the chosen paths are stated.
- Given the full suite, when it runs, then the failing and erroring set equals the baseline, and the new tests pass.
- Given a throwaway copy of an existing project, when only asset_analyst, story and visual run live with `MAX_BUDGET_USD` ≤ $5, then all three contracts validate and the log records each stage's skills.

## Design Notes

A skill's `SKILL.md` holds the rules the agent always needs. A reference file holds conditional detail: rubrics, failure preservation, turnaround sheets. This keeps extra turns to a minimum.

The Veo and stills `max_turns` go up (8→12 and 6→10), because skill loads consume turns. `max_budget_usd` still bounds the spend.

## Verification

**Commands:**
- `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q` -- expected: the same 5 failed and 12 errors as the baseline, with the passed count up by the new tests.
- The live-validation AC and the commit are done by the orchestrating session afterwards. The implementer makes no live Claude calls and runs no git command that stages, commits, stashes or restores, because the working tree holds the user's own uncommitted run output.

## Implementation Notes

- Suite: 5 failed / 12 errors (identical set to baseline), 621 passed (565 + 56 new in `test_skills_plugin.py`), 5 skipped.
- The init check runs only when an init `SystemMessage` arrives; a stream without one (every mocked test stream) is not treated as a violation.
- Reference files: `story-craft` and `visual-planning` hold their self-review rubrics, `veo-production` and `still-production` their failure handling, `character-likeness-qa` the turnaround-sheet rule. `asset-analysis`, `source-fidelity` and `visual-qa` have none.
- `visual-qa` holds the four shared rejects; stage-specific additions (wrong scene/emotion, letters/logos/watermarks, one-instance and subtitle-space checks, clip motion and face stability) stay in the production skills, worded as before.
- `asset_analyst` also gets the plugin `Read(...)` rule next to its bare `Read`, for uniformity; it has no hook.
- A Veo/stills isolation halt writes no attempt record and charges $0; the attempt counter increment already saved stays.
- AD-2 cites the `analyze_assets` docstring by name, since the line numbers moved.

## Spec Change Log

## Review Triage Log

Review done by the orchestrating session after the implementer stalled, against the diff from `baseline_commit` over the story's paths only.

- **Prompt content lost in the migration?** None lost. Every line of the old inline prompts (`git show 2b2dd98:orchestrator/agents/*.py`) was checked against the new prompts plus the plugin files. The only misses were re-wrapped lines and the "no tools at all" / "exactly two tools and no filesystem" sentences, which are now untrue and were correctly reworded. The 90-115 word rule is verbatim in `story-craft/SKILL.md`. The live story attempt 1 at 125 words is model variance, rejected and retried by the contract as before. Disposition: no change.
- **`CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` in `env`.** Needed: without it the unfiltered init `skills` list contains CLI-bundled, unprefixed skills that the fallback check would reject as foreign. It affects only the pipeline agents' CLI subprocess. The live run authenticated normally, so `env` merges rather than replaces. Disposition: keep.
- **Read path-check bypass.** The hook resolves the path, which follows symlinks and collapses `..`, and joins relative paths to the session cwd. It denies an empty path and matches by `parents`, not by string prefix, so a `skills_plugin-sibling` folder is refused. The tests cover `..`, the sibling prefix, relative and empty paths. Disposition: no change.
- **`max_turns` raised (veo 6→10, stills 8→12).** A deviation from the story text: skill loads take turns. Spend is still bounded by the unchanged `max_budget_usd`. Disposition: keep; noted.
- **Live validation (done).** Throwaway copy of `projects/new-old` (`zz-story41-validation`, deleted afterwards), `MAX_BUDGET_USD=5`, Claude-only stages. asset_analyst logged `book-reels:asset-analysis`. story_agent logged `book-reels:story-craft, book-reels:source-fidelity`; attempt 1 was rejected at 125 words and attempt 2 passed. visual_agent logged `book-reels:visual-planning, book-reels:source-fidelity`; the first visual attempt died with the stalled session, and a resumed attempt passed. `AnalyzedAssetsContract`, `FinalStoryPlanContract` and `VisualPlanContract` all validate. Total spend $2.03. Every init check passed, with plugins exactly `[book-reels]`.
- **Misleading progress label.** `_stream.py` labelled every `Read` "opening a screenshot", including skill-reference reads by story/visual/veo/stills. Disposition: fixed, with a label that names the reference file, plus a test.
