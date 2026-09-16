# Review — ARCHITECTURE-SPINE.md against the good-spine checklist

**Reviewed:** `docs/architecture/architecture-book_reels-2026-09-15/ARCHITECTURE-SPINE.md`
**Reviewed against:** `AGENTS.md`, `docs/book_reels_architecture.md`, `docs/book_reels_manual_interventions_and_automation_map.md`, `docs/book_reels_troubleshooting_agent_playbook.md`, and live version checks (PyPI, the repo's actual `kayak-video` conda env, npm).

## Verdict

The 14 ADs are a strong, well-evidenced encoding of this pipeline's real failure history (seed fallback, JSON-overwrite, RAI-inspection, locked-artifact tension are all correctly ratified from the troubleshooting playbook), but the spine is not yet safe to build against: it is entirely silent on the operational/environmental envelope, contains one stale factual claim about the existing environment, and has three ADs whose rules don't fully cover the divergence they claim to prevent.

## Critical

1. **The operational/environmental envelope is completely absent — not decided, not deferred, not even an open question.** Nothing in the spine addresses: how `orchestrator/run.py` is invoked (CLI entrypoint? watched folder? the equivalent of today's `python script.py` convention in `AGENTS.md`'s "Running and verifying" section has no successor here); where/how credentials and secrets are supplied and rotated for an unattended run (AD-13 only checks that ADC/bucket access *works*, it doesn't own how it's provisioned); and — most importantly given the user's explicit goal of "full autonomy, only review the finished video" — how anyone or anything is notified when AD-6's hard-halt fires with nobody watching. The Deferred section (5 bullets) has no entry for this dimension at all.
   - **Fix:** add an explicit Deployment/Operations section — even if most of it is one-line deferrals — that at minimum states the invocation contract, the secrets-provisioning story, and the halt-notification mechanism (or explicitly defers each with a reason).

## High

2. **The Stack table's `google-genai` version claim is already stale against the real environment it claims to describe.** The spine states `google-genai 2.19.0 — pinned; do not upgrade reflexively`. The actual `kayak-video` conda env (the only place this project's dependencies live, per `AGENTS.md`) has `google-genai 2.23.0` installed. `AGENTS.md`'s actual policy is "don't upgrade reflexively, diagnose the traceback first" — it never pins a version number. The spine converts a process rule into a false fact about the environment, which could lead an implementer to "fix" a non-problem by downgrading.
   - **Fix:** state the actual installed version (2.23.0) and rephrase as "don't upgrade reflexively" (the real policy), not a hard pin to a now-superseded version.

3. **AD-4's "semantic/quality judgment" gate is written as universal ("every stage's output") with no carve-out for purely deterministic stages**, but `AGENTS.md` explicitly forbids replacing/second-guessing `build_subtitle_cues.py`'s deterministic segmentation with free-form LLM judgment ("a prior LLM-grouping approach was unreliable and was deliberately replaced"). `voice_agent.py`'s "subtitle QA" responsibility (Structural Seed) sits exactly on this fault line: read literally, AD-4 obligates an LLM-judgment gate on a deterministically-produced artifact that the codebase has already decided must not be second-guessed by an LLM.
   - **Fix:** scope AD-4's semantic-judgment gate explicitly to generative/creative outputs (narration, images, video, prompts); state that deterministic-tool outputs (ingest, word-timing, subtitle segmentation) clear stage 2 via their own mechanical validators (e.g. alignment ratio) only, never LLM re-judgment.

4. **AD-8's Binds list is incomplete relative to the three locked-artifact categories it exists to interpret.** AD-8 binds only `story_agent.py` and `visual_agent.py`, but the `AGENTS.md` locked-artifact policy it's clarifying names three things: locked narration, locked `subtitle_cues.json`, and the locked shot-timing plan. Per the spine's own Structural Seed, subtitle cues are owned by `voice_agent.py`, which AD-8 never mentions. This leaves the exact question AD-8 exists to answer — "can the producing agent revise its own draft mid-run vs. is it locked" — unresolved for whichever agent will spend the most retry cycles on a deterministic-plus-QA artifact.
   - **Fix:** add `voice_agent.py` to AD-8's Binds list.

## Medium

5. **AD-7 treats `max_budget_usd` and `task_budget` as interchangeable hard ceilings; they are not.** Per the current SDK, `max_budget_usd` (float, USD) is an enforced stop that returns `error_max_budget_usd`. `budget: TaskBudget` (a token count) only "makes the model aware of its remaining budget so it can pace tool use" — it's advisory, not an enforced halt. If an implementation wires up only `TaskBudget`, AD-6's guarantee ("hitting the ceiling halts the run the same way as AD-6") silently doesn't hold.
   - **Fix:** name `max_budget_usd` as the enforced ceiling; mention `TaskBudget` only as a supplementary pacing hint, never as a substitute for the hard stop.

6. **AD-3's consolidation of 14 scripts into ~4 tool files silently overrides an existing repo convention that the spine's own Consistency Conventions table claims to cover but doesn't.** `AGENTS.md` documents "config (PROJECT_ID, region, model name) is hardcoded per-script... expect the same literal repeated across files." Collapsing gemini/veo/stt logic into `gemini_tools.py`/`veo_tools.py`/`stt_tools.py` is a real config-ownership decision (duplicate constants across merged functions, or centralize them?) that the Consistency Conventions table's "State & cross-cutting (mutation, errors, logging, **config**, **auth**)" row should decide — but that row only discusses run-state/failure-report location, never config or auth.
   - **Fix:** add an explicit line to that table's cross-cutting row deciding whether tool consolidation also centralizes config, and how auth (ADC/service-account) is threaded into the merged tool files.

7. **Deferring the failure-report schema conflicts with AD-6 assigning report-writing to "every agent," not only `run.py`.** If each agent independently satisfies AD-6 with its own idea of "what failed, what was attempted," two independently-built agents can produce structurally different failure reports — which directly undermines AD-14's resume logic, which needs a consistent way to read what happened at the halted stage.
   - **Fix:** pin a minimal common envelope now (stage id, failed contract name, attempt count, timestamp, partial-artifact paths); leave only the diagnostic-detail payload per-agent-specific in Deferred.

## Low

8. **Minor version drift not caught before publishing:** the Stack table's `pydantic 2.13.5` doesn't match the actually-installed `2.13.4` in `kayak-video` (harmless patch lag, but shows the table wasn't checked against the live environment).
   - **Fix:** re-verify Stack table versions against the actual `kayak-video` env right before finalizing, not against PyPI "latest" alone.

9. **The three standalone manual-debug scripts `AGENTS.md` calls out by name** (`review_story_plan.py`, `revise_story_plan.py`, `review_revised_story_plan.py` — explicitly "not in this chain") **are never mentioned in the spine.** Unclear whether they're retired once the orchestrator exists, or kept as an escape hatch for the troubleshooting playbook's manual diagnostic workflow.
   - **Fix:** add one line (Deferred or Structural Seed) stating their fate.
