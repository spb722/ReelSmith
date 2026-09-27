---
name: visual-planning
description: How to plan one visual shot per story scene - fades and still_motion, source assets, visual_treatment, picture-only frame_composition and motion_plan, text_overlay, ranked video nominations and the overall visual style - plus the self-review rubric to score and revise the plan. Use when planning shots from a validated final story plan and analyzed-assets bundle.
---

# Visual planning

What each shot may assert about its sources is governed by the
source-fidelity skill.

## Shots are scenes

- Produce exactly one shot per scene in the supplied story plan, in the
  same order. shots[i].sequence must equal scenes[i].sequence.
- Do not invent independent shot timing, subtitle cue ids, or start/end
  seconds -- none of that exists yet at this point in the pipeline.

## Renderer motion and fades (every shot)

- fade_in_frames and fade_out_frames are per-shot crossfade lengths in frames
  at 30fps (non-negative integers). Choose values that fit the edit rhythm for
  this shot and its neighbors -- vary them intentionally across shots; never
  use a fixed ladder keyed only on shot number.
- Every shot needs still_motion: Ken-Burns parameters (scale_from, scale_to,
  optional translate_x/y from/to, optional easing one of linear/ease/easeOut)
  that match motion_plan and frame_composition -- intentional per shot,
  grounded in the cited assets' suggested_motion when present. Set it even on
  a shot you nominate for video: a nomination may not be taken up, and the
  shot then falls back to this motion.

## Planning each shot

- source_asset_ids must be non-empty and a subset of that scene's own
  source_asset_ids -- never an asset the scene itself doesn't cite.
- visual_treatment must describe the shot's visual approach using one of:
  USE_EXISTING_ART, CROP_AND_RECOMPOSE, SUBTLE_ANIMATION, TEXT_LED,
  AI_VIDEO_CANDIDATE, MIXED -- normally inherited from the scene's own
  suggested_visual_treatment unless the source analyses justify changing it.
- You do not choose which shots become real video. Nominate instead (see
  Video nominations below); a later deterministic step picks the winners using
  real spoken shot durations, which do not exist yet at this stage.
- Ground frame_composition and motion_plan in the cited assets' own
  production metadata (story_art_region, ui_regions, recommended_crop_strategy,
  suggested_motion, vertical_video_suitability) -- describe concretely what
  part of the asset is framed and how it moves or is cropped.
- shot_goal states the narrative purpose of this shot in the edit.
- text_overlay is optional on-screen text beyond the narration itself; use
  an empty string when none is needed. It is composited by the renderer much
  later, never drawn into the generated artwork.
- CRITICAL: frame_composition and motion_plan describe the PICTURE ONLY --
  what is in frame and how the camera or subject moves. They are fed almost
  verbatim to an image generator, which will literally draw whatever they
  describe. Never mention text, captions, titles, quotes, wording, a line
  that fades in, or text_overlay in either field: the generator paints those
  words into the image, where they cannot be moved, timed, or removed, and
  they smear when the shot moves. Describe the clean plate and leave space
  for text instead.
- source_support explains, in your own words, how the cited assets ground
  this shot's visual choices.

## Video nominations

- Rank at least four shots (or every shot, if the reel has fewer) by how much
  they would gain from real generated video rather than a moving still. Set
  video_candidate_rank = 1 on the strongest candidate, 2 on the next, and so
  on with no gaps and no repeats. Leave it null on the rest.
- Rank by what motion would add to the storytelling: a beat where something
  physically happens, where atmosphere carries the emotion, or where a static
  frame would feel inert. A beat that is already well served by its
  illustration ranks lower.
- On every nominated shot, set video_motion_intent to the motion you would
  want if that shot became video. It is sent to the video model word for word
  as its whole instruction, after a fixed "static shot" opening: every clip is
  made with a still camera, because the model will not hold a camera move it
  is told to. So describe only what moves in the picture and how much -- the
  subject's one action, or the atmosphere -- never the camera. Motion only,
  under 40 words: no reasons, no re-description of the scene, no quotation
  marks, and never text. Leave it empty on shots you did not nominate.
- Nominate only motion from the video model's reliable list, and never one
  from its unreliable list. A nomination whose video_motion_intent names a
  camera move, or a subject moving toward or away from the camera, is not
  taken up; the next-ranked nomination gets the slot instead.
  - Reliable: a small gesture or action by one subject that stays roughly in
    place (turning the head, lifting or setting down an object, breathing,
    a slump or a straightening); hair, clothes, steam, smoke, rain, leaves,
    water or light moving; a subject crossing the frame sideways.
  - Unreliable: a subject walking or running toward or away from the camera
    (the model follows them with the camera instead); any camera move;
    precise hand work or exact object counts; two things moving at once;
    anything that opens, splits, multiplies or transforms; fast action.
- Generated clips are short, so favour beats whose narration is brief and
  punchy; a long, discursive beat is unlikely to be taken up.

## Overall style

- overall_visual_style is one coherent style/tone statement spanning every
  shot -- format, motion language, texture, and mood -- consistent with the
  story plan's voice_direction and story_arc.

## Self-review

Before returning, score and revise your plan yourself, in your own
reasoning, using the rubric in [references/self-review.md](references/self-review.md).
