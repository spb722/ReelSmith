---
title: 'Story 4.2: Guard hooks -- budget guard and vendor-limit classification'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: 'e6f6422b5cc431535fad0f4ff28dc368692cb2cf'
route: 'full'
review_loop_iteration: 0
context:
  - '{project-root}/docs/epics.md'
  - '{project-root}/docs/implementation-artifacts/spike-4-1-sdk-skills.md'
  - '{project-root}/docs/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A paid tool call inside an agent's own loop is never checked against the run budget, and a vendor refusal (quota, usage/session/spend limit, auth, 429) is counted as the reel's fault: it burns attempts and draws the conservative charge for work that never ran.

**Approach:** Bind a per-query guard to the Veo and stills queries. Its `PreToolUse` hook denies a paid call the remaining budget can't cover, cumulatively. Its `PostToolUse` hook records paid calls that billed and the tool failure contracts. One classifier in `run.py` sorts every refusal route into `transient`/`hard`/`none`. A vendor refusal gives the attempt back, charges only real costs, and ends in a resumable `vendor_limit` halt. `FailureReport` gains `reason_code` and `resumable`.

## Boundaries & Constraints

**Always:**
- The story AC in `docs/epics.md:248-306` is binding, together with FR9, FR10, NFR5 and NFR8.
- The new hooks merge with 4.1's `plugin_read_guard()`. Both keep firing.
- The `tool_response` is parsed as a list of content blocks (spike (g)).
- Non-vendor failures keep today's behaviour exactly.
- New `FailureReport` fields have defaults, so an old report dict still builds.

**Transient backoff, decided from retry evidence (Implementation Notes):** the orchestrator backs off only on routes with no retry of their own: the Veo clip tool (google-genai with no `retry_options`) and Codex images. A transient refusal on a Claude route (the CLI already retries 429 and 5xx) or on a Gemini image (`QUOTA_RETRY_SECONDS`) escalates straight to the hard path.

**Never:** Touch the user's dirty-tree run output. Make live vendor calls. Change contract schemas other than by adding fields. Add reservations (4.3) or concurrency (4.4).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Budget covers calls | seed + clip costs ≤ remaining | Both allowed | none |
| Second seed over budget | allowed so far + cost > remaining | The call is denied with a reason. After the query, halt `budget_exhausted`, whatever outcome the agent returned | Resumable after raising `MAX_BUDGET_USD` |
| Hard Claude exception or `is_error` | "session limit", 401/403, or a 429 | Attempt given back, Claude cost charged, no conservative charge | Halt `vendor_limit`, resumable |
| Tool failure contract (Codex usage limit) | `failure.reason` holds "usage limit" | Same hard path. Paid calls that billed are charged | Halt `vendor_limit` |
| Veo submit SDK_ERROR, hard | no operation dump | No clip cost | Halt `vendor_limit` |
| Veo poll SDK_ERROR, hard | operation dump present | Clip cost charged | Halt `vendor_limit` |
| Transient Veo/Codex | 429 or "rate limit" | Up to 3 backoff reruns that consume no attempt, each charged its real cost. Still failing → hard | Halt `vendor_limit` |
| Non-vendor failure | anything else | Unchanged | Unchanged |

</frozen-after-approval>

## Code Map

- `orchestrator/run.py` -- `_halt` (≈126) gains `reason_code`. Bounded stage: the `except Exception` (≈223) and the `is_error` check (≈244). Veo loop: `except Exception` (≈1202), `is_error` (≈1237). Stills loop: ≈1524, ≈1555. Put the classifier next to `LIKENESS_REJECTION_CODES` (≈861).
- `orchestrator/agents/_skills.py` -- add `merge_hooks`.
- `orchestrator/agents/_guard.py` (new) -- `PaidToolGuard`: the Pre/Post/PostFailure hooks and closure state.
- `orchestrator/agents/{veo,stills}_agent.py` -- add an optional `guard` kwarg, with hooks merged.
- `orchestrator/state/run_manifest.py:172` -- `FailureReport` and `build_failure_report`.
- `orchestrator/tools/veo_tools.py:503-553` -- a submit SDK_ERROR has no `operation_dump_path`; a poll SDK_ERROR has one. That tells the two apart.
- Tests: reuse the `chain` fixture and the outcome builders in `orchestrator/tests/test_full_chain_resume.py`.

