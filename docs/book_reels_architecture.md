# Book Reels Pipeline Architecture

## 1. Purpose

This document describes the full end-to-end architecture and production process used to build the **“The Backwards Law: Don’t Try”** short-form vertical reel.

It captures both:

- the **final pipeline architecture**, and
- the **actual sequence of steps, experiments, failures, corrections, and design decisions** that were made while building the reel.

The goal of the system was not to create a simple slideshow from screenshots. The goal was to transform source screenshots into a polished, cinematic, narrative-driven reel using structured metadata, AI-assisted story development, generated voice, precise subtitle timing, selective AI video generation, AI image recomposition, and a deterministic Remotion rendering pipeline.

The final output target is:

- **1080 × 1920**
- **9:16 vertical**
- **30 fps**
- **52.44 seconds**
- narration-led storytelling
- selective Veo motion
- clean recomposed still images
- subtitles
- impact typography
- restrained transitions
- deterministic final rendering in Remotion


---

# 2. High-Level Architecture

The final architecture evolved into the following pipeline:

```text
Source Screenshots
      ↓
Asset Ingestion
      ↓
Pass 1: Image Understanding
      ↓
Semantic Asset Manifest
      ↓
Pass 2: Story Director
      ↓
Automated Story Quality Loop
      ↓
Locked Story Plan
      ↓
Voice Generation
      ↓
Word-Level Speech Timing
      ↓
Subtitle Cue Generation
      ↓
Locked Subtitle Cues
      ↓
Visual Plan
      ↓
Visual Asset Preparation
      ├── Gemini Image Recomposition for stills
      └── Gemini Image Recomposition → Veo for video shots
      ↓
Final Asset Library
      ↓
Remotion Timeline Assembly
      ↓
Subtitles
      ↓
Still Motion
      ↓
Impact Typography
      ↓
Transitions / Polish
      ↓
Render
      ↓
Visual QA
      ↓
Targeted Fixes
      ↓
Final MP4
```

A key architectural principle throughout the project was:

> **AI is used for understanding, generation, and creative transformation; deterministic code is used for timing, validation, composition, and final rendering.**


---

# 3. Source Material

The original content consisted of six vertical screenshots covering the idea of **The Backwards Law**, Charles Bukowski, Alan Watts, rejection, acceptance, discomfort, and the phrase:

> “Don’t try.”

The screenshots were not intended to be shown directly as the final reel. They served as:

- semantic source material,
- visual references,
- illustration references,
- factual/story references,
- and inputs for later AI recomposition.

The source images were:

```text
source_images/IMG_8802.PNG
source_images/IMG_8803.PNG
source_images/IMG_8804.PNG
source_images/IMG_8805.PNG
source_images/IMG_8806.PNG
source_images/IMG_8807.PNG
```

The mapped asset IDs were:

| Asset ID | Source | Meaning |
|---|---|---|
| `img_dcfce1645810` | `IMG_8802.PNG` | Intro / orange mascot |
| `img_badc7b4efc85` | `IMG_8803.PNG` | Bukowski writer / rejection |
| `img_fe38b500f8d1` | `IMG_8804.PNG` | Tombstone / “Don’t try.” |
| `img_6f6dabaab3eb` | `IMG_8805.PNG` | Cliff / briefcase / letting go |
| `img_ef0dc599dff0` | `IMG_8806.PNG` | Mirror / self-judgment |
| `img_37c610ebb57b` | `IMG_8807.PNG` | Backwards Law summary |

The orange mascot asset was ultimately considered too playful for the mature tone and was not used in the final visual direction.


---

# 4. Asset Ingestion

The first deterministic engineering stage was asset ingestion.

Script:

```text
ingest_assets.py
```

Responsibilities:

- recursively discover input image files,
- generate stable SHA-256-based IDs,
- normalize asset metadata,
- and write a deterministic asset registry.

Output:

```text
metadata/assets.json
```

This gave every later stage a stable reference to an asset rather than relying on filenames manually.


