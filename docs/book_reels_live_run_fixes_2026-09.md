# Live orchestrator run — problems and fixes (2026-09)

Reference note from the first end-to-end autonomous run after Story 3.1
(`conda run -n kayak-video python -m orchestrator.run source_images`).

Use this when a later run fails with a similar message, or when changing
stills / voice timing / delivery sync.

---

## What succeeded vs what failed

| Stage | Outcome |
| --- | --- |
| Asset / story / visual | Skipped (already validated) |
| Voice | Succeeded after retries (see below) |
| Veo | Skipped (all shots STILL) |
| Stills | All 6 approved after normal QA retries |
| Delivery | Failed twice on consecutive bugs; both fixed in code |

Normal stills QA rejects (duplicate mug, baked-in text) are **not** bugs —
the agent retried and approved. Ignore the Claude SDK line
`aclose(): asynchronous generator is already running`; it is cleanup noise.

---

## 1. Voice: impact phrases + STT 60s limit

### What happened

- Story narration paraphrased impact lines → AD-12 halt (“impact text not found”).
- Narration audio ~56–62s → Google STT V2 sync rejected with “Audio max 60 seconds”.
- Misleading logs made it look like a Claude budget failure.

### Fix (deterministic + contract)

- **Contract / story prompt:** `impact_text` must appear verbatim in narration
  (`orchestrator/contracts/final_story_plan.py`, `orchestrator/agents/story_agent.py`).
- **STT:** split WAV into ≤55s chunks, merge word timings with offsets
  (`orchestrator/tools/deterministic_tools.py`).
- Clearer per-step console logging in the voice stage (`orchestrator/run.py`).

No new agent. Re-run voice (or the full pipeline) after story/audio changes.

---

## 2. Stills shot 4: “outcome does not match the requested source shot”

### What happened

- Gemini **did** generate `generated/stills/shot_04.png` with the correct tool fingerprint.
- The stills agent’s structured output **replaced** that 64-char `shot_fingerprint`
  with a fabricated hash (sequence and image path were still correct).
- Orchestrator compared agent fingerprint ≠ live visual-plan fingerprint and halted.
- No `stills_agent_shot_4_attempt_*.json` was written on that path, so the bad
  outcome was hard to see (evidence recovered from the Claude session log).

### Fix (orchestrator stamps provenance)

Fingerprint is **not** creative agent output. After contract validate:

1. Require `shot_sequence` matches the requested shot (wrong shot still halts).
2. **Overwrite** top-level + nested `shot_fingerprint` with
   `shot_fingerprint(shot)` from the authoritative visual plan.
3. Persist invalid outcomes as `*_attempt_N.json` with
   `status: "INVALID_OUTCOME"` plus the raw structured output.

Same stamp applied to Veo outcomes.

**Code:** `_stamp_still_outcome_fingerprint` /
`_stamp_veo_outcome_fingerprint` in `orchestrator/run.py`.

**Tests:** `test_stills_agent_wrong_fingerprint_is_stamped_from_requested_shot`,
`test_stills_agent_wrong_sequence_halts_and_persists_invalid_outcome`
in `orchestrator/tests/test_full_chain_resume.py`.

---

## 3. Delivery: “Shot 2 does not start where the previous shot ended”

### What happened (simple words)

Voice timing followed spoken subtitle cues. Between scenes there are natural
pauses, e.g. shot 1 ended at **7.21s**, shot 2 started at **7.64s** (0.43s hole).
Delivery’s timeline builder requires shots to touch with **no gaps**.

Stills were fine; delivery refused the gapped visual-plan timing.

### Fix (deterministic gap fill — no agent)

Policy chosen: **absorb silence into the previous shot**.

- If shot *N* ends before shot *N+1* starts, set shot *N*’s `end_seconds` =
  shot *N+1*’s `start_seconds`.
- The previous still (or clip) simply holds through the pause.
- Overlaps still raise (bad data).

Where it runs:

1. **After fresh cue→shot backfill** — write contiguous timing into
   `metadata/visual_plan.json` (new voice runs, before stills).
2. **At delivery** — same fill **in memory** before building timeline, without
   rewriting on-disk visual-plan fingerprints (so already-approved stills
   stay resume-valid).

**Code:** `_close_inter_shot_timing_gaps` in `orchestrator/run.py`
(called from `_backfill_visual_plan_shot_timing_from_cues` and
`run_delivery_stage`).

**Tests:** `test_delivery_absorbs_inter_shot_cue_gaps_into_previous_shot`,
`test_close_inter_shot_timing_gaps_*` in
`orchestrator/tests/test_delivery_stage.py`.

---

## 4. Delivery: `SameFileError` on `timeline.json`

### What happened

Delivery wrote `timeline.json` **directly** to
`remotion/public/data/timeline.json`, then `sync_remotion_assets` tried to
`shutil.copy2` that path onto itself.

### Fix

- Write the pipeline artifact to **`metadata/timeline.json`**.
- Sync copies it into `remotion/public/data/timeline.json` (same pattern as
  subtitle cues / visual plan).
- `_copy_into_public` skips the copy when source and destination resolve to
  the same file (defensive).

**Code:** `TIMELINE_FILE` + `run_delivery_stage` in `orchestrator/run.py`;
`_copy_into_public` in `orchestrator/tools/deterministic_tools.py`.

**Tests:** delivery stage asserts `metadata/timeline.json`;
`test_sync_remotion_assets_same_path_is_noop` in
`orchestrator/tests/test_deterministic_tools.py`.

---

## How to re-run after these fixes

From repo root, with the `kayak-video` conda env:

```bash
conda run -n kayak-video python -m orchestrator.run source_images
```

Expected resume behavior with current artifacts:

- Early stages skip if contracts are valid.
- Stills skip if all 6 production assets match.
- Delivery builds `metadata/timeline.json`, syncs into Remotion, renders.

If delivery still fails, check the newest
`orchestrator_runs/failure_delivery_*.json` and this note’s section that
matches the reason string.

---

## Quick lookup

| Symptom | Cause | Fix type |
| --- | --- | --- |
| Impact phrase / AD-12 | Narration ≠ locked impact text | Contract + story prompt |
| STT “Audio max 60 seconds” | Sync recognize ≤60s | Chunked STT in code |
| Stills “does not match the requested source shot” | Agent mistyped fingerprint | Orchestrator stamp |
| Delivery gap/overlap between shots | Cue pauses vs contiguous timeline | Deterministic gap fill |
| `SameFileError` … `timeline.json` | Wrote timeline into public then copied onto itself | Write `metadata/timeline.json` + sync |
