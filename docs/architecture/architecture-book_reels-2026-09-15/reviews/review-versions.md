---
review: version-and-reality-check
target: ARCHITECTURE-SPINE.md (book_reels — Claude Agent SDK Orchestration)
date: '2026-09-15'
---

# Version & Reality-Check Review — ARCHITECTURE-SPINE.md

**Verdict:** The Stack table's two new-dependency claims (claude-agent-sdk 0.2.152, pydantic 2.13.5) and every named SDK API surface are verified accurate against PyPI and the actually-installed package, but the google-genai entry is stale/inaccurate — it asserts "2.19.0 pinned" as the current state when the project's own `kayak-video` environment (the canonical dependency location per `AGENTS.md`) actually has **2.23.0** installed, so the spine is asserting a fact about existing infrastructure that contradicts reality.

## Critical

- **google-genai version claim contradicts the actual installed environment.** The spine states `google-genai | 2.19.0 — pinned; do not upgrade reflexively`. Checked `pip show google-genai` in `/opt/homebrew/anaconda3/envs/kayak-video` (the env `AGENTS.md` line 26 names as "the only place dependencies... are installed") and it reports **2.23.0**, confirmed by `pip freeze` in that env. The 2.19.0 figure appears to have been copied from `docs/book_reels_troubleshooting_agent_playbook.md:238` (an older troubleshooting note) without re-checking the live environment, which is exactly the kind of asserted-not-verified claim this review exists to catch. The "do not upgrade reflexively" *policy* is correctly carried forward from `AGENTS.md`/the playbook, but the *version number* attached to it is wrong as a description of current reality.
  - **Fix:** Re-run `pip show google-genai` (or `pip freeze | grep google-genai`) inside `kayak-video` immediately before finalizing the spine, and record 2.23.0 (or whatever it is at write time) as the actual pinned version — keep the "don't upgrade reflexively" rule, just correct the number.

## High

- **pydantic version in the Stack table (2.13.5) does not match what's actually installed in `kayak-video` (2.13.4).** 2.13.5 is real and is PyPI's current latest (confirmed via PyPI JSON API, released 2026-08-28), so it's not hallucinated — but it describes a not-yet-installed target version rather than the environment's current state, and the spine doesn't flag this as a pending bump the way it explicitly does for other items.
  - **Fix:** Either state "2.13.4 installed (2.13.5 available)" or add an explicit upgrade action item; don't present 2.13.5 as already the case.
- **claude-agent-sdk is not installed at all in `kayak-video`** (`pip show claude-agent-sdk` → not found in that env; a *different* env/interpreter on this machine has 0.2.147 installed). This is consistent with it being a brand-new dependency this spine introduces (not a regression), but the spine doesn't note that it still needs to be installed into `kayak-video` before any orchestrator code can run there, which matters given AD-13 (preflight before spend).
  - **Fix:** Add an explicit setup step/AD note: `pip install claude-agent-sdk` into `kayak-video` is a prerequisite, not yet done.

## Medium

- **claude-agent-sdk 0.2.152 and its `>=3.10` requirement are correctly verified** — PyPI JSON API confirms `version: 0.2.152`, `requires_python: >=3.10`, uploaded 2026-09-02 (13 days before this spine's `created` date), and this exact release is PyPI's current latest. No issue found here; noted only so the "verified vs. asserted" ledger is complete.
- **All named SDK capability/API surfaces are real and current**, checked against the actually-installed `claude_agent_sdk` package (v0.2.147, one patch behind the claimed 0.2.152, found in a different conda env on this machine) by grepping its `types.py`/`__init__.py`:
  - `agents: dict[str, AgentDefinition] | None` on `ClaudeAgentOptions` — confirmed (types.py:2212), matches the spine's "agents={} dict of AgentDefinition" description.
  - `@tool` decorator and `create_sdk_mcp_server` — both defined and exported from `__init__.py` (lines 251, 491, 761-762).
  - `can_use_tool` — confirmed as a real `ClaudeAgentOptions` field with documented shadowing semantics relative to `PreToolUse` hooks.
  - `PreToolUse` hook event — confirmed (`PreToolUseHookInput`, `PreToolUseHookSpecificOutput` types exist and are exported).
  - `session_id`, `resume`, `SessionStore` protocol — all confirmed present (types.py:2005 `resume`, plus `SessionKey`/`SessionStoreEntry`/`SessionStore` Protocol).
  - `max_budget_usd` — confirmed as a real `ClaudeAgentOptions` field (types.py:2021).
  - `task_budget` — confirmed present but **gated behind a beta flag** (`task-budgets-2026-03-13`), per the type's own docstring at types.py:2352/72. The spine's AD-7 cites `max_budget_usd` / `task_budget` together without noting `task_budget` requires opting into a beta — worth a one-line callout so implementers don't hit a silent no-op or error from omitting the beta flag.
  - **Fix:** Add a footnote under AD-7 or the Stack table: "`task_budget` requires the `task-budgets-2026-03-13` beta flag; `max_budget_usd` does not."
- **Remotion 4.0.524 claim confirmed exact and correct.** `remotion/package.json` pins `remotion`, `@remotion/cli`, and `@remotion/eslint-config-flat` all to `4.0.524` verbatim — no discrepancy. This one claim in the spine appears to have been read directly from the file rather than asserted from memory.

## Low

- **No currency/staleness flag for google-genai's actual release cadence.** PyPI shows google-genai releasing roughly weekly-to-biweekly (2.19.0 → 2.23.0 is four minor bumps in about the same window claude-agent-sdk moved from ~0.2.14x to 0.2.152). Given that cadence, whatever number is recorded as "current" in the Stack table will likely be stale again within days — the spine should note the check date so future readers know how fresh the pin is, rather than implying it's a fixed fact.
  - **Fix:** Add "(checked YYYY-MM-DD)" next to the google-genai and pydantic version cells so staleness is self-evident rather than silently assumed.
- **No explicit note that claude-agent-sdk's `>=3.10` floor and the stated "kayak-video (3.11) satisfies it" claim were cross-checked against the *actual* interpreter in that env**, though this review did so (`python --version` in `kayak-video` → 3.11.16, satisfies `>=3.10`). Confirmed correct, just undocumented as verified in the spine itself.

## Verification method (for traceability)

- `pip index versions claude-agent-sdk|pydantic|google-genai` (base env) and PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`) for latest-version and `requires_python` ground truth.
- `pip show <pkg>` and `pip freeze` inside `/opt/homebrew/anaconda3/envs/kayak-video` for actual-installed-version ground truth (the env AGENTS.md names as canonical).
- `python --version` inside `kayak-video` → 3.11.16.
- Direct grep of an installed `claude_agent_sdk` package's `types.py`/`__init__.py` (found in a separate local conda env, v0.2.147) for every named API surface (`AgentDefinition`, `@tool`, `create_sdk_mcp_server`, `can_use_tool`, `PreToolUse`, `session_id`/`resume`/`SessionStore`, `max_budget_usd`/`task_budget`).
- Direct `Read` of `/Users/sachinpb/PycharmProjects/book_reels/remotion/package.json` for the Remotion version claim.
- Grep of `AGENTS.md` and `docs/book_reels_troubleshooting_agent_playbook.md` to trace where the spine's google-genai figure likely originated.
