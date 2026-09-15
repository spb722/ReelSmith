# Book Reels Troubleshooting & Agent Playbook

## 1. Purpose

This document is the **failure-history, troubleshooting guide, and future-agent reference** for the `book_reels` project.

It is intentionally different from the architecture document.

The architecture document explains **what the pipeline is**.

This document explains:

- what went wrong,
- how the issue appeared,
- what assumptions were incorrect,
- how the issue was diagnosed,
- what fixed it,
- what did **not** fix it,
- what future agents should check first,
- and what guardrails should be preserved so the same mistakes are not repeated.

The intended reader is a future coding agent such as Claude Code, Codex, or another autonomous engineering assistant working inside the repository.

The primary rule for future agents is:

> **Do not “improve” or redesign the pipeline before understanding which parts are already locked and which previous failure modes were already solved.**

The current project contains several stages that are intentionally deterministic. Do not replace those with free-form LLM behavior without a concrete reason.


---

# 2. Project Context for Future Agents

Project root:

```text
/Users/sachinpb/PycharmProjects/book_reels/
```

Primary Python environment used during development:

```text
kayak-video
Python 3.11
```

Google Cloud project:

```text
gen-lang-client-0240752803
```

Vertex location used successfully:

```text
global
```

Reusable GCS bucket:

```text
gs://sachin-kayaking-video-test/
```

Veo prefix:

```text
gs://sachin-kayaking-video-test/book_reels/veo/
```

Final reel target:

```text
1080x1920
9:16
30fps
52.44 seconds
1573 frames
```

Important operating principle:

> **Make one engineering change at a time, verify it, then continue.**

Do not make broad multi-stage changes unless explicitly requested.


---

# 3. Locked Artifacts — Do Not Reopen Casually

The following were deliberately locked during development.

## Narration

Final narration:

> After a lifetime of struggle and eventual success, Charles Bukowski left two words on his tombstone: 'Don't try.' He faced 30 years of rejection. His breakthrough at 50 seemed like an underdog tale. But success came from accepting who he was, not from wanting it more. This is 'The Backwards Law,' from Alan Watts. The harder you chase good feelings, the more they elude you. Straining for success makes you feel like a failure. Sometimes, the boldest moves only happen when we stop obsessing over the outcome. Accept discomfort, and it loses its power. What if you just let yourself feel it?

Do not rewrite unless the user explicitly asks.


## Narration audio

```text
audio/narration.wav
```

Authoritative duration:

```text
52.44 seconds
```


## Subtitle cues

```text
metadata/subtitle_cues.json
```

Properties:

```text
24 cues
word alignment ratio = 1.0
```

Do not:

- rewrite,
- merge,
- split,
- paraphrase,
- retime

without explicit user direction.


## Visual timing

The final eight-shot structure is:

| Shot | Time |
|---|---:|
| 1 | 0.0–12.0 |
| 2 | 12.0–18.5 |
| 3 | 18.5–24.5 |
| 4 | 24.5–33.8 |
| 5 | 33.8–38.4 |
| 6 | 38.4–45.0 |
| 7 | 45.0–49.6 |
| 8 | 49.6–52.44 |

These are the authoritative story-shot boundaries unless the user explicitly asks to revise them.


---

# 4. Issue Index

This project encountered the following major issues:

1. Environment instability risk around `base`
2. Over-conservative AI visual planning
3. Gemini subtitle JSON grouping unreliability
4. Wrong Veo input mode / wrong interpretation of `reference_images`
5. Feeding raw mobile screenshots directly into Veo
6. Shot 5 emotional prompt mismatch
7. Need to use `global` endpoint rather than guessing regions
8. Veo operation completed but script found no URI
9. Seed-results JSON overwrite caused wrong-source fallback
10. Shot 5 accidentally used the screenshot instead of the recomposed seed
11. GCS generation-results JSON overwrite lost previous clip metadata
12. Need to recover Shot 5 URI from bucket listing
13. AI-generated Shot 1 produced duplicate tombstones
14. Remotion setup ambiguity
15. Captions initially absent by design
16. Special “Don’t try.” needed duplicate-caption suppression
17. Veo 24fps vs Remotion 30fps was misdiagnosed as source of zoom/judder
18. Frame interpolation did not solve the zooming
19. Actual rapid zoom/breathing came from Remotion transform logic
20. Same Remotion transform issue affected Shots 5 and 6
21. Need to keep video-motion and still-motion code paths separate
22. Need to avoid proliferating `v2`, `v3`, `final2` scripts
23. Need visual QA gates after every generative stage

