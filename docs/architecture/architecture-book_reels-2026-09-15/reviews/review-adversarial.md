---
name: 'Adversarial Review — ARCHITECTURE-SPINE (book_reels)'
type: architecture-review
reviews: '../ARCHITECTURE-SPINE.md'
created: '2026-09-15'
---

# Adversarial Review — book_reels Claude Agent SDK Orchestration Spine

## Verdict

The spine's fourteen ADs are individually well-formed but under-specify *ownership and sequencing* at exactly the seams the pipeline's own history shows are dangerous (shared manifests, resume state, retry-vs-budget interaction), and contain at least one direct self-contradiction (AD-1's capability boundary vs. the Structural Seed's own tool inventory) plus one unresolved AD-vs-AD boundary (AD-8's lock vs. AD-14's autonomous resume) — so two engineers each following every AD to the letter can produce components that do not fit together.

---

## Critical

**1. AD-13's preflight is scoped to "stage 1," but AD-14's resume can start anywhere — a resumed run can legally skip preflight entirely.**
AD-13's rule text is "the orchestrator verifies ... **before starting stage 1**." AD-14's rule is that a re-invoked run "continues from the first stage without one [a valid contract]" — which, per AD-14's own intent, is very often *not* stage 1. An implementer who wires preflight into the fresh-run code path only (literally satisfying AD-13, which never mentions resume) reintroduces on every resumed run exactly the failure AD-13 exists to prevent: burning turns/wall-clock before discovering ADC/bucket auth is broken. Another implementer who assumes preflight always runs first will build a resume path that never calls it. Both are "AD-13 compliant."
*Fix:* Reword AD-13 to bind "before executing the first stage of **any** invocation, fresh or resumed" — decouple it from the literal ordinal "stage 1."

**2. AD-8 (locked artifacts) and AD-14 (autonomous resume) don't agree on when a run's artifacts become locked.**
AD-8 locks artifacts "once a run's video is delivered for review... for any subsequent run," and says in-run self-correction is fine until then. AD-14 says a halted/re-invoked run resumes autonomously from its last valid contract with no human gate. Neither AD says whether "delivered for review" happens *before or after* the single human video-review checkpoint completes, nor whether a re-invocation of a run whose V1 already rendered counts as "resuming the same run" (AD-14 governs, still mutable) or "a subsequent run" (AD-8 governs, locked, needs explicit user direction). One engineer builds resume to freely revise stage outputs of an already-rendered-but-not-yet-reviewed run; another builds it to refuse touching anything once V1 exists. Both cite a correct AD.
*Fix:* Add an explicit boundary AD (or amend AD-8): a run's artifacts lock only at the recorded human-approval event of the final-review checkpoint (or an explicit "reject/regenerate" signal), never merely at "V1 rendered" — and re-invocation before that event is unambiguously AD-14's resume, never AD-8's lock.

**3. `veo_agent` and `stills_agent` both perform keyed upserts into the same `ProductionAssetsContract` with no assigned single writer or lock.**
AD-10 mandates keyed-merge writes (never wholesale overwrite) and AD-11 mandates a single named manifest — but neither AD says only one component may hold write access, or that concurrent per-shot writes must be serialized. Two agents (or two shot-level tool invocations of the same agent, if AD-5's per-shot retry runs concurrently for different shots — parallelism is only "deferred," not forbidden by any AD) each doing a compliant "read-manifest, merge-my-shot-key, write-manifest" round trip is a textbook lost-update race: agent A reads the manifest, agent B reads the (same) manifest, both merge their own shot and write back — whichever writes last silently drops the other's already-approved shot. Every individual write is a valid "keyed upsert."
*Fix:* Amend AD-10/AD-11 to name a single writer-of-record (e.g., only `run.py`/orchestrator commits merges, agents return validated per-shot results to it) or require a file lock / single-writer queue around `ProductionAssetsContract` mutation.

**4. AD-1's capability boundary contradicts the Structural Seed's own tool inventory on "image understanding."**
AD-1: "Only image generation/editing, TTS synthesis, Veo video generation, and Google STT/Chirp alignment may be implemented as Google/Gemini API tool calls. All **understanding**... work is implemented as Claude agent reasoning." But the Structural Seed lists `gemini_tools.py — @tool: image understand, image edit/recompose, TTS`, and AD-2 names "asset understanding" as `asset_analyst.py`'s whole responsibility (carried over from the existing `analyze_assets.py`, which today calls Gemini vision per AD-3's "wrap existing scripts" mandate). This is the spine contradicting itself: one reading has `asset_analyst` calling Claude's own vision only (satisfies AD-1, breaks AD-3's "wrap the existing script's logic" if that logic is Gemini-vision-based); the other has it calling `gemini_tools`'s "image understand" tool (satisfies AD-3, breaks AD-1 outright). Two implementers following different halves of the spine build genuinely incompatible `asset_analyst` agents.
*Fix:* Either strike "image understand" from `gemini_tools.py`'s scope and require `asset_analyst` to reason directly over image inputs itself, or narrow AD-1's "understanding" carve-out to explicitly exclude raw asset/image understanding (as distinct from narrative/semantic understanding) — pick one and make the Structural Seed match it.