---

# 5. Pass 1 — Image Understanding

Each screenshot was analyzed semantically.

The goal was to separate:

- what text existed in the screenshot,
- what the illustration represented,
- what emotional role the image could serve,
- what parts were useful,
- and what parts should not be carried into the final reel.

The resulting analyzed asset metadata became the semantic foundation for the story and visual planning stages.

Important interpretation examples:

### Bukowski writer image
Useful for:

- rejection,
- persistence,
- long struggle,
- writer’s room,
- typewriter,
- desk,
- worn-down atmosphere.

### Tombstone image
Useful for:

- the opening hook,
- the phrase “Don’t try.”,
- reveal,
- callback at the ending.

### Mirror image
Useful for:

- inadequacy,
- self-judgment,
- striving,
- feeling like failure.

### Cliff / briefcase image
Useful for:

- letting go,
- release,
- non-attachment,
- bold action without obsession.

### Backwards Law summary image
Useful for:

- philosophical explanation,
- title / concept introduction,
- central idea.

This stage converted raw screenshots into reusable semantic assets.


---

# 6. Story Director

The next stage was story construction.

The reel was designed around a narration-first structure rather than a screenshot-first structure.

The final story title was:

```text
The Backwards Law: Don't Try
```

The story passed through an automated quality loop and was eventually locked.

The final narration was:

> After a lifetime of struggle and eventual success, Charles Bukowski left two words on his tombstone: 'Don't try.' He faced 30 years of rejection. His breakthrough at 50 seemed like an underdog tale. But success came from accepting who he was, not from wanting it more. This is 'The Backwards Law,' from Alan Watts. The harder you chase good feelings, the more they elude you. Straining for success makes you feel like a failure. Sometimes, the boldest moves only happen when we stop obsessing over the outcome. Accept discomfort, and it loses its power. What if you just let yourself feel it?

The approved story plan scored highly across:

- fidelity,
- internal consistency,
- hook strength,
- clarity,
- pacing,
- narrative coherence,
- ending quality.

Once approved, the narration was treated as **locked**.


---

# 7. Voice Generation

The narration was generated with Gemini TTS.

Script:

```text
generate_voice.py
```

Model:

```text
gemini-3.1-flash-tts-preview
```

Voice:

```text
Gacrux
```

Language:

```text
en-US
```

Output:

```text
audio/narration.wav
```

Audio format:

- 24 kHz
- mono
- 16-bit PCM WAV

Final duration:

```text
52.44 seconds
```

This duration became the **authoritative timing source** for the reel.

The effective narration speed was approximately:

```text
116.7 WPM
```

The voice style target was:

- mature,
- calm,
- confident,
- slightly intense,
- deliberate,
- not overly soothing,
- not theatrical,
- not “trailer voice”.

A particularly important pause was preserved after:

```text
"Don't try."
```


---

# 8. Word-Level Timing

Once voice generation was complete, the narration needed exact word-level timing.

Script:

```text
extract_word_timing.py
```

Speech-to-text model:

```text
Google Speech-to-Text V2
Chirp 3
```

Region:

```text
us
```

The transcription aligned perfectly:

```text
102 / 102 words
exact_match_ratio = 1.0
```

No substitutions, missing words, or extras were present.

Important timings included:

```text
"Don't"  9.96s – 10.60s
"try"    10.72s – 11.40s
"He"     begins around 12.28s
```

This verified that the desired pause after “Don’t try.” actually existed in the generated audio.


---

# 9. Subtitle Cue Generation

An early attempt to have Gemini group subtitles directly into JSON was unreliable.

The pipeline was changed to a deterministic subtitle segmentation approach using:

- word timestamps,
- punctuation,
- pause lengths,
- protected phrases,
- semantic ending penalties,
- minimum / maximum words per cue.

The final subtitle segmentation used dynamic programming rather than relying on free-form LLM grouping.

Final subtitle file:

```text
metadata/subtitle_cues.json
```

