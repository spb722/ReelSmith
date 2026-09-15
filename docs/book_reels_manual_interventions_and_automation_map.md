# Book Reels Manual Intervention & Automation Map

## 1. Purpose

This document identifies every area in the current `book_reels` workflow where manual intervention was required during the production of the reel **“The Backwards Law: Don’t Try.”**

The purpose is to make future automation work explicit.

This document answers:

- Where did the user personally have to run commands?
- Where did the user have to inspect outputs?
- Where did the user have to approve or reject results?
- Where did the user have to copy files or move assets?
- Where did the user have to open Claude Code or another coding agent?
- Which manual steps are purely mechanical and should disappear?
- Which manual steps are creative approval gates and may be worth keeping?
- What should a future autonomous agent own end-to-end?

The long-term goal is:

> The user should stop acting as the pipeline orchestrator.

The ideal future interaction is closer to:

```text
Provide source material
→ Approve story
→ Optionally approve visual direction
→ Review final reel
```

Everything between those points should be automated.

---

# 2. Current Manual-Intervention Map

The table below lists the major manual steps that occurred in the current workflow.

| Stage | Manual action performed | Type | Automation potential |
|---|---|---|---|
| Environment setup | Activate `kayak-video` Conda environment | Mechanical | Fully automatable |
| Google Cloud setup | Ensure ADC / Vertex authentication works | One-time setup | Mostly automatable as preflight |
| Environment variables | Set project, location, GCS output URI | Mechanical | Fully automatable |
| Source ingestion | Put source screenshots into `source_images/` | Human input | User still provides source material |
| Asset ingestion | Run `ingest_assets.py` | Mechanical | Fully automatable |
| Image analysis | Trigger pass-1 understanding | Mechanical | Fully automatable |
| Story generation | Run story generation and quality loop | Mechanical | Fully automatable |
| Story approval | Decide story is good enough and lock it | Human judgment | Keep as optional approval gate |
| Voice generation | Run `generate_voice.py` | Mechanical | Fully automatable |
| Voice review | Listen to narration and judge tone / pacing | Human judgment | Keep optional |
| Word timing | Run `extract_word_timing.py` | Mechanical | Fully automatable |
| Word-alignment check | Confirm perfect transcript alignment | Mechanical validation | Fully automatable |
| Subtitle cue generation | Run deterministic segmentation | Mechanical | Fully automatable |
| Subtitle approval | Check cue grouping / readability | Human judgment | Mostly automatable |
| Visual Director | Run `visual_director.py` | Mechanical | Fully automatable |
| Video-vs-still choice | Override conservative planner and choose Veo selectively | Creative judgment | AI recommendation + human override |
| GCS configuration | Set `VEO_OUTPUT_GCS_URI` | Mechanical | Fully automatable |
| Veo seed generation | Run `prepare_veo_seed_images.py --shot X` | Mechanical | Fully automatable |
| Veo seed QA | Inspect each generated seed | Visual judgment | AI QA + optional human review |
| Prompt correction | Tighten prompt after bad result | Creative/debugging | Automatable retry loop |
| Veo generation | Run `generate_veo_clips.py --shot X` | Mechanical | Fully automatable |
| Veo polling | Wait for long-running operation | Mechanical | Already automatable |
| Veo failure diagnosis | Inspect traceback / operation result | Debugging | Automatable |
| GCS recovery | Run bucket listing to recover overwritten result info | Mechanical/debugging | Should disappear |
| Clip download | `gcloud storage cp` | Mechanical | Fully automatable |
| Clip visual review | Watch generated MP4 | Human judgment | AI QA + optional human approval |
| Still generation | Run `prepare_remotion_stills.py --shot X` | Mechanical | Fully automatable |
| Still QA | Inspect generated stills | Human judgment | AI QA + optional human approval |
| Still regeneration | Decide whether to regenerate | Judgment | Automatable QA/retry loop |
| Remotion install | Run `npx create-video@latest` | One-time setup | Not needed per reel |
| Installer choices | Blank template, Tailwind no, skills selected | One-time setup | Should disappear |
| Asset copy into Remotion | Copy audio / stills / videos / JSON into `public/` | Mechanical | Fully automatable |
| Open Claude Code | Start coding agent manually | Manual orchestration | Replace with autonomous agent or fixed renderer |
| Paste Claude prompts | Timeline, subtitles, motion, typography, polish | Manual orchestration | Should disappear |
| Timeline coding | Agent builds `timeline.ts`, `BookReel.tsx`, etc. | Coding | Should become reusable renderer |
| Start Remotion Studio | Run `npm run dev` | Mechanical | Automatable |
| Timeline QA | Check order, duration, narration | Human QA | Mostly automatable |
| Caption QA | Confirm captions display correctly | Human QA | Automatable via frame inspection |
| Still-motion QA | Judge subtlety of zoom/drift | Aesthetic QA | AI assisted |
| Impact typography QA | Judge “Don’t try.” moment | Aesthetic QA | AI assisted |
| Final polish | Ask coding agent to add transitions | Manual orchestration | Should become renderer configuration |
| Render V1 | Run Remotion render command | Mechanical | Fully automatable |
| Watch V1 | Review entire final video | Human QA | Keep as final approval gate |
| Diagnose Shot 2 issue | Compare raw asset vs render | Manual debugging | Automatable diagnostics |
| Run `ffprobe` | Inspect frame rate | Mechanical debugging | Automatable |
| Run FFmpeg interpolation | Experimental debugging step | Manual debugging | Not part of normal pipeline |
| Restore original clip | Undo failed interpolation path | Manual debugging | Should disappear |
| Fix video transforms | Tell coding agent to remove transforms | Manual coding/debugging | Permanent renderer fix |
| Apply same fix to Shots 5/6 | Repeat stable-video behavior | Manual coding/debugging | Permanent renderer fix |
| Re-render after fixes | Run render again | Mechanical | Fully automatable |
| Final completion decision | Decide reel is done | Human judgment | Keep |