## Tasks & Acceptance

**Execution:**
- [x] `orchestrator/state/run_manifest.py` -- add `reason_code` and `resumable` with defaults.
- [x] `orchestrator/agents/_guard.py`, `_skills.py`, `veo_agent.py`, `stills_agent.py` -- guard and hook merge.
- [x] `orchestrator/run.py` -- classifier, the four routes, transient backoff, reason codes on halts.
- [x] `orchestrator/tests/test_guard_hooks.py` -- every item in the story's test list, plus a merged-hooks test.
- [x] `ARCHITECTURE-SPINE.md` -- amend AD-5/6/7. Mark `docs/vendor-limit-fix.md` superseded.

**Acceptance Criteria:**
- Given the full suite, when it runs, then the failing set equals the baseline and the new tests pass.

## Implementation Notes

- Implemented directly by the orchestrating session, not by a fresh subagent. Two earlier agents were killed by a no-progress watchdog, and the investigation was already in context.
- Suite: 5 failed / 12 errors, the same set as the baseline. 681 passed (622 + 59 new in `test_guard_hooks.py`), 5 skipped.

**Which clients already retry 429s** (so the orchestrator never doubles a retry):
- **Claude (Agent SDK → bundled CLI 2.1.259):** retries. The bundled binary's Anthropic client `shouldRetry` returns true for `x-should-retry: true`, 408, 409, 429 and >=500. That is at byte ~157.28M of `claude_agent_sdk/_bundled/claude`: `if(e.status===429)return!0;if(e.status>=500)return!0`. It honours `retry-after-ms`, is tunable with `CLAUDE_CODE_MAX_RETRIES`, and its UI prints "Retrying in … · attempt N/maxRetries". The Python SDK adds no retry of its own. So a transient refusal on a Claude route goes straight to the hard path.
- **google-genai 2.23.0:** no retry by default. `_api_client.retry_args(None)` returns `stop_after_attempt(1)`. The defaults (5 attempts, 429 included) apply only when `HttpOptions.retry_options` is set, and the repo never sets it (`veo_tools.py:612`, `gemini_tools.py:1016,1092`). Veo therefore gets the orchestrator's backoff. The Gemini image path has its own `QUOTA_RETRY_SECONDS` loop (`gemini_tools.py:59,624`), so it gets none.
- **Codex CLI (installed 0.156.1; source docs rust-v0.155.1 via Context7):** ChatGPT-auth providers build `ApiRetryConfig { retry_429: false, retry_5xx: true, … }` (`codex-rs/model-provider-info/src/lib.rs`). A 429 whose body carries `usage_limit_reached` becomes the terminal `CodexErr::UsageLimitReached` (`codex-rs/codex-api/src/api_bridge.rs`). So Codex rate limits get the orchestrator's backoff, and a usage limit is `hard`.