Final result:

```text
24 subtitle cues
alignment ratio = 1.0
```

Important cues included:

```text
cue_005
9.92 – 11.65
"Don't try."
style: IMPACT
```

Other important cue groups:

```text
"He faced 30 years of rejection."

"His breakthrough at 50"

"This is 'The Backwards Law,'"

"from Alan Watts."

"The harder you chase good feelings,"

"Sometimes, the boldest moves"

"only happen when we stop"

"obsessing over the outcome."

"Accept discomfort,"

"What if you just"

"let yourself feel it?"
```

The subtitle file was then treated as **locked and authoritative**.


---

# 10. Visual Planning

A Visual Director stage created:

```text
metadata/visual_plan.json
```

The visual plan divided the reel into eight shots.

Final shot structure:

| Shot | Time | Narrative Role | Main Visual |
|---|---:|---|---|
| 1 | 0.0–12.0 | Hook | Tombstone |
| 2 | 12.0–18.5 | Setup | Writer / rejection |
| 3 | 18.5–24.5 | Pivot | Tombstone / acceptance |
| 4 | 24.5–33.8 | Explanation | Backwards Law |
| 5 | 33.8–38.4 | Story | Mirror / failure |
| 6 | 38.4–45.0 | Principle | Cliff / briefcase |
| 7 | 45.0–49.6 | Principle | Ocean / release |
| 8 | 49.6–52.44 | Reflection | Tombstone callback |

The original Visual Director was conservative and recommended no AI video.

That recommendation was manually overridden for selected scenes where motion had real storytelling value.


---

# 11. Selective Veo Strategy

Veo was intentionally **not used for every shot**.

The final decision was to use Veo only for:

- Shot 2 — writer / rejection
- Shot 5 — mirror / failure
- Shot 6 — cliff / briefcase drop

Why selective use?

Because overusing generated video would:

- increase cost,
- increase visual inconsistency,
- increase hallucination risk,
- reduce editorial control,
- make the reel feel synthetic.

The remaining shots were designed as controlled still-image compositions animated inside Remotion.


---

# 12. Initial Veo Attempt and First Major Learning

The first Veo generation used the original screenshot directly.

Model:

```text
veo-3.1-fast-generate-001
```

Endpoint:

```text
global
```

Output:

```text
9:16
720p
8 seconds
MP4
24 fps
```

The first Shot 2 generation technically worked.

However, visual inspection revealed a major issue:

> The original mobile screenshot still contained app UI, text, card chrome, and surrounding interface.

Veo spent the beginning of the video visually transforming / expanding the illustration out of the screenshot.

This made the clip unsuitable as a clean production shot.

This was the turning point that led to the image-recomposition stage.


---

# 13. Gemini Image Recomposition Stage

Instead of mechanically cropping screenshots, the pipeline was changed to:

```text
Original screenshot
      ↓
Gemini image editing / recomposition
      ↓
Clean 9:16 image
      ↓
Veo
```

Model:

```text
gemini-3.1-flash-image
```

This model was used as an image editor rather than only as an image-understanding model.

The goals were:

- remove app UI,
- remove status bars,
- remove buttons,
- remove progress indicators,
- remove original card layout,
- remove text,
- preserve visual meaning,
- preserve editorial illustration style,
- recompose scenes naturally into full 9:16 frames,
- leave room for later captions and typography.

Script:

```text
prepare_veo_seed_images.py
```

The first recomposed Shot 2 seed was approved.

Output:

```text
generated/veo_seeds/shot_02_seed.png
```

This became the new canonical Veo seed.


---

# 14. Veo Seed Generation for Shots 2, 5, and 6

The same recomposition approach was applied to all three Veo-target shots.

Outputs:

```text
generated/veo_seeds/shot_02_seed.png
generated/veo_seeds/shot_05_seed.png
generated/veo_seeds/shot_06_seed.png
```

The Shot 5 prompt emphasized:

- mirror,
- bathroom,
- hourglass,
- mug,
- pressure,
- self-judgment,
- discomfort,
- inadequacy.

The Shot 6 prompt emphasized:

- cliff,
- figure,
- briefcase,
- ocean,
- sunset,
- birds,
- calm release,
- letting go.

These generated images became the canonical inputs to Veo.


---

# 15. Veo Generation Harness

The canonical Veo generation script became:

```text
generate_veo_clips.py
```

Key design principles:

- one script,
- no unnecessary v2/v3 proliferation,
- `--plan-only` mode,
- `--shot` mode,
- image-to-video mode,
- explicit model,
- explicit project / location,
- GCS output,
- metadata logging,
- failure diagnostics.

Model:

```text
veo-3.1-fast-generate-001
```

Vertex project:

```text
gen-lang-client-0240752803
```

Default location:

```text
global
```

GCS output prefix:

```text
gs://sachin-kayaking-video-test/book_reels/veo/
```

Generation settings:

```text
aspect ratio: 9:16
resolution: 720p
duration: 8 seconds
videos per request: 1
audio: false
prompt enhancement: true
```

The script used:

```python
client.models.generate_videos(...)
```

with a clean image passed as the starting frame.


---

# 16. Veo Result Handling and Diagnostic Improvements

A later Veo generation completed but returned:

```text
No generated video URI returned
```

The script was then improved to:

- inspect both `operation.response` and `operation.result`,
- save the completed raw operation,
- support both:
  - `video.uri`
  - `video.video_bytes`
- report:
  - RAI filtered count,
  - RAI filtered reasons,
  - operation errors.

Additional output:

```text
metadata/veo_last_operation.json
```

This turned an opaque failure into a diagnosable state.

A later rerun succeeded correctly.


---

# 17. Production Veo Clips

The approved production Veo clips were:

### Shot 2
```text
gs://sachin-kayaking-video-test/book_reels/veo/6102034147660935021/sample_0.mp4
```

### Shot 5
```text
gs://sachin-kayaking-video-test/book_reels/veo/8649026797495028929/sample_0.mp4
```

### Shot 6
```text
gs://sachin-kayaking-video-test/book_reels/veo/3451503193750344566/sample_0.mp4
```

An earlier Shot 2 test based on the raw screenshot remained in the bucket but was not used for production.


---

# 18. Seed Result Overwrite Bug

A subtle pipeline bug appeared when generating shots one at a time.

`veo_seed_results.json` was overwritten on each run.

That meant after generating Shot 6, the JSON only knew about Shot 6.

As a result, when Shot 5 was later passed to the Veo generation script, the script could fail to locate its recomposed seed and silently fall back to the original screenshot.

The fix was architectural:

> **The actual deterministic seed file on disk became authoritative.**

The script now checks:

```text
generated/veo_seeds/shot_XX_seed.png
```

before relying on the results JSON.

This prevented future accidental fallback to source screenshots.


---

# 19. Still Asset Generation for Non-Veo Shots

Shots not using Veo still needed clean production images.

These were:

- Shot 1
- Shot 3
- Shot 4
- Shot 7
- Shot 8

Script:

```text
prepare_remotion_stills.py
```

Model:

```text
gemini-3.1-flash-image
```

Outputs:

```text
generated/remotion_stills/shot_01_still.png
generated/remotion_stills/shot_03_still.png
generated/remotion_stills/shot_04_still.png
generated/remotion_stills/shot_07_still.png
generated/remotion_stills/shot_08_still.png
```

The prompts intentionally removed all screenshot UI and created clean 9:16 compositions.


---

# 20. Shot 1 Image Iteration

The first generated Shot 1 still had a composition defect:

> two tombstones were generated.

This was rejected.

The prompt was tightened to explicitly require:

- exactly one tombstone,
- no duplicate,
- no second grave marker,
- no reflection,
- no repeated stone shape,
- a single clear focal subject.

The regenerated image was approved.

This reinforced an important QA rule:

> AI-generated visual assets are not accepted solely because generation succeeded; they must pass visual inspection.


---

# 21. Remotion Installation and Project Setup

The final composition system was implemented in Remotion.

The Remotion project was created inside:

```text
/Users/sachinpb/PycharmProjects/book_reels/remotion
```

Template:

```text
Blank
```

Tailwind:

```text
No
```

Useful Remotion agent skills were installed:

- `remotion-best-practices`
- `remotion-captions`
- `remotion-multimedia`
- `remotion-render`
- `remotion-studio`

The project stayed inside the existing Git repository rather than initializing a nested repository.


---

# 22. Remotion Public Asset Structure

Assets were copied into:

```text
remotion/public/
```

Final structure:

```text
public/
├── audio/
│   └── narration.wav
│
├── stills/
│   ├── shot_01.png
│   ├── shot_03.png
│   ├── shot_04.png
│   ├── shot_07.png
│   └── shot_08.png
│
├── video/
│   ├── shot_02.mp4
│   ├── shot_05.mp4
│   └── shot_06.mp4
│
└── data/
    ├── subtitle_cues.json
    └── visual_plan.json
```


---

# 23. Remotion Timeline Architecture

The first Remotion coding pass intentionally did **not** include:

- subtitles,
- transitions,
- typography,
- still animation,
- visual effects.

The goal was simply to prove the full timeline.

Claude Code created:

```text
src/timeline.ts
src/BookReel.tsx
```

and modified:

```text
src/Composition.tsx
src/Root.tsx
```

The composition:

```text
id: BookReel
width: 1080
height: 1920
fps: 30
durationInFrames: 1573
```

Total duration:

```text
52.44 seconds
```

The timeline:

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

Approximate frame boundaries at 30 fps:

```text
Shot 1: 0–359
Shot 2: 360–554
Shot 3: 555–734
Shot 4: 735–1013
Shot 5: 1014–1151
Shot 6: 1152–1349
Shot 7: 1350–1487
Shot 8: 1488–1572
```

The first pass rendered stills with `<Img>`, videos with `<Video>`, and narration with `<Audio>`.


---

# 24. Subtitle Rendering in Remotion

Once the base timeline was verified, subtitles were implemented.

The authoritative source remained:

```text
public/data/subtitle_cues.json
```

No cues were rewritten, regrouped, split, merged, or retimed.

A reusable subtitle component was added.

Design goals:

- lower-third safe placement,
- centered,
- readable on mobile,
- semibold / bold,
- white / near-white,
- dark readability support,
- max width around 80–85%,
- restrained entrance motion.

Semantic styles such as:

```text
IMPACT
EMPHASIS
REFLECTION
```

were preserved where present.

The subtitle system was then visually verified against narration.


---

# 25. Still Motion

After subtitles were working, only the still shots were animated.

A reusable animated-still component was introduced.

Motion remained intentionally restrained.

### Shot 1
```text
slow push-in
scale ≈ 1.00 → 1.07
```

### Shot 3
```text
soft drift
scale ≈ 1.02 → 1.07
```

### Shot 4
```text
slow explanatory push-in
scale ≈ 1.00 → 1.06
```

### Shot 7
```text
gentle lateral drift across horizon
very small scale change
```

### Shot 8
```text
almost imperceptible final drift
```

Important rule:

> Veo shots were not supposed to inherit still-image transforms.


---

# 26. “Don’t Try.” Impact Typography

The phrase:

```text
Don't try.
```

was given its own cinematic treatment.

Timing aligned with:

```text
~9.92s – 11.65s
```

The normal subtitle cue was suppressed during this moment so the text would not appear twice.

The special impact typography used:

- large centered type,
- mature off-white / white,
- restrained fade,
- slight scale settle,
- optional minimal vertical motion,
- no per-letter animation,
- no cheesy trailer effects.

The visual beat became:

```text
Narration says “Don’t try.”
      ↓
Large impact typography lands
      ↓
Pause breathes
      ↓
“He faced 30 years of rejection.”
```

This was one of the most important editorial moments in the reel.


---

# 27. Final Visual Polish

The final polish stage focused on cohesion rather than adding spectacle.

Transitions were kept short and restrained.

Preferred treatments:

- opacity crossfade,
- gentle dissolve,
- occasional clean cut,
- soft fade toward dark only where appropriate.

No:

- spins,
- wipes,
- glitches,
- zoom transitions,
- camera shake,
- film burns,
- heavy LUTs,
- aggressive grain.

The final shot was allowed to breathe rather than fading out too early.


---

# 28. First Render

The first full render command was:

```bash
npx remotion render BookReel out/book_reel_v1.mp4
```

Output:

```text
remotion/out/book_reel_v1.mp4
```

This became the first full V1 for end-to-end visual QA.


---

# 29. Shot 2 Stutter / Zoom Debugging

During V1 review, a visible problem was found around:

```text
00:13 – 00:15
```

The symptom looked like:

- stuttering,
- fast zoom in,
- fast zoom out,
- breathing / pulsing motion.

Initial suspicion:

> 24 fps Veo footage inside a 30 fps Remotion composition.

The source was verified:

```bash
ffprobe ...
```

Result:

```text
r_frame_rate=24/1
avg_frame_rate=24/1
```

A 30 fps interpolated version was created using `minterpolate`.

That did **not** solve the problem.

This proved the issue was not fundamentally the 24→30 fps mismatch.


---

# 30. Correct Root Cause — Remotion Transform Logic

The raw Veo clip played correctly outside Remotion.

Both:

- the original 24 fps source, and
- the converted 30 fps source

showed the zoom issue only after passing through Remotion.

That isolated the real cause:

> **Remotion-side transform or transition logic was affecting video shots.**

The fix required ensuring Shot 2 rendered as a plain video layer:

```text
transform: none
scale: 1
translateX: 0
translateY: 0
```

No:

- spring transform,
- interpolate transform,
- Ken Burns effect,
- perspective,
- zoom,
- animated filter.

Transitions touching Shot 2 were restricted to opacity only.

The Shot 2 issue was fixed.


---

# 31. Same Fix Applied to Shots 5 and 6

After Shot 2 was fixed, the same issue was observed in Shots 5 and 6.

The same principle was applied:

> **Video shots must remain visually stable and must never inherit still-image transform logic.**

Shot 5 and Shot 6 were changed to:

- fixed scale,
- fixed translation,
- no zoom,
- no animated transform,
- opacity-only transitions.

This removed the same rapid zoom / breathing problem from the remaining Veo shots.


---

# 32. Final Visual Architecture

The final reel architecture became:

```text
SHOT 1
Clean Gemini-generated tombstone still
+ Remotion slow push
+ normal captions
+ special “Don’t try.” typography

SHOT 2
Gemini-recomposed seed
→ Veo video
→ plain stable Remotion playback
+ captions

SHOT 3
Gemini-generated still
+ gentle Remotion drift
+ captions

SHOT 4
Gemini-generated conceptual Backwards Law still
+ slow Remotion push
+ captions
+ title typography if used

SHOT 5
Gemini-recomposed mirror seed
→ Veo video
→ stable Remotion playback
+ captions

SHOT 6
Gemini-recomposed cliff seed
→ Veo video
→ stable Remotion playback
+ captions

SHOT 7
Gemini-generated ocean / horizon still
+ gentle lateral drift
+ captions

SHOT 8
Gemini-generated reflective tombstone callback
+ extremely slow drift
+ final reflection captions
```

This created a hybrid visual language:

```text
AI-generated stills
+
selective AI video
+
deterministic typography
+
deterministic subtitles
+
deterministic timing
+
deterministic rendering
```


---

# 33. Final Pipeline File Map

A representative project layout is:

```text
book_reels/
│
├── source_images/
│   ├── IMG_8802.PNG
│   ├── IMG_8803.PNG
│   ├── IMG_8804.PNG
│   ├── IMG_8805.PNG
│   ├── IMG_8806.PNG
│   └── IMG_8807.PNG
│
├── audio/
│   └── narration.wav
│
├── generated/
│   ├── veo_seeds/
│   │   ├── shot_02_seed.png
│   │   ├── shot_05_seed.png
│   │   └── shot_06_seed.png
│   │
│   ├── remotion_stills/
│   │   ├── shot_01_still.png
│   │   ├── shot_03_still.png
│   │   ├── shot_04_still.png
│   │   ├── shot_07_still.png
│   │   └── shot_08_still.png
│   │
│   └── veo/
│       ├── shot_02.mp4
│       ├── shot_05.mp4
│       └── shot_06.mp4
│
├── metadata/
│   ├── assets.json
│   ├── analyzed_assets.json
│   ├── story_plan.json
│   ├── subtitle_cues.json
│   ├── visual_plan.json
│   ├── veo_seed_manifest.json
│   ├── veo_seed_results.json
│   ├── veo_generation_manifest.json
│   ├── veo_generation_results.json
│   ├── veo_generation_status.txt
│   ├── veo_last_operation.json
│   ├── remotion_still_manifest.json
│   └── remotion_still_results.json
│
├── ingest_assets.py
├── generate_voice.py
├── extract_word_timing.py
├── visual_director.py
├── prepare_veo_seed_images.py
├── generate_veo_clips.py
├── prepare_remotion_stills.py
│
└── remotion/
    ├── public/
    │   ├── audio/
    │   ├── stills/
    │   ├── video/
    │   └── data/
    │
    ├── src/
    │   ├── timeline.ts
    │   ├── BookReel.tsx
    │   ├── Composition.tsx
    │   ├── Root.tsx
    │   └── components/
    │       ├── Subtitles.tsx
    │       ├── AnimatedStill.tsx
    │       └── ImpactTypography.tsx
    │
    └── out/
        └── book_reel_v1*.mp4
```


---

# 34. Core Design Principles

## 34.1 Narration is authoritative

Once narration was approved and generated, all later timing was built around the actual audio.

The reel was not forced into an arbitrary 40-second target once the natural approved audio measured 52.44 seconds.


## 34.2 Metadata before rendering

The system relied on structured metadata for:

- assets,
- story,
- subtitle cues,
- visual shots,
- Veo plans,
- generation results.

This made the pipeline debuggable and reproducible.


## 34.3 Deterministic timing

Creative AI models were not trusted with final timing.

Exact timing was handled by:

- word timestamps,
- deterministic cue grouping,
- fixed shot boundaries,
- Remotion frame math.


## 34.4 Selective AI generation

AI video was only used where motion added storytelling value.

Still images remained still when stillness was more appropriate.


## 34.5 Separate image preparation from video generation

A major lesson was that Veo should not receive a messy mobile screenshot if the final shot needs a clean cinematic composition.

The improved flow was:

```text
screenshot
→ clean AI recompose
→ visual QA
→ Veo
```


## 34.6 Visual QA gates matter

Generation success is not approval.

Examples:

- raw screenshot visible in first Veo attempt → rejected,
- duplicate tombstone → rejected,
- wrong seed fallback → fixed,
- Remotion zooming video shots → fixed.


## 34.7 Video and still motion must be architecturally separate

Still shots can use:

- scale,
- pan,
- drift,
- Ken Burns-like motion.

Video shots should normally use:

```text
transform: none
```

unless a deliberate video transform is explicitly designed.

Mixing the two caused the fast zoom / breathing bug.


## 34.8 Preserve canonical scripts

The project intentionally avoided endless file proliferation like:

```text
script_v2.py
script_v3.py
script_final2.py
```

Canonical scripts were updated in place.


---

# 35. Important Failure Modes and Lessons

## Failure: using screenshots directly in Veo