---

# 3. Manual Effort by Category

The current manual work can be grouped into three categories.

## 3.1 Mechanical Manual Work

These are actions that do not require creative judgment.

Examples:

```text
activate environment
set environment variables
run Python scripts
run gcloud commands
download clips
copy files
start Remotion
render MP4
run ffprobe
```

These should be eliminated almost completely.

## 3.2 Human Approval Gates

These involve real judgment.

Examples:

```text
approve story
approve voice
approve image composition
approve Veo motion
approve final reel
```

These can be reduced but should probably remain available.

## 3.3 Creative / Editorial Judgment

These were moments where human direction improved the project.

Examples:

```text
override the Visual Director and use Veo selectively
reject duplicate tombstone image
recognize that a video feels visually wrong
judge whether “Don’t try.” lands emotionally
decide whether motion feels tasteful
```

These should be AI-assisted, not necessarily fully removed.

---

# 4. Detailed Manual Interventions

## 4.1 Environment Activation

The user manually worked inside:

```text
kayak-video
```

Typical command:

```bash
conda activate kayak-video
```

### Current problem

Every new terminal session may require remembering which environment is correct.

### Future automation

A launcher can enforce the correct interpreter automatically.

Example:

```bash
./run_pipeline.sh
```

or:

```bash
conda run -n kayak-video python pipeline.py
```

The user should not need to manually activate the environment.

---

# 5. Google Cloud Preflight

The user had to ensure:

- ADC was configured,
- the Vertex project was correct,
- permissions worked,
- the project ID was correct.

Project:

```text
gen-lang-client-0240752803
```

### Future automation

Add a preflight stage:

```text
check ADC
check project
check model access
check bucket write permission
check bucket read permission
```

If any fail, stop with a clear actionable error.

---

# 6. Environment Variables

The user manually set values such as:

```bash
export GOOGLE_CLOUD_PROJECT=gen-lang-client-0240752803
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_GENAI_USE_ENTERPRISE=True
export VEO_OUTPUT_GCS_URI=gs://sachin-kayaking-video-test/book_reels/veo/
```

### Future automation

Move these into:

```text
.env
config.yaml
pipeline_config.json
```

or hard-code safe project defaults in one central configuration file.

The user should not repeatedly export variables manually.

---

# 7. Source Material Placement

The user manually provides source screenshots.

Example:

```text
source_images/
```

This is a valid human input point.

### Future automation

The user can:

- drag files into a folder,
- upload them through UI,
- or pass paths to a command.

Everything after that should begin automatically.

---

# 8. Asset Ingestion

Manual action:

```bash
python ingest_assets.py
```

### Future automation

This should become an automatic first pipeline stage.

No human should need to run it directly.

---

# 9. Image Understanding

The semantic image-analysis stage was manually triggered as part of development.

### Future automation

After ingestion:

```text
assets.json
→ image analysis
→ analyzed_assets.json
```

should run automatically.

---

# 10. Story Generation

The Story Director and automated quality loop were part of the pipeline.

The user still had to supervise when the story was considered final.

### Future automation

The system should:

1. generate story,
2. score story,
3. revise until thresholds pass,
4. present the best version.

Then optionally pause:

```text
Approve story?
[Approve]
[Regenerate]
[Edit]
```

This is one of the most useful human gates to retain.

---

# 11. Voice Generation

Manual command:

```bash
python generate_voice.py
```

### Future automation

Once story is approved:

```text
story approved
→ automatically generate narration.wav
```

No manual command needed.

---

# 12. Voice QA

The user listened to the generated narration and accepted:

- voice,
- pacing,
- pauses,
- tone.

This is subjective.

### Future automation

Automated checks can verify:

- duration,
- silence gaps,
- no clipping,
- output exists,
- expected sample rate.

But tone quality remains a useful human gate.

Recommended future behavior:

```text
Generate voice
→ run automatic audio QA
→ optional “Listen / Approve”
```

---

# 13. Word Timing

Manual command:

```bash
python extract_word_timing.py
```

### Future automation

This should happen immediately after voice generation.

Validation can automatically require:

```text
all narration words aligned
no substitutions
no missing words
no extras
```

If alignment fails:

```text
retry / stop / diagnose
```

No human command required.

---

# 14. Subtitle Cue Generation

The deterministic subtitle stage should also run automatically.

### Current manual role

The user visually checked caption behavior later in Remotion.

### Future automation

Generate cue JSON immediately after word timing.

Run automated checks:

```text
no cue exceeds max length
no missing words
no overlap
all words covered
important phrases protected
```

---

# 15. Visual Planning

Manual action:

```bash
python visual_director.py
```

### Future automation

After subtitles:

```text
story + semantic assets + timing
→ visual plan
```

This can be automatic.

---

# 16. Video-vs-Still Selection

This was one of the important creative manual interventions.

The Visual Director originally recommended avoiding AI video.

The user intentionally chose:

```text
Shot 2
Shot 5
Shot 6
```

for Veo.

### Future automation

The Visual Director should output a recommendation score per shot:

```json
{
  "shot": 6,
  "recommended_mode": "VEO",
  "motion_value": 0.93,
  "hallucination_risk": 0.18,
  "cost_score": 0.42
}
```

Then the system can choose automatically or ask the user.

Recommended:

```text
Use AI recommendation by default.
Allow human override.
```

---

# 17. Veo Seed Generation

Current workflow required separate commands:

```bash
python prepare_veo_seed_images.py --shot 2
python prepare_veo_seed_images.py --shot 5
python prepare_veo_seed_images.py --shot 6
```

### Future automation

The orchestrator should read the visual plan and automatically identify all Veo shots.

Example:

```text
Veo shots = [2, 5, 6]
```

Then generate all required seeds.

---

# 18. Seed Image QA

The user inspected generated seed images.

This caught issues such as poor composition or wrong source behavior.

### Future automation

Add AI visual QA.

For each seed, verify:

```text
no app UI
no status bar
no buttons
no accidental text
correct subject
correct subject count
correct 9:16 composition
correct visual style
no photorealistic drift
emotion matches shot
```

If QA fails:

```text
automatic prompt adjustment
→ regenerate
→ recheck
```

Only show the user if:

- retry limit exceeded,
- confidence low,
- or supervised mode is enabled.

---

# 19. Still Asset Generation

Current commands:

```bash
python prepare_remotion_stills.py --shot 1
python prepare_remotion_stills.py --shot 3
python prepare_remotion_stills.py --shot 4
python prepare_remotion_stills.py --shot 7
python prepare_remotion_stills.py --shot 8
```

### Future automation

Read the visual plan:

```text
still shots = [1, 3, 4, 7, 8]
```

Generate automatically.

---

# 20. Still Visual QA

The user caught the duplicate-tombstone defect.

This is a real creative/visual QA intervention.

### Future automation

