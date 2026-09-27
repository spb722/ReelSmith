---
name: visual-planning-content
description: How to plan one invented visual shot per story scene when the source screenshots give only content and no usable artwork - the reel's style guide, a drawable picture per beat, when the recurring character appears, fades and still_motion, video nominations with motion video models can render - plus the self-review rubric to score and revise the plan. Use when planning shots from a validated final story plan and a content-only analyzed-assets bundle.
---

# Visual planning (content only)

The source screenshots supplied the ideas, not the pictures. Every image in
this reel is drawn from your descriptions, so the plan is the whole visual
brief. What each shot may assert is governed by the source-fidelity-content
skill.

## Shots are scenes

- Produce exactly one shot per scene in the supplied story plan, in the
  same order. shots[i].sequence must equal scenes[i].sequence.
- Do not invent independent shot timing, subtitle cue ids, or start/end
  seconds -- none of that exists yet at this point in the pipeline.

## The style guide (overall_visual_style)

- The operator chose the reel's style, and it is supplied with your input.
  Copy it into overall_visual_style unchanged -- the orchestrator writes it
  there again anyway, and it is sent word for word with EVERY image request.
- Plan every shot to suit that style: its palette, lighting, and mood.
- Never describe the main person's looks (face, hair, build, clothing)
  anywhere in the plan. The recurring character's reference sheet decides
  how they look; a description here would contradict it.

## Planning each shot

- source_asset_ids must be non-empty and a subset of that scene's own
  source_asset_ids -- cite the page the shot's idea comes from.
- visual_treatment: the sources have no usable artwork, so never use
  USE_EXISTING_ART, CROP_AND_RECOMPOSE or TEXT_LED. Use AI_VIDEO_CANDIDATE
  or SUBTLE_ANIMATION for beats that want motion, MIXED otherwise.
- frame_composition invents the picture for the beat: the setting, the one
  focal subject, what they are doing, the framing (close, medium, wide), and
  the light. Write it so an illustrator could draw it without reading
  anything else. Show exactly one focal subject.
- Make abstract ideas concrete. Show the story's person, object, or moment,
  never a diagram. A number or percentage becomes a picture (a long corridor
  a third of the way walked, a jar a third full), never written digits.
- Never plan anything that only works with readable text: no signs, labels,
  screens, documents, book pages, charts, or numbers in the picture.
- shows_character: set true when the shot's focal subject is the story's
  main person -- that person is then drawn as the reel's recurring character,
  so they look the same in every shot. Set false when the shot has no person,
  or only other people. Never put the main person in a shot with
  shows_character false.
- Consecutive shots should feel like one world: reuse settings, props, and
  the character where the story allows.
- You do not choose which shots become real video. Nominate instead (see
  Video nominations below); a later deterministic step picks the winners using
  real spoken shot durations, which do not exist yet at this stage.
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
- source_support explains, in your own words, how this picture follows from
  the scene's narration and the cited content.

## Renderer motion and fades (every shot)

- fade_in_frames and fade_out_frames are per-shot crossfade lengths in frames
  at 30fps (non-negative integers). Choose values that fit the edit rhythm for
  this shot and its neighbors -- vary them intentionally across shots; never
  use a fixed ladder keyed only on shot number.
- Every shot needs still_motion: Ken-Burns parameters (scale_from, scale_to,
  optional translate_x/y from/to, optional easing one of linear/ease/easeOut)
  that match motion_plan and frame_composition -- intentional per shot. Set it
  even on a shot you nominate for video: a nomination may not be taken up, and
  the shot then falls back to this motion.

## Video nominations

- Rank at least four shots (or every shot, if the reel has fewer) by how much
  they would gain from real generated video rather than a moving still. Set
  video_candidate_rank = 1 on the strongest candidate, 2 on the next, and so
  on with no gaps and no repeats. Leave it null on the rest.
- Rank by what motion would add to the storytelling: a beat where something
  physically happens, where atmosphere carries the emotion, or where a static
  frame would feel inert.
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

## Self-review

Before returning, score and revise your plan yourself, in your own
reasoning, using the rubric in [references/self-review.md](references/self-review.md).