Each issue is documented below.


---

# 5. Issue: Do Not Destabilize the `base` Conda Environment

## Symptom / Context

An older project had already experienced `ffmpeg` / `libintl` problems in the Conda `base` environment.

The user had a known-working environment:

```text
kayak-video
```

## Risk

A future agent may see missing packages and reflexively modify or upgrade `base`.

That can break unrelated system tools and existing workflows.

## Correct Approach

Use the existing environment where possible:

```bash
conda activate kayak-video
```

Install only what is required there.

## Prevention Rule

> **Do not modify the Conda base environment unless the user explicitly asks.**

Do not perform broad package upgrades simply because a tool suggests them.


---

# 6. Issue: Do Not Reflexively Upgrade `google-genai`

## Context

The installed version during development was:

```text
google-genai 2.19.0
```

The pipeline already had working Vertex calls.

## Risk

A future agent may see newer docs and immediately run:

```bash
pip install --upgrade google-genai
```

This can change API signatures and introduce unrelated regressions.

## Correct Approach

If a signature fails:

1. inspect the exact traceback,
2. compare the installed SDK behavior,
3. adapt the canonical script first,
4. upgrade only if an actual incompatibility requires it.

## Example

If:

```python
generate_videos(..., image=image)
```

were rejected by the installed SDK, an alternate supported SDK shape considered during development was a source object such as:

```python
source=types.GenerateVideosSource(...)
```

The principle is:

> **Adapt deliberately from the actual error, not from assumption.**


---

# 7. Issue: Visual Director Was Too Conservative About AI Video

## Symptom

The generated visual plan recommended avoiding AI video entirely.

## Why This Was a Problem

Some moments had strong motion potential:

- writer-room atmosphere,
- mirror / discomfort,
- briefcase drop / release.

Using only stills would have made the reel flatter than desired.

## Fix

Human creative judgment overrode the conservative policy.

Veo was selectively assigned to:

```text
Shot 2
Shot 5
Shot 6
```

## What Was Preserved

AI video was **not** used everywhere.

## Agent Rule

> Treat visual-plan policies as recommendations, not absolute law, when the user has already approved a selective override.

Do not re-disable Veo globally.


---

# 8. Issue: Gemini Subtitle Grouping Was Unreliable

## Symptom

An attempt to have Gemini produce subtitle cue grouping directly as JSON was not reliable enough.

## Risk

LLM-created cue boundaries can:

- drift from exact speech timing,
- split important phrases,
- merge pauses incorrectly,
- produce malformed JSON,
- vary between runs.

## Fix

Subtitle segmentation was replaced by deterministic dynamic programming using:

- word timestamps,
- punctuation,
- pauses,
- protected phrases,
- semantic ending penalties,
- 2–6 word cue constraints.

## Final Result

```text
24 cues
alignment ratio = 1.0
```

## Agent Rule

> Do not replace the deterministic subtitle cue generator with a free-form LLM formatter.

The current subtitle JSON is locked.


---

# 9. Issue: Wrong Understanding of Veo `reference_images`

## Initial Assumption

The early Veo harness considered using `reference_images`.

## Why It Was Wrong

`reference_images` are better suited to preserving:

- a subject,
- person,
- character,
- product identity.

They are not the best mechanism when the goal is:

> “Animate this exact full illustration as the starting frame.”

## Correct Mode

Use **image-to-video** with the full image as the starting frame:

```python
image = types.Image.from_file(...)
client.models.generate_videos(
    model="veo-3.1-fast-generate-001",
    prompt=...,
    image=image,
    config=...
)
```

## Agent Rule

> For these story illustrations, use IMAGE_TO_VIDEO, not subject reference-image mode.


---

# 10. Issue: Raw Mobile Screenshot Was Fed Directly Into Veo

## Symptom

The first Shot 2 Veo clip technically succeeded, but the first seconds showed:

- app UI,
- screenshot framing,
- surrounding text/card layout,
- visual expansion from screenshot to illustration.

## Why

