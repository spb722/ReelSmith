---
title: 'Recover reliable narration and subtitles after alignment failures'
type: 'bugfix'
created: '2026-09-20'
status: 'in-progress'
route: 'full'
review_loop_iteration: 0
baseline_commit: '5b4c5d4ca993a1a2095cd31a685746c67e808ac4'
context: []
---

<frozen-after-approval reason="User requested implementation of the diagnosed fixes in conversation">

## Intent

**Problem:** The current British Cycling reel repeatedly regenerates narration when literal STT word matching scores about 92.9%, below the subtitle gate. Diagnostics lose the differing words, Algieba's environment override is ignored, and agent streams close noisily. Users cannot recover an exhausted voice stage without editing generated state.

**Approach:** Make equivalent spoken/written forms align deterministically with traceable timing; preserve diagnostics and reuse valid audio on downstream retries; respect voice selection; give explicit bounded voice retry control; finish SDK response streams cleanly.

## Boundaries & Constraints

**Always:** Preserve existing local artifacts and user changes. Work on character-reference. Keep 98% subtitle acceptance and report literal and normalized match measures honestly. Keep deterministic subtitle segmentation and exact canonical display text. Cache provenance includes narration, voice direction, selected voice/model, and actual audio hash; stale or unverifiable audio cannot silently satisfy reuse. Check paid-step budget and record incurred costs. Explicit retry keeps cumulative spend, archives earlier voice attempts, and resets only the voice attempt ceiling. Run tests in kayak-video, no network generation. User already authorized implementation on the known dirty branch; no additional plan permission required.

**Never:** Edit locked narration/visual-plan/subtitle artifacts, hand-edit run manifests, lower quality thresholds, ask an LLM to invent timestamps, introduce forced alignment dependencies, upgrade dependencies, launch paid generation, commit/stage unrelated generated files, or overwrite unrelated local work.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Equivalent formatting | Script words vs numeric/percent or hyphenated STT forms | Deterministic many-to-many mapping preserves script text; bounded monotonic times from observed spans; distinguish equivalence from literal matches | Unsupported or ambiguous forms remain mismatches |
| Real disagreement | Missing/changed/extra speech | Evidence saved before rejection, no fabricated acceptance; report mismatches | Gate remains strict; no blind repeated TTS for deterministic failure |
| Downstream transient failure | Valid cached audio, STT request fails | Retry recognition using same audio; charge new calls only | Bounded attempts and useful failure report |
| Voice configuration | TTS_VOICE_NAME=Algieba | Actual API request and logging use Algieba | Empty setting fails clearly |
| Stale cache | Changed narration/voice/direction/model or audio bytes | Cache rejected; stale timing/cues cannot silently skip | Regenerate when explicitly running pipeline |
| Exhausted run | Three voice attempts persisted | Default run stays halted; --retry-voice permits fresh bounded attempts, preserving spend and old evidence | No manual metadata editing |
| Agent completion | ResultMessage followed by cleanup in generator | Stream drains before stage returns; stages use one event loop | Cleanup exceptions surface |

</frozen-after-approval>

## Code Map

- `orchestrator/tools/deterministic_tools.py`: `align_words` uses one-token edit alignment; normalize_word only cleans punctuation/case. 0.85 extraction gate vs 0.98 subtitle gate. `extract_word_timing` returns transcript/words but does not save them. `build_subtitle_cues` rechecks literal ratio. Existing chunking splits at 55 seconds; do not expand scope to new speech service.
- `orchestrator/tools/gemini_tools.py`: hardcoded TTS_VOICE_NAME=Gacrux; generate_narration_audio writes audio/narration.wav. Prompt includes voice direction. Canonical path must stay.
- `orchestrator/run.py`: `run_voice_pipeline` regenerates TTS on every retry and wraps failures as synthetic Claude ResultMessage; `run_voice_stage` and generic run_bounded_agent_stage both have persistence checks. `main` separately asyncio.run's each stage. Avoid a broad shared-stage rewrite.
- `orchestrator/contracts/subtitle_cues.py`: alignment gate 0.98 and narration/audio existence resume checks. Keep backward compatibility for existing approved contracts where safe; new cache provenance must be checked when available, and an explicit voice override must not silently reuse legacy Gacrux cues.
- `orchestrator/agents/{asset_analyst,story_agent,visual_agent,veo_agent,stills_agent}.py`: immediate return inside query iterator; retain final result and exhaust iterator before return. Current SDK docs show full iteration; no dependency upgrade needed.
- `orchestrator/tests/test_voice_agent.py`: fake tools and temporary cwd; existing tests expect full TTS retry and need intentional updates for reuse. `test_deterministic_tools.py` exercises actual alignment/segmentation. Agent tests monkeypatch query.

## Tasks & Acceptance

**Execution:**
- [ ] Voice configuration and audio reuse with provenance; alignment diagnostics persisted before quality rejection.
- [ ] Conservative number/percentage/hyphen equivalence alignment with original text and observed time spans, quality gates and regression coverage.
- [ ] Voice-only retry option, archived evidence, correct costs/error labels, safe resume behavior.
- [ ] Drain all agent streams and run sequential stages in one event loop, with lifecycle tests.
- [ ] Document recovery command and diagnostics in README; run targeted and broader offline tests.

**Acceptance Criteria:**
- Given equivalent numeric representations, when aligning and segmenting, then canonical captions pass without lowering the 98% quality bar or modifying narration.
- Given genuine word loss or substitutions, when processing, then diagnostics identify the problem and insufficient alignment still halts.
- Given failed subtitle/recognition work after successful TTS, when retrying, then valid matching audio is reused and no duplicate TTS cost is charged.
- Given the user's exhausted run and Algieba setting, when invoking documented recovery, then only voice attempts reset, old evidence/spend survive, and Algieba is actually requested.
- Given completed agent stages, when advancing the pipeline, then generator cleanup completes on the same live event loop.

## Implementation Notes

Current working-tree changes are generated reel assets/state, unrelated to source modifications. No user run should be launched by implementation. Existing full-suite reference-reel fixture tests may fail because production metadata is absent/different; classify and report against baseline instead of modifying assets to satisfy them. Use only bounded subagents if needed; do not recursively invoke bmad-build. Favor a small auditable normalizer over a permissive semantic matcher.

## Spec Change Log

## Review Triage Log

## Verification

- `conda run -n kayak-video python -m pytest orchestrator/tests -q` (offline mocks); compare pre-existing fixture failures to baseline.
- Targeted tests cover every matrix row including cache invalidation, retry exhaustion/reset, actual requested voice, budget accounting, and stream finalization.
- Inspect final diff; no generated production files changed by this work.