**5. AD-7's global budget ceiling and AD-5's per-agent retry loop are two independent, unsynchronized spend-tracking owners.**
AD-7 binds `orchestrator/run.py` and checks the ceiling — implicitly at stage boundaries, since that's `run.py`'s only involvement per AD-2 (it delegates, never reasons). AD-5 binds "every agent" and lets each agent's own retry loop keep calling paid Gemini/Veo/STT tools up to its local iteration ceiling. Nothing requires an agent's loop to consult the live remaining run budget *before each retry's paid call* — an agent mid-retry-loop on an expensive Veo shot can blow through the whole run's budget ceiling before control ever returns to `run.py` for it to notice and halt per AD-7. Both components are individually AD-compliant; together they let the ceiling get exceeded before enforcement fires.
*Fix:* Add a rule requiring every paid tool call to check remaining budget synchronously (e.g. the tool wrapper itself consults `state/run_manifest.py` before invoking Gemini/Veo/STT), giving AD-5's loop and AD-7's ceiling one shared enforcement point instead of two.

---

## High

**6. `veo_tools.py`'s AD-12 "structured failure" and `contracts/`'s AD-4 pydantic validation model are two independently-owned schemas for the same data.**
AD-12 requires `veo_tools` to surface RAI-filter/operation-inspection failures as "a structured failure... feeding AD-4/AD-5's validate-and-retry loop," but AD-12 binds only `veo_tools.py`/`veo_agent.py`, while AD-4's schema-conformance gate binds `orchestrator/contracts/`. Nothing says the tool author's ad hoc failure shape and the contracts author's pydantic model must be the same type. Built separately (plausible: different owners, different files), the tool's output won't validate against the contract, or the retry loop reads a different failure taxonomy than what's actually produced.
*Fix:* Name a single contract class (e.g. `VeoFailureContract` in `orchestrator/contracts/`) that AD-12's tool output must literally be/serialize to, closing the schema gap.