### Symptom
UI and screenshot framing remained visible during the first part of the generated clip.

### Fix
Create clean 9:16 seed images with Gemini before calling Veo.


---

## Failure: overly conservative visual planner

### Symptom
Visual Director suggested avoiding AI video entirely.

### Fix
Human creative override selected three specific moments where AI video materially improved the reel.


---

## Failure: Gemini subtitle grouping

### Symptom
Free-form JSON grouping was unreliable.

### Fix
Use deterministic segmentation from exact word timestamps.


---

## Failure: missing Veo URI

### Symptom
Generation completed but no URI was detected.

### Fix
Inspect:

- `operation.response`,
- `operation.result`,
- URI,
- inline bytes,
- RAI filtering,
- raw operation state.


---

## Failure: result JSON overwriting

### Symptom
Generating seeds one at a time caused earlier seed metadata to disappear.

### Fix
Treat deterministic on-disk asset paths as authoritative.


---

## Failure: duplicate tombstone in generated still

### Symptom
Shot 1 contained two tombstones.

### Fix
Strengthen prompt with explicit single-subject constraints and regenerate.


---

## Failure: apparent 24 fps stutter

### Symptom
Veo Shot 2 looked like it was rapidly zooming in and out inside the final render.

### Initial hypothesis
24 fps source in a 30 fps composition.

### Test
Create 30 fps motion-interpolated source.

### Result
Issue remained.

### Actual cause
Remotion transform logic was affecting the video layer.

### Fix
Video shots use stable transform-free rendering; transitions use opacity only.


---

# 36. Final Rendering Philosophy

The final renderer is Remotion because it gives exact control over:

- frame timing,
- asset placement,
- narration,
- subtitles,
- text hierarchy,
- transforms,
- transitions,
- reproducibility,
- rerendering.

AI generates assets.

Remotion assembles the final product.

That separation is central to the architecture.


---

# 37. Reusable Pipeline for Future Reels

This workflow can now be generalized.

For future book / idea / wisdom reels:

```text
1. Drop source screenshots / references into source_images/

2. Run asset ingestion

3. Run semantic image analysis

4. Build story plan

5. Run story quality loop

6. Lock narration

7. Generate voice

8. Extract word timing

9. Generate deterministic subtitle cues

10. Build visual plan

11. Decide per shot:
    - still
    - AI-recomposed still
    - AI-recomposed seed → Veo

12. Generate / approve all visual assets

13. Copy production assets into Remotion public/

14. Build deterministic timeline

15. Add subtitles

16. Add still motion

17. Add special typography

18. Add restrained transitions

19. Render

20. Perform final QA

21. Fix only targeted issues

22. Render final master
```


---

# 38. Current Final State

At the end of this production process:

- story is locked,
- narration is locked,
- word timing is locked,
- subtitle cues are locked,
- visual plan is locked,
- still assets are generated,
- Veo clips are generated,
- Remotion project is configured,
- timeline is implemented,
- subtitles are implemented,
- still motion is implemented,
- “Don’t try.” impact typography is implemented,
- transitions / final polish are implemented,
- video transform bug has been diagnosed and fixed,
- the reel can be rendered deterministically as a full MP4.

The project is now a complete hybrid AI + deterministic video production pipeline rather than a one-off manual edit.


---

# 39. Architectural Summary

The strongest version of the system can be summarized as:

```text
UNDERSTAND
source screenshots
→ semantic metadata

WRITE
semantic metadata
→ approved narration

TIME
narration
→ exact word timing
→ exact subtitle cues

DESIGN
story + semantic assets
→ shot plan

GENERATE
screenshots
→ clean AI visual assets
→ selective Veo motion

ASSEMBLE
visuals + narration + cues
→ deterministic Remotion timeline

POLISH
motion + captions + typography + transitions

VERIFY
visual QA
→ targeted fixes

RENDER
final 1080×1920 MP4
```

This architecture preserves the strengths of generative AI while keeping the parts that require precision under deterministic control.