**Decisions:**
- **Veo submit versus poll `SDK_ERROR`:** told apart structurally. A submit failure has no `operation_dump_path`, and a poll failure has one. No tool change was needed.
- **google-genai HTTP status:** read from the error text's `"<code> <STATUS>"` form (for example `429 RESOURCE_EXHAUSTED`), so a Veo 429 with no marker words still classifies. Vertex "Quota exceeded" 429s classify `hard`, because the story's `quota` marker wins. That errs toward stopping early.
- **Marker normalisation:** `_` is read as a space, so `usage_limit_reached`, `rate_limit_error` and `service_spend_limit_reached` match. The marker is `spend limit`, a superset of the story's `monthly spend limit`.
- **Vendor-refusal records:** written as `<agent>_shot_N_attempt_K_{vendor_limit|transient_M|budget_denied}.json`, with no `structured_output`. So the ladder never reads them, and a resumed attempt's own `_K.json` doesn't overwrite them. The bounded stage writes `<prefix>_K_vendor_limit.json` in place of `_K.json`.
- **A budget denial keeps its attempt consumed.** The story gives back attempts only for vendor refusals. It charges the Claude cost plus the paid calls that billed.
- **`reason_code` is set on:** vendor-limit halts; budget halts (bounded stage, Veo, stills); and retry-ceiling halts. Every other halt is `unclassified`. The voice stage's TTS/STT budget halts are not tagged.
- **`PostToolUseFailure`:** a crashed paid handler is recorded, charged its configured cost (it may have reached the vendor), and classified by its error text.
- **Resume:** `main()` runs preflight on every invocation, a resume included, so AD-13 needs no new code. The stage resumes because the given-back attempt count and the absent persisted output make it run again. `test_resume_after_a_vendor_limit_redoes_the_same_attempt` covers this.

## Spec Change Log

## Review Triage Log

Pass 1: three layers (blind, edge-case, verification-gap). Verdicts: 0 high, 6 medium, 5 low, 6 false, 2 maybe-false.

**Patched** (tests added for each):
- **medium** -- `allowed_usd` was never given back after a free failure, so a retry in the same session could be denied as `budget_exhausted`. It is now decremented when a call doesn't bill.
- **medium** -- A transient tool refusal the agent recovered from, ending in SUCCESS, was rerun and its paid asset discarded. It is now ignored when the outcome is SUCCESS. Hard refusals still halt whatever the outcome, as the story says.
- **medium** -- There was no stills denial test. Added.
- **medium** -- There was no exception-plus-tool-refusal test. Added.
- **low** -- `reason_code` wasn't asserted on the shot-stage budget and retry-ceiling halts. Tests added.
- **low** -- The bounded-stage exception route left no attempt record. It now writes `_vendor_limit.json`.
- **low** -- Float epsilon on the guard comparison. Added.
- **low** -- The denial text named the run budget, which becomes wrong after 4.3. Now worded neutrally.
- **low** -- An exception after a denial was reported as `unclassified`. Now `budget_exhausted`.
- **low** -- AD-5's amendment text was garbled. Reworded.

**Rejected:**
- **false** -- A real Veo 429 carrying "quota" is classed `hard`. The story's classifier makes `quota` hard. A 429 without that word is still transient, through the parsed status. Recorded in the notes.
- **false** -- The guard limit ignores the Claude share. The story sets the limit to the run's remaining budget, and 4.3 moves it to the reservation.
- **false** -- A Veo denial halts rather than demoting to a still. The story mandates a `budget_exhausted` halt.
- **false** -- A denial discards paid work. The story says "whatever outcome".
- **false** -- A flat image cost ignores the provider. `settings.image_call_cost_usd` is already provider-specific (Codex costs $0).
- **false** -- `tool_response` could be a dict or a string. Spike (g) is binding: it is a list.
- **maybe-false** -- The exception route charges $0 of Claude cost. The story says "the Claude cost reported", and an exception reports none. Settle it by checking whether the CLI's refusal exceptions come after paid turns. It is left as the story states.
- **maybe-false** -- A Claude exception saying "429" without the words "rate limit" goes unclassified. Settle it by capturing the real CLI exception text; the CLI normally surfaces this as `is_error` with `api_error_status`.

**Deferred** (entries in `deferred-work.md`):
- No test pins that `transient_retries` resets per attempt.
- Nothing checks against the real CLI that `PostToolUseFailure` fires for SDK MCP tools and that exact-name matchers bind. No live calls were allowed.
- Voice-stage budget halts carry no `reason_code`.
- Evidence records are overwritten across repeated vendor halts at the same attempt number.

## Verification

**Commands:**
- `/opt/homebrew/anaconda3/envs/kayak-video/bin/python -m pytest orchestrator/tests -q -p no:cacheprovider` -- expected: the same 5 failed and 12 errors, with more passed.