**7. `contracts/*.py`'s per-stage persistence (AD-14) and `state/run_manifest.py`'s resume pointer are two separate mutation paths for "what stage is safely resumable."**
AD-14 says validated contract output is durably persisted "as soon as it passes AD-4," and resume "loads the last valid contract per stage and the prior session." The Consistency Conventions table explicitly separates run state (`orchestrator/state/`) from pipeline contract outputs (`metadata/*.json`/`orchestrator/contracts/`) as two different write targets. If a crash happens between "contract file written" and "run_manifest's stage/session pointer updated," resume can either re-run an already-validated stage (wasting AD-7 budget) or treat a stage as valid when its pointer wasn't actually advanced — with no AD requiring these two writes to be atomic or for the pointer to be derived rather than independently tracked.
*Fix:* Either make the resume pointer purely derived (scan `contracts/` for the newest valid per-stage record, don't track it separately in `run_manifest.py`), or require both writes inside one transaction/durable-write sequence.

**8. `visual_agent`'s still-vs-Veo shot-mode decision and `veo_agent`/`stills_agent`'s own AD-4/AD-5 self-correction loops have no assigned final authority.**
`visual_agent` (AD-2) owns "shot/visual planning," which per the pipeline's own history includes the still-vs-Veo classification per shot. But AD-4 lets `veo_agent`/`stills_agent` self-correct through failed QA, and AD-6 forbids silent fallback-asset substitution on exhausted retries — it says nothing about whether a generation agent may (or must not) reclassify a shot's *mode* after repeated QA failure, nor which contract that reclassification would be written into. Built independently: `veo_agent` could treat "shot assigned to me by the plan" as immutable (any Veo failure → hard-halt per AD-6, no mode change) while `stills_agent` is built to opportunistically pick up any shot the plan didn't explicitly forbid it from — two agents disagreeing about who has final say over one shot's mode.
*Fix:* Add a rule that `visual_agent`'s per-shot mode assignment is binding and exclusive; any change requires writing back into the same visual-plan contract (not a unilateral decision by the generation agent), and AD-6's hard-halt applies to the originally assigned mode only.

---

## Medium

**9. AD-4's "producing agent" performs semantic QA — but the subtitle-cue producer is deterministic code, not an agent.**
Subtitle cues come from `deterministic_tools`'s DP segmentation (AD-1 category 3, no judgment), yet AD-4 requires "the producing agent's own semantic/quality judgment" as one of the two mandatory gates. There is no agent that "produces" subtitle cues to hold that judgment. `voice_agent` (owns "word-timing + subtitle QA" per the Structural Seed) and `visual_agent` (owns shot/timing planning, which subtitle cues feed into) are both plausible owners of this QA step, and nothing in AD-2 or AD-4 assigns it to one over the other.
*Fix:* Name the subtitle-cue QA owner explicitly (the Structural Seed's own comment suggests `voice_agent`) directly in AD-2's responsibility list, so AD-4's "producing agent" phrase resolves unambiguously when the producer is deterministic code.

**10. AD-13's preflight ownership is ambiguous against AD-2's "every responsibility is an AgentDefinition" rule.**
AD-2 says the orchestrator "never performs a stage's reasoning itself" and every responsibility becomes a scoped `AgentDefinition`. AD-13 binds preflight to `orchestrator/run.py` directly, implying core deterministic logic with no agent. Preflight involves no judgment, so this is probably intended as an AD-2 exemption — but the spine never says so, leaving one implementer to build a bare function in `run.py` and another to build a `preflight_agent` (reasoning that "verifying auth" is itself a stage/responsibility per AD-2's literal wording).
*Fix:* State explicitly in AD-13 (or as a clause in AD-2) that preflight is core orchestrator logic, exempt from AD-2's agent-only-reasoning rule because it involves no judgment.

---

## Low

**11. Tool-file-to-MCP-server topology is unstated, which the naming convention depends on.**
The Consistency Conventions table fixes tool naming as `mcp__<server>__<tool>`, but the Structural Seed only shows four `tools/*.py` files without saying whether each is its own `create_sdk_mcp_server` (four servers) or all four mount on one combined server. Both satisfy every AD; only one produces a stable, predictable tool name per file.
*Fix:* State the file→server mapping once in the Structural Seed (e.g., "one server per `tools/*.py` file, server name = module name").

**12. The `<Stage>Contract` naming convention doesn't say how one agent producing multiple contract shapes should be named.**
`veo_agent` alone plausibly needs a seed contract, a result contract, and a contribution to `ProductionAssetsContract` — three shapes from one "stage." The convention ("one pydantic model per `metadata/*.json` shape... named `<Stage>Contract`") is ambiguous about whether "stage" means "agent" or "artifact," which two implementers could resolve differently (`VeoContract` with sub-fields vs. `VeoSeedContract`/`VeoResultContract` as siblings).
*Fix:* Clarify that naming keys off the `metadata/*.json` artifact, not the owning agent, so one agent can own several distinctly-named contracts.

---

File: `/Users/sachinpb/PycharmProjects/book_reels/docs/architecture/architecture-book_reels-2026-09-15/reviews/review-adversarial.md`
