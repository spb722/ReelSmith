# Veo subject motion: version 2 to-do

Version 1 (branch `veo-subject-motion`) added the `MAX_VEO_SHOTS` setting, subject-motion
rules for the visual planner, a budget warning and stricter clip QA. Start these only
after version 1 has been checked on real reels.

## 1. Shots longer than 8 s can become video
**Status: built, opt-in (`VEO_EXTEND_LONG_SHOTS`).** Off by default. The notes below are
the test findings and the build notes it was built from.
Two build notes changed: the decision is not stored in the plan (it is worked out from
the shot's length and the settings), and the planner writes no second motion line (the
code uses one fixed "quiet hold" extension prompt).

Today a shot longer than the longest Veo clip (8 s) always stays a still
(`select_clip_duration_seconds`, `orchestrator/tools/veo_tools.py`). This is the main reason
a reel gets fewer videos than asked for.
**Chosen fix: Veo's native "extend video" (approach B).** Tested on 2026-09-30 on
`flashlight_effect` shot 4 (10.84 s). Test files are in the session scratchpad, under
`veo_continuation/`.

How it works:
```text
clip 1: image-to-video from the seed, 8 s           (today's call, unchanged)
   ↓ clip 1 is already in GCS (output_gcs_uri)
extend: generate_videos(source=GenerateVideosSource(prompt=follow-on motion,
        video=types.Video(uri=<clip 1 gs:// uri>, mime_type="video/mp4")), duration_seconds=7)
   ↓ Veo returns the FULL combined video (8 s + 7 s = 15 s), not only the new part
trim to the shot's length, drop the audio track → the shot's one mp4
```

What the test showed:
- Works on `veo-3.1-fast-generate-001` (google-genai 2.23.0) at 720p, 9:16.
- The motion keeps flowing across the join: speed after the join was 1.20× the speed
  before it. The face stays toward the viewer, and identity, style and lighting stay
  the same.
- The input video must be a `gs://` URI.
- The extension was asked for as 7 s, which is Google's documented length. Shorter
  lengths are untested. Check whether 4–6 s is accepted, to save cost.
- The returned mp4 has a silent AAC track even with `generate_audio=False`. Strip it.
- The first 192 frames of the returned video match clip 1, so no ffmpeg join is needed.
- Cost: 15 s of Veo output for a 10.84 s shot (about 25% more than approach A).

Why not the other options:
- **A. Image-to-video again from clip 1's last frame, then an ffmpeg join.** Rejected.
  Motion froze for about 0.3 s after the join (speed ratio 0.15), and the follow-on clip
  tipped the head back to the ceiling.
- **Hold the last frame.** Rejected. The character freezes mid-shot.
- **Slow the clip down.** Rejected. Veo clips are 24 fps and the reel is 30 fps, so a
  slowed moving character stutters.

Build notes:
- Split the clip, not the scene. The shot stays 1:1 with its scene, cues and timeline
  entry, and the shot's asset is still one mp4. Remotion already trims a longer clip to
  the shot's length (`BookReel.tsx` Sequence duration), so the timeline, subtitles and
  Remotion need no change.
- `select_clip_duration_seconds` and its two callers (`run.py` promotion, `veo_tools`
  at attempt time) must agree on the new maximum: 8 + 7 = 15 s per shot. If a setting
  changes the limit, store the decision at promotion, without changing fingerprints.
- The planner writes a second motion line for the extension (a follow-on that "holds
  still"), or the code derives one. It should stay a quiet continuation. In the test,
  the follow-on breath still opened the mouth by the end.
- QA: check the frames around the join (8 s ± 0.5 s) and the end of the shown part for
  face direction, morphing and style. Previews must stay inside the shown window.
- Retries: if the extension fails QA, retry only the extension from the same clip 1. If
  clip 1 fails, redo both. Budget reservation for a long shot = 2 Veo calls.
- Keep it opt-in per shot length. Shots of 8 s or less use exactly today's single call.

Still unsolved in both approaches (track separately):
- Veo zooms in on its own (×1.11 per clip; ×1.16 over the test shot) despite the
  static-camera prompt, and the drift adds up over a longer shot.
- Clip 1 didn't follow the planned move: the man leaned forward before leaning back.

Deferred (not in the build):
- Retrying only the extension (reusing clip 1). Every try redoes both calls.
- Extensions shorter than 7 s.
- 1080p.
- More than one extension.
- A per-part camera check. The camera is measured once, over the shown part.
- Trimming the mp4. The 15 s file is kept; Remotion shows only the shot's part.

## 2. Bring back videos the budget turned into stills
`plan_first_attempt_demotions` (`run.py`) writes `STILL` into the plan and promotion is
marked decided, so raising `MAX_BUDGET_USD` later never brings those videos back.
- Mark budget-demoted shots so a later run with more budget can re-promote them, but only
  shots with no approved asset yet. Never re-spend on finished shots.

## 3. Wardrobe and colour drift across clips
Past reels changed the character's shirt colour between clips (e.g. navy, then light blue).
- Name the wardrobe once (style guide or character sheet) and reuse it in every seed.
- QA: fail a clip whose clothes or colours differ from the reel's earlier shots of the
  character.

## 4. Stop early on risky motion
One shot used 7 Veo tries (about $8.40) before falling back to a still.
- Lower the retry cap for motion that failed QA for camera movement, or stop after the first
  failure of the same kind.

## 5. Tighten the camera-move filter
`UNRELIABLE_VIDEO_MOTION` / `_DEPTH_MOVE` (`run.py`) should also catch phrases like
"trudges away down the corridor".

## 6. Optional: a "video share" setting
A percentage (e.g. 50%) instead of a count. Deferred because `floor(share × shots)`
is confusing for small reels. Revisit only if the count feels awkward.