An agent can inspect each generated still for:

- duplicate subjects,
- unexpected objects,
- accidental text,
- wrong tone,
- composition issues.

For Shot 1 specifically, constraints like:

```text
exactly one tombstone
```

can be machine-checked with vision.

---

# 21. Veo Generation

Current manual commands:

```bash
python generate_veo_clips.py --shot 2
python generate_veo_clips.py --shot 5
python generate_veo_clips.py --shot 6
```

### Future automation

The orchestrator should:

1. verify required seed exists,
2. generate clip,
3. poll operation,
4. parse result,
5. save metadata,
6. download automatically,
7. run QA,
8. retry if needed.

The user should not have to run Veo one shot at a time.

---

# 22. Veo Polling

The script already handled operation polling.

This is already mostly automated.

### Future improvement

The top-level orchestrator should own this completely.

---

# 23. Veo Failure Diagnosis

The user had to surface errors and upload results.

Example failure:

```text
No generated video URI returned
```

### Future automation

The script should automatically inspect:

```text
operation.response
operation.result
video.uri
video.video_bytes
RAI filtered count
RAI filtered reasons
operation error
```

Then classify the failure:

```text
SDK parsing issue
safety filtering
permission issue
quota issue
model availability
output failure
```

The system should retry only when appropriate.

---

# 24. GCS Result Recovery

The user had to manually run:

```bash
gcloud storage ls --long --recursive \
  gs://sachin-kayaking-video-test/book_reels/veo/
```

because results JSON had been overwritten.

### This should disappear completely

Future solution:

- merge per-shot metadata,
- preserve historical results,
- create a production-asset manifest.

No human should ever need bucket timestamps to determine which clip belongs to which shot.

---

# 25. GCS Downloads

The user manually downloaded production clips:

```bash
gcloud storage cp ...
```

### Future automation

After successful generation:

```text
GCS URI
→ automatic local download
→ deterministic local filename
```

Example:

```text
generated/veo/shot_02.mp4
```

---

# 26. Clip Visual QA

The user watched generated MP4s.

### Future automation

Agent video QA can check:

- screenshot UI leakage,
- camera instability,
- extra objects,
- style drift,
- text artifacts,
- subject distortion,
- unnatural zooming,
- broken first/last frames.

Human review can remain optional.

---

# 27. Remotion Installation

The user manually created the Remotion project using:

```bash
npx create-video@latest remotion
```

Then selected:

```text
Blank
Continue inside existing Git repo
TailwindCSS: No
selected Remotion skills
```

### Future automation

This is a one-time project setup.

It should never happen for future reels.

The reusable renderer should remain permanently in the repository.

---

# 28. Copying Assets into Remotion

Manual commands copied:

```text
narration.wav
stills
Veo videos
subtitle_cues.json
visual_plan.json
```

into:

```text
remotion/public/
```

### Future automation

Create a script such as:

```text
sync_remotion_assets.py
```

or make the renderer read directly from the production asset manifest.

This entire copy step should disappear.

---

# 29. Opening Claude Code

A major manual intervention was repeatedly opening Claude Code and pasting highly scoped prompts.

This happened for:

- base timeline,
- subtitles,
- animated stills,
- impact typography,
- final polish,
- video transform fixes.

### Why this happened

We were building the renderer for the first time.

### Future state

This should not happen per reel.

The Remotion renderer should already contain reusable components.

---

# 30. Timeline Coding

Claude created:

```text
src/timeline.ts
src/BookReel.tsx
```

and modified composition registration files.

### Future automation

The renderer should read a data file instead of requiring coding.

Example:

```json
{
  "fps": 30,
  "durationFrames": 1573,
  "shots": [
    {
      "sequence": 1,
      "type": "still",
      "asset": "/stills/shot_01.png",
      "start": 0,
      "end": 12,
      "motion": "slow_push"
    }
  ]
}
```

Then no agent needs to write React for each reel.

---

# 31. Subtitle Coding

The user asked Claude to implement subtitles.

### Future state

Subtitles should already be a permanent reusable component.

For each new reel:

```text
subtitle_cues.json
→ automatic render
```

No coding agent needed.

---

# 32. Still Motion Coding

Claude added shot-specific still motion.

### Future state

Motion should be data-driven.

Example:

```json
{
  "motion": {
    "type": "slow_push",
    "scaleStart": 1.0,
    "scaleEnd": 1.07
  }
}
```

No code changes per reel.

---

# 33. Impact Typography Coding

The “Don’t try.” cue received special treatment.

### Future state

Impact typography should be driven by cue metadata.

Example:

```json
{
  "cue_id": "cue_005",
  "style": "IMPACT"
}
```

The renderer should automatically:

- suppress normal caption,
- render impact component,
- respect timing.

No coding prompt required.

---

# 34. Transition Coding

Claude added final polish and transitions.

### Future state

Transitions should be declarative:

```json
{
  "transitionOut": {
    "type": "dissolve",
    "frames": 6
  }
}
```

or chosen automatically by shot adjacency.

---

# 35. Starting Remotion Studio

Manual command:

```bash
npm run dev
```

### Future automation

Studio can remain a developer tool.

For normal production, the pipeline can render previews automatically.

The user should not need to open Studio unless they want to inspect the timeline.

---

# 36. Timeline QA

The user manually checked:

- total duration,
- shot order,
- narration,
- correct Veo clips.

### Future automation

Structural validation should check:

```text
8 shots present
no missing assets
no frame gaps
no unintended overlaps
duration = 1573 frames
narration starts at frame 0
all shot boundaries match visual plan
```

Then optionally render a low-res preview.

---

# 37. Caption QA

The user visually checked that captions appeared and aligned correctly.

### Future automation

Possible checks:

- sample frames at cue centers,
- confirm visible text layer,
- OCR or component-level validation,
- safe-area checks,
- overlap checks.

Human review can remain optional.

---

# 38. Still-Motion QA

The user judged whether slow movement felt tasteful.

### Future automation

Agent can check numerical constraints:

```text
scale delta below threshold
no black edges
no abrupt reset
no excessive translation
```

Aesthetic judgment may still benefit from human review.

---

# 39. Impact Typography QA

The user checked whether:

```text
“Don’t try.”
```

felt right.

### Future automation

Technical checks can confirm:

```text
normal subtitle suppressed
impact text active only in intended range
no overlap
timing matches cue
```

Emotional quality may remain a human review point.

---

# 40. Rendering V1

Manual command:

```bash
npx remotion render BookReel out/book_reel_v1.mp4
```

### Future automation

The orchestrator should render automatically.

Example:

```text
pipeline complete
→ render V1
→ run QA
→ output final path
```

---

# 41. Watching Final V1

The user watched the entire rendered MP4.

This is a meaningful human gate.

### Recommended future behavior

Keep:

```text
Final V1 review
```

as an optional stop before publishing.

---

# 42. Debugging Shot 2

The user noticed a fast zoom / shuttering issue.

Manual debugging involved:

```text
inspect raw clip
run ffprobe
create 30fps test
re-render
compare
```

### Future automation

A diagnostic agent should compare:

```text
raw source
vs
Remotion public copy
vs
final render
```

Then identify the first stage where the defect appears.

---

# 43. FFprobe

Manual command:

```bash
ffprobe ...
```

### Future automation

Media metadata should be collected automatically when clips are ingested.

Example stored fields:

```json
{
  "fps": 24,
  "width": 720,
  "height": 1280,
  "duration": 8.0,
  "codec": "h264"
}
```

---

# 44. FFmpeg Interpolation Experiment

A 24→30 fps conversion was tried manually.

It did not solve the issue.

### Future automation

This should not be part of normal production.

Only use frame-rate conversion when a diagnostic agent proves it is needed.

---

# 45. Video Transform Debugging

The true problem was Remotion transform logic.

The user had to ask Claude to fix Shot 2, then Shots 5 and 6.

### Future state

This fix should remain permanent in the renderer.

Invariant:

```text
video shots:
transform = none
scale = 1
translate = 0
```

Transitions may use opacity only.

This manual debugging step should disappear entirely.

---

# 46. The Biggest Automation Opportunity: Orchestration

The largest amount of user effort was not generation itself.

It was orchestration.

The recurring pattern was:

```text
run command
→ inspect output
→ upload / report
→ run next command
→ copy asset
→ open agent
→ paste prompt
→ preview
→ repeat
```

This should become one orchestrated process.

Example future command:

```bash
python pipeline.py source_images/
```

Conceptually:

```text
INGEST
↓
ANALYZE
↓
WRITE STORY
↓
STORY QA
↓
OPTIONAL STORY APPROVAL
↓
GENERATE VOICE
↓
WORD TIMING
↓
SUBTITLE CUES
↓
VISUAL PLAN
↓
CLASSIFY STILL / VEO
↓
GENERATE CLEAN IMAGES
↓
AUTOMATIC IMAGE QA
↓
AUTO-RETRY FAILURES
↓
GENERATE VEO
↓
AUTOMATIC VIDEO QA
↓
DOWNLOAD ASSETS
↓
BUILD PRODUCTION ASSET MANIFEST
↓
REMOTION RENDER
↓
AUTOMATIC TECHNICAL QA
↓
OUTPUT V1
↓
OPTIONAL HUMAN REVIEW
```

---

# 47. Second Major Opportunity: Make Remotion Fully Data-Driven

A lot of manual work came from using Claude Code to incrementally build the renderer.

That was appropriate once.

It should not repeat.

The renderer should consume structured data.

Example:

```json
{
  "durationSeconds": 52.44,
  "fps": 30,
  "shots": [
    {
      "sequence": 1,
      "type": "still",
      "asset": "/stills/shot_01.png",
      "startSeconds": 0.0,
      "endSeconds": 12.0,
      "motion": "slow_push",
      "transitionOut": "dissolve"
    },
    {
      "sequence": 2,
      "type": "video",
      "asset": "/video/shot_02.mp4",
      "startSeconds": 12.0,
      "endSeconds": 18.5,
      "motion": "none",
      "transitionOut": "dissolve"
    }
  ]
}
```

The same renderer can then handle future reels without code edits.

---

# 48. Manual Steps That Should Remain

Even in a highly automated system, some human gates remain valuable.

## 48.1 Story Approval

Before expensive generation:

```text
Approve narration
```

This prevents wasted TTS / image / video generation.

## 48.2 Visual Asset Approval

Optionally show:

```text
contact sheet of stills
contact sheet / preview of Veo seeds
```

before video generation.

## 48.3 Final V1 Review

The final reel should still be watchable before publishing.

These three gates provide most of the human value with very little friction.

---

# 49. Manual Steps That Should Disappear Completely

The mature system should eliminate:

```text
manually activating the environment
manually exporting project variables
manually running every Python stage
manually generating shots one by one
manually downloading GCS files
manually listing the bucket
manually mapping object IDs to shots
manually copying files into Remotion
manually opening Claude Code for every renderer feature
manually pasting implementation prompts
manually checking video transforms
manually running ffprobe in normal production
manually deciding which result JSON is authoritative
manually remembering filenames
manually applying the same fix to multiple shots
```

These are engineering overhead, not creative work.

---

# 50. Genuine Human-Creative Interventions

Some manual actions were valuable because they were editorial, not mechanical.

Examples:

## Selective Veo decision

The automated Visual Director was too conservative.

Human judgment improved the reel by selecting only:

```text
Shots 2, 5, 6
```

for Veo.

## Rejecting duplicate tombstone image

The image technically succeeded but visually failed.

## Evaluating Veo movement

Whether motion feels tasteful is not purely technical.

## Detecting rapid zoom issue

The user’s visual review caught something structural checks did not.

## Final emotional pacing

Whether the pause after:

```text
“Don’t try.”
```

feels right is partly aesthetic.

Future automation should support these decisions, not hide them.

---

# 51. Recommended Operating Modes

A mature system should support three modes.

## 51.1 Supervised Mode

Pause after:

```text
story
visual assets
V1 render
```

Best for quality-sensitive production.

## 51.2 Fast Mode

Pause only:

```text
before expensive Veo generation
before final approval
```

Best for routine content.

## 51.3 Autonomous Mode

Run everything:

```text
source
→ final V1
→ QA report
```

Best once the pipeline is mature and reliable.

---

# 52. Recommended Future Orchestrator Responsibilities

A future top-level orchestrator should own:

```text
configuration
dependency checks
asset discovery
story pipeline
voice generation
alignment
subtitle generation
visual planning
still generation
seed generation
Veo generation
automatic QA
retry policy
asset download
production manifest
Remotion asset synchronization
Remotion rendering
technical QA
artifact output
```

The user should not manually advance from stage to stage.