The Veo input was the **whole screenshot**, not a clean production frame.

## Failed Direction

Mechanical cropping was initially considered.

A crop script was created to extract the illustration.

## Better Fix

The approach was changed to **Gemini image recomposition**.

Pipeline:

```text
mobile screenshot
→ Gemini image edit/recomposition
→ clean 9:16 seed
→ visual QA
→ Veo
```

## Why This Was Better

It allowed:

- removal of app chrome,
- removal of text,
- natural 9:16 composition,
- scene extension,
- better motion space,
- cleaner visual identity.

## Agent Rule

> Never send the original mobile screenshots directly to Veo for production shots.

Use the cleaned seed image instead.


---

# 11. Issue: Shot 5 Prompt Had the Wrong Emotional Direction

## Symptom

An early Shot 5 prompt leaned toward:

- self-acceptance,
- release.

But the narration at that moment was about:

- strain,
- inadequacy,
- feeling like failure.

## Fix

The prompt was corrected to emphasize:

```text
pressure
self-judgment
discomfort
feeling inadequate
```

and explicitly avoid horror/supernatural interpretation.

## Agent Rule

Always align visual emotion with the exact narration section, not the broader message of the reel.


---

# 12. Issue: Endpoint / Region Guessing

## Context

The user's existing Vertex setup already worked with:

```text
location="global"
```

Some examples elsewhere referenced regional endpoints.

## Risk

A future agent may randomly switch between:

```text
global
us-central1
asia-south1
```

without an error requiring it.

## Correct Approach

Default to:

```text
global
```

Use a regional override only when the actual API error indicates model availability or endpoint constraints.

## Agent Rule

> Do not randomly change Vertex location to “try things.” Diagnose first.


---

# 13. Issue: Veo Operation Completed but No Video URI Was Found

## Symptom

The generation operation completed, but the script raised:

```text
RuntimeError: No generated video URI returned
```

## Initial Ambiguity

Possible causes included:

- inline video bytes instead of a URI,
- RAI filtering,
- SDK response-shape differences,
- operation error,
- extraction bug.

## Fix

The canonical `generate_veo_clips.py` was improved to:

1. inspect both:

```text
operation.response
operation.result
```

2. support:

```text
video.uri
video.video_bytes
```

3. save the completed raw operation:

```text
metadata/veo_last_operation.json
```

4. expose:

```text
rai_media_filtered_count
rai_media_filtered_reasons
operation error
```

## Result

A rerun succeeded and returned the GCS URI.

## Agent Rule

> If Veo “finishes” but no URI is found, do not immediately regenerate or upgrade libraries.

First inspect the completed operation object.


---

# 14. Issue: Seed Results JSON Was Overwritten Per Shot

## Symptom

`prepare_veo_seed_images.py --shot X` wrote a results JSON containing only the latest generated shot.

After generating Shot 6, the file no longer contained metadata for Shot 5.

## Consequence

The Veo generation script looked at the results JSON and did not find Shot 5.

It then fell back to:

```text
ORIGINAL_SOURCE_IMAGE
```

which meant Shot 5 used the raw screenshot again.

## Root Cause

The script treated a mutable “last run results file” as if it were a complete persistent asset registry.

## Fix

The canonical Veo script was changed so the deterministic file on disk is authoritative:

```text
generated/veo_seeds/shot_02_seed.png
generated/veo_seeds/shot_05_seed.png
generated/veo_seeds/shot_06_seed.png
```

Logic became:

```text
if deterministic seed file exists:
    use it
elif seed metadata exists:
    use metadata path
else:
    fall back to original source
```

## Agent Rule

> For generated production assets, prefer deterministic file existence over transient “last run” JSON.

## Stronger Future Improvement

A future refactor may merge results by shot ID instead of overwriting them, but do not break the current deterministic-path safeguard.


---

# 15. Issue: Shot 5 Video Accidentally Used Screenshot Input

## Symptom

The user observed that Shot 5 video was still based on the screenshot instead of the generated clean seed.

## Diagnosis

This directly traced back to the seed-results overwrite bug above.

## Fix

The script was changed to prioritize:

```text
generated/veo_seeds/shot_05_seed.png
```

and Shot 5 was regenerated.

## Agent Rule

Before any Veo generation, verify the manifest contains:

```text
input_image_source: AI_RECOMPOSED_SEED
```

and the correct deterministic path.

Never rely solely on visual intuition after generation.


---

# 16. Issue: Veo Generation Results JSON Was Also Overwritten

## Symptom

After generating Shot 5 and then Shot 6, the uploaded:

```text
metadata/veo_generation_results.json
```

contained only Shot 6.

## Consequence

Shot 5's GCS URI was no longer available in that JSON.

## Recovery

The bucket was listed:

```bash
gcloud storage ls --long --recursive \
  gs://sachin-kayaking-video-test/book_reels/veo/
```

The recent objects were identified by timestamp.

The production URIs were then mapped as:

```text
Shot 2
gs://sachin-kayaking-video-test/book_reels/veo/6102034147660935021/sample_0.mp4

Shot 5
gs://sachin-kayaking-video-test/book_reels/veo/8649026797495028929/sample_0.mp4

Shot 6
gs://sachin-kayaking-video-test/book_reels/veo/3451503193750344566/sample_0.mp4
```

## Important Qualification

Shot 6 was explicitly confirmed by metadata.

Shot 5 was identified from generation order and bucket timestamps because the results file had been overwritten.

## Agent Rule

Do not treat a per-run results JSON as a complete historical database.

Prefer:

- merging keyed by `shot_sequence`, or
- preserving per-shot result records.

Do not delete old bucket objects unless explicitly asked.


---

# 17. Issue: AI-Generated Shot 1 Had Two Tombstones

## Symptom

The first clean still for Shot 1 generated:

- one intended tombstone,
- one unwanted duplicate tombstone.

## Why It Failed QA

The image no longer had a single strong visual focal point.

## Fix

The prompt was strengthened with explicit constraints:

```text
EXACTLY ONE tombstone
no duplicate tombstone
no second grave marker
no reflection
no background duplicate
no repeated stone shape
```

The regenerated still was approved.

## Agent Rule

> “Generation succeeded” is not equivalent to “asset approved.”

Every generated asset requires visual QA.


---

# 18. Issue: Remotion Setup Could Have Added Unnecessary Complexity

## Installation Choices

The project deliberately used:

```text
Template: Blank
TailwindCSS: No
```

Useful skills installed:

```text
remotion-best-practices
remotion-captions
remotion-multimedia
remotion-render
remotion-studio
```

## Why

The project already had:

- its own timing,
- its own asset pipeline,
- its own captions,
- its own story structure.

A SaaS, prompt-to-video, or other complex template would add unnecessary code.

## Agent Rule

> Preserve the minimal Remotion project. Do not migrate it to a more complex template.


---

# 19. Issue: Captions Were “Missing” After First Remotion Pass

## Symptom

The user opened the reel and saw no captions.

## Cause

This was **not a bug**.

The first Remotion coding prompt intentionally said:

```text
do NOT add subtitles yet
```

The goal of that phase was only to validate:

- timeline,
- shot order,
- video playback,
- narration,
- total duration.

## Fix

Subtitles were implemented in the next controlled step.

## Agent Rule

Before diagnosing a missing feature, check whether that feature was intentionally excluded from the current milestone.


---

# 20. Issue: “Don’t Try.” Could Have Appeared Twice

## Context

The subtitle cue itself contains:

```text
"Don't try."
```

A separate large impact typography treatment was later added.

## Risk

Without special handling, the screen would show:

- normal caption,
- large impact phrase

at the same time.

## Fix

The normal subtitle presentation was suppressed **only for that cue** while the special impact typography rendered.

The JSON cue itself was not deleted or modified.

## Agent Rule

> Preserve authoritative cue data; suppress presentation conditionally rather than mutating the source metadata.


---

# 21. Issue: Still Motion and Video Motion Became Entangled

## Context

Still shots intentionally received slow motion:

- push,
- drift,
- scale.

Video shots from Veo were supposed to remain natural.

## Failure Mode

Shared transform / transition logic affected video shots.

This created rapid:

- zoom-in,
- zoom-out,
- breathing,
- pulsing

inside the final Remotion render.

## Important Observation

The raw Veo MP4 played correctly outside Remotion.

Therefore:

> the source video itself was not broken.

## Agent Rule

Still-image motion and video playback must be separate code paths.


---

# 22. Issue: 24fps → 30fps Was Initially Suspected

