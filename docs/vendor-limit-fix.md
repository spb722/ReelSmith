# Fix: a vendor limit is not a reel failure

Every edit is in `orchestrator/run.py`. Five changes: one new helper, then four
guards that use it. The before-blocks below are copied verbatim from the file,
so they can be searched for directly.

## The problem

Three times now an external vendor refusing service has been counted as the reel
being at fault:

| What happened | What it cost |
| --- | --- |
| Claude org spend limit | all 4 `visual_agent` attempts burned in 6 seconds |
| Claude session limit | $26.69 charged for a call that never ran |
| Codex usage limit | 2 of shot 3's 3 Veo attempts burned, no seed ever drawn |

The retry ceiling and the conservative cost charge are both correct for what they
were built for: a shot that keeps coming out wrong, and an agent that crashed
*after* a paid call may have gone out. Neither is true when the vendor said no
before any work started. Those need the opposite treatment — stop cleanly, charge
nothing, keep the attempt.

The Claude agent itself diagnosed this correctly in
`veo_agent_shot_3_attempt_2.json` and the orchestrator counted it against the
ceiling anyway:

> "This is an infrastructure/quota failure, not a QA or content failure — retry
> once Codex credits reset, keeping seed_prompt_level=1."

---

## 1. Add the classifier

Put this next to `_is_likeness_rejection`, just **above** line 837
(`LIKENESS_REJECTION_CODES = {"RAI_FILTERED", "UNSAFE_SEED"}`), so the two
"classify a failure" helpers sit together.

```python
# Vendor limits are not reel problems. A quota, auth or session-limit refusal
# means the work was never attempted, so it must not consume an attempt or
# charge the ceiling -- retrying before the limit resets only burns the
# remaining attempts on the same refusal.
VENDOR_LIMIT_MARKERS = (
    "usage limit",          # Codex / ChatGPT
    "session limit",        # Claude
    "monthly spend limit",  # Claude org
    "rate limit",
    "quota",
    "credentials",
    "unauthorized",
)


def _is_vendor_limit(text: str) -> bool:
    """True when an error says a vendor refused service, not that work failed."""

    lowered = (text or "").lower()
    return any(marker in lowered for marker in VENDOR_LIMIT_MARKERS)
```

---

## 2. Claude SDK exception in the agent stages — around line 221

This is what burned all four `visual_agent` attempts. `count` was already
incremented at line 213, before the call, so it has to be given back.

`halt(...)` is the local closure defined at line 197; it already carries
`stage_id` and `partial_paths`.

**Find:**

```python
        except Exception as exc:
            feedback = f"Claude attempt failed: {type(exc).__name__}: {exc}"
            previous_output = None
```

**Replace with:**

```python
        except Exception as exc:
            if _is_vendor_limit(str(exc)):
                # Refused, not spent: give the attempt back before halting.
                count -= 1
                manifest.iteration_counts[stage_id] = count
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return halt(f"Blocked by a vendor limit, nothing was attempted: {exc}")
            feedback = f"Claude attempt failed: {type(exc).__name__}: {exc}"
            previous_output = None
```

---

## 3. Veo agent exception — around line 1129

This is the $26.69. The existing comment and `charged_cost = remaining_budget`
stay exactly as they are — they are right for a genuine mid-tool crash, where a
Veo clip really may have been paid for. Only add the guard in front.

**Find:**

```python
            except Exception as exc:
                # Tool execution may have reached a paid boundary before the
```

**Replace with:**

```python
            except Exception as exc:
                if _is_vendor_limit(str(exc)):
                    count -= 1
                    manifest.iteration_counts[stage_id] = count
                    save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                    return _halt(
                        "veo_agent",
                        "VeoOutcomeContract",
                        f"Blocked by a vendor limit, nothing was attempted: {exc}",
                        attempt_count=count,
                        partial_artifact_paths=attempt_paths,
                    )
                # Tool execution may have reached a paid boundary before the
```

---

## 4. Stills agent exception — around line 1439

Same shape as #3.

**Find:**

```python
            except Exception as exc:
                # Same conservative accounting as run_veo_stage: no real cost
```

**Replace with:**

```python
            except Exception as exc:
                if _is_vendor_limit(str(exc)):
                    count -= 1
                    manifest.iteration_counts[stage_id] = count
                    save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                    return _halt(
                        "stills_agent",
                        "StillOutcomeContract",
                        f"Blocked by a vendor limit, nothing was attempted: {exc}",
                        attempt_count=count,
                        partial_artifact_paths=attempt_paths,
                    )
                # Same conservative accounting as run_veo_stage: no real cost
```