---

# 53. Recommended Production Asset Manifest

A robust future pipeline should create one authoritative file:

```text
metadata/production_assets.json
```

Example:

```json
{
  "audio": {
    "narration": "audio/narration.wav"
  },
  "shots": {
    "1": {
      "type": "still",
      "path": "generated/remotion_stills/shot_01_still.png",
      "approved": true
    },
    "2": {
      "type": "video",
      "path": "generated/veo/shot_02.mp4",
      "gcs_uri": "gs://...",
      "approved": true
    }
  }
}
```

Then all downstream stages use this file.

This removes ambiguity between:

- test assets,
- old generations,
- production assets.

---

# 54. Recommended Automatic QA Layers

## 54.1 Image QA

Check:

```text
no UI
no accidental text
correct number of subjects
correct style
correct aspect ratio
correct emotional meaning
```

## 54.2 Video QA

Check:

```text
no app UI
no extra people
no text artifacts
no sudden camera breathing
no broken first frame
no extreme style drift
```

## 54.3 Timeline QA

Check:

```text
all shots present
correct order
no gaps
correct total frames
correct narration
correct captions
```

## 54.4 Render QA

Check:

```text
output exists
correct resolution
correct fps
correct duration
audio track exists
no black frames
no missing media
```

---

# 55. Recommended Retry Logic

Future autonomous generation should not immediately ask the user after every defect.

Example:

```text
generate asset
↓
automatic QA
↓
pass?
├── yes → continue
└── no
     ↓
adjust prompt
     ↓
retry
     ↓
pass?
     ├── yes → continue
     └── no after N attempts → ask human
```

This removes many unnecessary intervention points.

---

# 56. Recommended Pipeline State Machine

A future pipeline could track stages like:

```text
INGESTED
ANALYZED
STORY_APPROVED
VOICE_READY
TIMING_READY
SUBTITLES_READY
VISUAL_PLAN_READY
IMAGES_READY
VIDEOS_READY
ASSETS_APPROVED
REMOTION_READY
V1_RENDERED
V1_APPROVED
FINAL
```

Each stage should have:

```text
status
inputs
outputs
validation
error
retry count
```

This allows agents to resume work rather than starting over.

---

# 57. Recommended Human Interaction Model

The ideal user experience should look like:

## Input

```text
Here are my source screenshots.
Make a reel.
```

## System

```text
Story generated.
Approve?
```

## User

```text
Approve.
```

## System

```text
Visual plan and production assets generated.
3 shots will use AI video.
Approve expensive generation?
```

## User

```text
Approve.
```

## System

```text
V1 rendered.
Here is the MP4 and QA report.
```

## User

```text
Looks good.
```

This is far better than requiring dozens of shell commands.

---

# 58. Final Automation Priority Order

The recommended automation roadmap is:

## Priority 1 — Eliminate orchestration commands

Create:

```text
pipeline.py
```

that runs existing stages automatically.

## Priority 2 — Create persistent production manifest

Stop relying on transient results files.

## Priority 3 — Make Remotion data-driven

No coding agent prompts per reel.

## Priority 4 — Add automatic visual QA

Images and videos should self-check.

## Priority 5 — Add automatic retry logic

Only escalate repeated failures.

## Priority 6 — Add final rendered-video QA

Catch visual regressions before user review.

## Priority 7 — Add autonomous mode

Run end-to-end with no pauses.

---

# 59. What the User Should Ideally Do in the Future

The target manual workflow should be reduced to:

```text
1. Provide source material.
2. Approve story.
3. Optionally approve visual plan / expensive Veo generation.
4. Review final V1.
```

Potentially, in fully autonomous mode:

```text
1. Provide source material.
2. Review final V1.
```

Everything else should be handled by the pipeline.

---

# 60. Final Conclusion

The current reel was successfully produced, but the user manually performed a large amount of orchestration work.

The biggest inefficiency was not generation itself.

It was the repeated loop of:

```text
run
inspect
copy
approve
open agent
paste prompt
render
debug
repeat
```

Most of that should be automated.

The strongest long-term architecture is:

> **AI for reasoning and generation, deterministic code for timing and rendering, automated QA for validation, and humans only for high-value creative approvals.**

The future user should not manage scripts, buckets, filenames, or renderer internals.

The future user should manage creative intent.