## Symptom

Shot 2 showed visible shuttering / rapid zoom-like motion around:

```text
00:13–00:15
```

## Observation

Veo output was:

```text
24fps
```

Remotion composition was:

```text
30fps
```

## Initial Hypothesis

The mismatch might be causing judder.

## Diagnostic Command

```bash
ffprobe -v error \
  -select_streams v:0 \
  -show_entries stream=r_frame_rate,avg_frame_rate \
  -of default=noprint_wrappers=1 \
  generated/veo/shot_02.mp4
```

Result:

```text
r_frame_rate=24/1
avg_frame_rate=24/1
```

## Experiment

A 30fps interpolated version was created with FFmpeg `minterpolate`.

## Result

The problem still existed.

## Conclusion

The 24fps → 30fps mismatch was **not the root cause** of the fast zooming.

## Agent Rule

> Do not convert Veo clips to 30fps merely because the Remotion composition is 30fps.

Use the original Veo file unless there is a separately proven playback reason to convert it.


---

# 23. Issue: Motion Interpolation Could Introduce Its Own Artifacts

## Experiment

The Shot 2 file was converted with motion interpolation.

## Risk

Optical-flow / motion-compensated interpolation can invent motion and produce:

- warping,
- breathing,
- temporal distortion.

## Outcome

It did not solve the actual problem and therefore was abandoned.

## Agent Rule

Avoid `minterpolate` as a default preprocessing step for Veo assets.

Do not apply it to Shots 5 or 6 unless a future problem independently proves it is needed.


---

# 24. Actual Root Cause of Shot 2 Rapid Zoom / Breathing

## Key Evidence

The user confirmed:

```text
generated/veo/shot_02.mp4
```

did **not** have the problem when played directly.

The issue appeared only inside the rendered Remotion reel.

## Diagnosis

The zoom was being introduced by Remotion-side:

- transform logic,
- transition logic,
- shared wrappers,
- or animation inherited by video shots.

## Fix Requirements

Shot 2 had to render as a plain video layer:

```text
transform: none
scale: 1
translateX: 0
translateY: 0
```

No:

```text
spring transform
interpolate transform
Ken Burns
zoom
perspective
animated filter
```

Transitions touching the shot could animate **opacity only**.

## Outcome

The user confirmed:

> the issue was fixed in Shot 2.

## Important Limitation

The exact final local code diff that fixed Shot 2 was performed by the coding agent and was not pasted into the conversation.

Therefore a future agent should inspect the current Remotion code to see the exact implementation rather than inventing it.

## Agent Rule

> Use the current working Shot 2 implementation as the reference implementation for stable video playback.


---

# 25. Same Rapid Zoom / Breathing Issue in Shots 5 and 6

## Symptom

After Shot 2 was fixed, the same issue remained in:

```text
Shot 5
Shot 6
```

## Diagnosis

Same class of Remotion-side video transform bug.

## Fix Strategy

Apply the same stable video rendering behavior used for Shot 2:

```text
transform: none
scale: 1
translateX: 0
translateY: 0
```

Check transitions:

```text
Shot 4 → 5
Shot 5 → 6
Shot 6 → 7
```

Transitions may affect opacity only.

## Agent Rule

All Veo shots should use the same stable video rendering pattern unless a deliberate video transform is explicitly approved.


---

# 26. Preventing the Video Transform Bug Permanently

Future agents should enforce an architectural split.

## Good Pattern

```tsx
if (shot.type === 'still') {
  return <AnimatedStill ... />;
}

if (shot.type === 'video') {
  return <StableVideo ... />;
}
```

## Bad Pattern

A single wrapper that applies:

```text
scale
translate
spring
interpolate
```

to every visual type.

## Recommended Invariant

For any video shot:

```text
computed transform = none
```

during the full active shot range.

Transitions should be applied to a separate opacity wrapper if needed.


---

# 27. Issue: Special Typography Timing Must Respect the Audio Pause

## Important Timing

Speech timing:

```text
"Don't" 9.96–10.60
"try"   10.72–11.40
next sentence starts around 12.28
```

## Risk

A future agent may make the impact text disappear immediately after 11.40.

That would destroy the intended pause.

## Correct Behavior

The impact treatment should:

- land with the phrase,
- remain visible long enough to register,
- respect the pause,
- leave cleanly before the next thought.

## Agent Rule

Do not optimize away intentional silence.


---

# 28. Issue: Shot 1 Should Not Bake “Don’t Try.” Into the Generated Image

## Why

The image-generation model was explicitly asked to create the tombstone **without text**.

The phrase is rendered later in Remotion.

## Benefits

- exact timing control,
- typography control,
- no AI text artifacts,
- easy revision,
- no duplicated phrase.

## Agent Rule

Do not regenerate Shot 1 with text baked into the tombstone unless explicitly requested.


---

# 29. Issue: Generated Image Style Drift

## Risk

Gemini image recomposition can:

- change object count,
- alter composition,
- invent text,
- drift toward photorealism,
- change emotional meaning.

## Existing Countermeasures

Prompts specify:

```text
editorial illustration
not photorealistic
no visible text
no logos
no watermarks
no extra characters
```

## Agent Rule

Every generated image must be checked for:

1. correct subject count,
2. no UI,
3. no accidental text,
4. correct emotional meaning,
5. correct 9:16 composition,
6. no unwanted photorealistic drift.


---

# 30. Issue: Generated Video Hallucination Risk

## Risk Areas

Veo can invent:

- extra people,
- new objects,
- text,
- unrealistic movement,
- camera motion,
- style drift.

## Prompt Strategy

Use:

- exact starting frame,
- preserve editorial style,
- restrained motion,
- negative prompt,
- no text,
- no extra characters,
- no photorealism.

## Agent Rule

Use Veo only where motion is worth the risk.

Do not expand Veo to all shots automatically.


---

# 31. Issue: GCS “Folders” Are Virtual

## Context

The chosen prefix was:

```text
gs://sachin-kayaking-video-test/book_reels/veo/
```

## Common Mistake

Trying to manually create the folder first.

## Correct Understanding

GCS object prefixes are virtual.

Writing an object to the prefix is enough.

## Agent Rule

Do not add unnecessary bucket-directory creation steps.


---

# 32. Issue: Production Clip Selection Must Exclude Old Tests

The GCS bucket contains an old Shot 2 test:

```text
gs://sachin-kayaking-video-test/book_reels/veo/7526757047499920967/sample_0.mp4
```

This was based on the raw screenshot and is not the production clip.

The production Shot 2 is:

```text
gs://sachin-kayaking-video-test/book_reels/veo/6102034147660935021/sample_0.mp4
```

## Agent Rule

Do not automatically choose the earliest or largest object in the bucket.

Use the approved production mapping.


---

# 33. Approved Production Veo Mapping

Current approved mapping:

```text
Shot 2
gs://sachin-kayaking-video-test/book_reels/veo/6102034147660935021/sample_0.mp4

Shot 5
gs://sachin-kayaking-video-test/book_reels/veo/8649026797495028929/sample_0.mp4

Shot 6
gs://sachin-kayaking-video-test/book_reels/veo/3451503193750344566/sample_0.mp4
```

Local production filenames:

```text
generated/veo/shot_02.mp4
generated/veo/shot_05.mp4
generated/veo/shot_06.mp4
```

Remotion copies:

```text
remotion/public/video/shot_02.mp4
remotion/public/video/shot_05.mp4
remotion/public/video/shot_06.mp4
```


---

# 34. Issue: Do Not Overwrite Canonical Scripts With Version Proliferation

## User Preference

The project explicitly avoided:

```text
generate_veo_clips_v2.py
generate_veo_clips_v3.py
generate_veo_clips_final.py
generate_veo_clips_final2.py
```

## Correct Pattern

Update:

```text
generate_veo_clips.py
```

in place.

Same for:

```text
prepare_veo_seed_images.py
prepare_remotion_stills.py
```

## Agent Rule

> Maintain one canonical implementation per pipeline stage.

If a backup is necessary, rely on Git rather than suffix proliferation.


---

# 35. Issue: Broad Changes Make Diagnosis Harder

This project was most successful when changes were isolated.

Examples:

```text
First prove timeline.
Then add captions.
Then add still motion.
Then add impact typography.
Then add transitions.
Then render.
Then debug only the broken shots.
```

## Why This Matters

If captions, transitions, timing, video transforms, and rendering were changed together, it would have been much harder to identify the Shot 2 bug.