---

## 5. The Codex case — around line 1252 (Veo) and 1559 (stills)

**This is the one that just stopped `sweaty_tax`, and it is a different path.**
Codex's quota error does not arrive as an exception. The tool catches it and
returns a perfectly normal failure contract with `retryable=True`, so the
orchestrator treats it as a shot that needs another go.

### Veo — line 1252

**Find:**

```python
            failure = outcome.failure
            correction = failure.reason
            if not failure.retryable:
                return _halt(
                    "veo_agent",
                    "VeoFailureContract",
```

**Replace with:**

```python
            failure = outcome.failure
            correction = failure.reason
            if _is_vendor_limit(failure.reason):
                # The image provider is out of credits; no seed was drawn, so
                # this attempt achieved nothing and must not be counted.
                count -= 1
                manifest.iteration_counts[stage_id] = count
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return _halt(
                    "veo_agent",
                    "VeoFailureContract",
                    f"Image provider is out of credits, nothing was drawn: {failure.reason}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths + failure.partial_artifact_paths,
                )
            if not failure.retryable:
                return _halt(
                    "veo_agent",
                    "VeoFailureContract",
```

### Stills — line 1559

Identical, with `stills_agent` / `StillFailureContract`:

**Find:**

```python
            failure = outcome.failure
            correction = failure.reason
            if not failure.retryable:
                return _halt(
                    "stills_agent",
                    "StillFailureContract",
```

**Replace with:**

```python
            failure = outcome.failure
            correction = failure.reason
            if _is_vendor_limit(failure.reason):
                count -= 1
                manifest.iteration_counts[stage_id] = count
                save_run_manifest(manifest, state_dir=RUN_STATE_DIR)
                return _halt(
                    "stills_agent",
                    "StillFailureContract",
                    f"Image provider is out of credits, nothing was drawn: {failure.reason}",
                    attempt_count=count,
                    partial_artifact_paths=attempt_paths + failure.partial_artifact_paths,
                )
            if not failure.retryable:
                return _halt(
                    "stills_agent",
                    "StillFailureContract",
```

---

## Check it

```bash
PY=/opt/homebrew/anaconda3/envs/kayak-video/bin/python
$PY -m py_compile orchestrator/run.py
$PY -m pytest orchestrator/tests -q -p no:cacheprovider
```

Baseline is **5 failures + 12 errors** — all pre-existing and unrelated
(`test_alignment_recovery` ×2, `test_regression_harness`, `test_visual_agent`,
`test_voice_agent`, `test_timeline_converter` ×12). Anything beyond that is a
regression from these edits.

Quick check that the classifier does what you expect:

```bash
$PY -c "
from orchestrator.run import _is_vendor_limit
for t in [
    \"You've hit your session limit · resets 3am\",
    \"You've hit your org's monthly spend limit\",
    \"ERROR: You've hit your usage limit. Upgrade to Pro\",
    'Veo safety filtering rejected one or more generated media outputs',
    'Codex returned a 1080x1920 image but the reel needs vertical 9:16',
]:
    print(f'{_is_vendor_limit(t)!s:5} {t[:60]}')
"
```

The first three must be `True`, the last two `False`.

---

## Two things this does not fix

**String matching is fragile.** If `quota` ever appears in an unrelated message,
a run stops that should have retried. Stopping early is the safer failure, but
keep the marker list tight — resist adding a bare `"limit"`.

**A hard process death still consumes an attempt.** The counter is incremented
*before* the call (lines 213, 1116, 1426), so Ctrl-C or an OOM bumps it and
writes no record — that is exactly what shot 3's phantom third attempt was. The
honest fix is to increment only once the call has started, which is a larger
change than these guards.

---

## Unblocking `sweaty_tax` (independent of the code)

Shot 3 has one genuine failure, not three: attempt 1 was a real Veo refusal,
attempts 2 and 3 were Codex being out of credits.

```bash
$PY - <<'EOF'
import json
from pathlib import Path

p = Path("projects/sweaty_tax/orchestrator_runs/run_manifest.json")
m = json.loads(p.read_text())
m["iteration_counts"]["veo_agent_shot_3"] = 1
p.write_text(json.dumps(m, indent=2) + "\n")
print(m["iteration_counts"])
EOF
```

Then, once Codex credits reset:

```bash
MAX_BUDGET_USD=60 IMAGE_PROVIDER=codex $PY -m orchestrator.run --project sweaty_tax
```

It resumes at shot 3 with the less-detailed-face wording it had already worked
out, because `_prior_likeness_rejections` reads that from the attempt records on
disk rather than from the counter.