## Agent Rule

For future iterations:

> One subsystem per change.

After each change:

```text
run lint/typecheck
preview
render if needed
visually verify
```

Then proceed.


---

# 36. Issue: Visual QA Cannot Be Replaced by Metadata Validation

Structured metadata confirmed many operations technically succeeded.

But several visually bad assets still passed technical checks:

- raw screenshot Veo clip,
- duplicate tombstone still,
- wrong-source Shot 5,
- Remotion zooming videos.

## Agent Rule

There are two separate gates:

```text
Technical success
AND
Visual approval
```

Both are required.


---

# 37. Recommended Future-Agent Diagnostic Order

When something looks wrong in the final reel, diagnose in this order.

## 1. Check the raw asset first

For a video:

```bash
open generated/veo/shot_XX.mp4
```

For a still:

```text
generated/remotion_stills/shot_XX_still.png
```

Question:

> Is the defect already present before Remotion?


## 2. Check the Remotion public copy

Verify the correct asset was copied:

```text
remotion/public/video/
remotion/public/stills/
```


## 3. Check timeline mapping

Inspect:

```text
src/timeline.ts
```


## 4. Check per-shot component

Determine whether the shot uses:

```text
AnimatedStill
StableVideo
```

or equivalent.


## 5. Check parent transforms

Search for:

```text
transform
scale
translate
spring
interpolate
```


## 6. Check transition wrappers

Confirm transitions are not scaling video shots.


## 7. Only then consider codec/FPS issues

Do not begin with frame-rate conversion if the raw asset is clean.


---

# 38. Recommended Future-Agent Search Commands

Useful searches inside the Remotion project:

```bash
grep -R "transform" src
grep -R "scale" src
grep -R "spring" src
grep -R "interpolate" src
grep -R "shot_02" src
grep -R "shot_05" src
grep -R "shot_06" src
```

For media inspection:

```bash
ffprobe -v error \
  -select_streams v:0 \
  -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,duration \
  -of default=noprint_wrappers=1 \
  path/to/video.mp4
```

For GCS recovery:

```bash
gcloud storage ls --long --recursive \
  gs://sachin-kayaking-video-test/book_reels/veo/
```


---

# 39. Recommended Agent Preflight Before Editing Anything

Before making changes, a future agent should inspect:

```text
README / architecture docs
metadata/subtitle_cues.json
metadata/visual_plan.json
generated asset directories
remotion/src/timeline.ts
remotion/src/BookReel.tsx
remotion/src/components/
remotion/public/
package.json
```

Then answer internally:

1. What is locked?
2. What is currently broken?
3. Is the source asset correct?
4. Is this a Python-stage issue or a Remotion-stage issue?
5. Can the fix be isolated?


---

# 40. Canonical Troubleshooting Matrix

| Symptom | First Check | Likely Layer | Known Fix |
|---|---|---|---|
| Screenshot UI visible in Veo | Inspect Veo input image | Asset prep | Use Gemini-recomposed seed |
| Veo clip starts wrong | Check `input_image_source` | Veo manifest | Require `AI_RECOMPOSED_SEED` |
| No Veo URI returned | Inspect operation JSON | Veo result parsing | Check URI, bytes, RAI, error |
| Earlier seed metadata disappeared | Inspect results JSON | Metadata persistence | Use deterministic seed path |
| Earlier Veo clip metadata disappeared | Inspect bucket listing | Metadata persistence | Recover from GCS; merge in future |
| Generated still has duplicate object | Inspect image | Image generation | Tighten prompt and regenerate |
| Captions missing | Check milestone/spec | Remotion feature stage | Add subtitle layer |
| “Don’t try.” appears twice | Check subtitle + impact layers | Remotion presentation | Suppress normal cue presentation |
| Veo video zooms in final render | Play raw MP4 | Remotion transforms | Remove video transforms |
| 24fps video in 30fps timeline | Play raw + rendered | Usually not root cause | Do not interpolate by default |
| Black edges on still motion | Inspect scale/translation | Remotion still motion | Maintain cover throughout |
| Video source looks clean but render does not | Compare raw/public/render | Remotion | Inspect wrappers/transitions |
| Wrong clip selected from bucket | Check timestamp + metadata | Asset selection | Use approved production mapping |


---

# 41. “Do Not Do This” List

Future agents should **not**:

- rewrite locked narration without explicit approval,
- regenerate voice casually,
- retime locked captions,
- replace deterministic caption grouping with an LLM,
- feed raw screenshots directly to Veo,
- use `reference_images` for whole-scene preservation,
- use Veo on every shot,
- upgrade `google-genai` reflexively,
- modify Conda `base`,
- randomly switch Vertex regions,
- convert all Veo clips to 30fps by default,
- apply Ken Burns transforms to video shots,
- bake “Don’t try.” into the Shot 1 image,
- trust “generation succeeded” as visual approval,
- create many suffixed script versions,
- make broad multi-stage changes before verifying the previous stage.


---

# 42. “Do This Instead” List

Future agents should:

- preserve canonical scripts,
- inspect current working code before editing,
- use deterministic asset paths,
- use image recomposition before Veo,
- verify `AI_RECOMPOSED_SEED` before video generation,
- visually inspect every generated asset,
- keep still motion and video rendering separate,
- keep video transforms at `none`,
- use opacity-only video transitions,
- preserve exact narration timing,
- preserve exact subtitle cues,
- run lint/typecheck after Remotion changes,
- render targeted test versions after visual changes,
- compare raw asset vs Remotion output before blaming the generator.


---

# 43. Suggested Future Improvements

These were not fully implemented during the original build but would make the pipeline more robust.

## 43.1 Merge result JSON instead of overwrite

For:

```text
veo_seed_results.json
veo_generation_results.json
remotion_still_results.json
```

Prefer keyed merge by:

```text
shot_sequence
```

rather than replacing the entire file per run.


## 43.2 Add validation that forbids screenshot fallback

For production Veo shots:

```text
2, 5, 6
```

the script could hard-fail if the recomposed seed does not exist.

Example policy:

```text
if shot in REQUIRED_RECOMPOSED_SEED_SHOTS and no seed:
    raise error
```

This is safer than silently falling back.


## 43.3 Add an asset lock manifest

Example:

```text
metadata/production_assets.json
```

containing final approved local paths and GCS URIs.

This would remove ambiguity between tests and production assets.


## 43.4 Add Remotion invariant checks

A test could assert:

```text
video shots do not receive transform animation
composition = 1573 frames
all 8 shots cover the full timeline
no gaps
no overlaps beyond deliberate opacity transitions
```


## 43.5 Add visual regression frames

Render representative frames at:

```text
0s
10.5s
13s
20s
29s
35s
41s
47s
51s
```

for quick QA after code changes.


---

# 44. Agent Handoff Checklist

A future agent taking over this repository should confirm all of the following before changing code:

```text
[ ] I have read this troubleshooting document.
[ ] I understand narration is locked.
[ ] I understand subtitle cues are locked.
[ ] I understand the reel is 52.44s / 1573 frames.
[ ] I know Shots 2, 5, and 6 are Veo video shots.
[ ] I know Shots 1, 3, 4, 7, and 8 are generated stills.
[ ] I know raw screenshots must not be used as production Veo inputs.
[ ] I know video shots must not inherit still-image transforms.
[ ] I know the raw Veo clips are 24fps and that this alone was not the zoom bug.
[ ] I know motion interpolation was tested and did not solve the issue.
[ ] I know the rapid zoom issue was Remotion-side.
[ ] I will inspect current Shot 2 implementation before changing video rendering.
[ ] I will make one scoped change at a time.
[ ] I will run lint/typecheck after Remotion edits.
[ ] I will visually verify the affected segment after changes.
```


---

# 45. Final Troubleshooting Philosophy

The most important lesson from this project is:

> **Debug the pipeline by layer, not by intuition.**

For any visual defect:

```text
source
→ cleaned asset
→ generated asset
→ copied production asset
→ timeline mapping
→ rendering component
→ transition wrapper
→ final render
```

Find the first layer where the problem appears.

Do not jump directly to regeneration.

Do not assume AI generation is the problem when the raw generated asset is clean.

Do not assume frame rate is the problem when a transform wrapper can explain the symptom.

And do not replace working deterministic stages with “smarter” generative behavior unless there is a clear, testable benefit.

The pipeline became reliable because each problem was isolated, verified, and fixed at the correct layer.
