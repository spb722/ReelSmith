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

- Rank at least the number of shots your task asks for (four if not stated),
  or every shot if the reel has fewer, by how much they would gain from real
  generated video rather than a moving still. Set video_candidate_rank = 1 on
  the strongest candidate, 2 on the next, and so on with no gaps and no
  repeats. Leave it null on the rest.
- Rank by what motion would add to the storytelling: a beat where the subject
  itself acts -- turns, sits, lifts something -- ranks above one where only
  the atmosphere moves (steam, mist, light), which plays like a moving still.
- A nominated shot's frame_composition should leave the action room: a
  medium or medium-close framing, headroom above the head, space on the
  side the gesture goes, and the face turned to the viewer.
- Only a beat of about 8 seconds of narration or less can become video;
  shorter beats are better candidates. A long, discursive beat is not taken up.
- When your task asks for more than four nominations, spread them through the
  reel: the hook (shot 1) if its subject acts and the beat fits 8 seconds; the
  turn or climax beat; and alternate still and video where you can -- avoid
  three stills in a row and two videos in a row. Keep the final shot a still:
  nominate it only when the count you must reach leaves no other shot.

### The motion

- One visible, body-scale action by one subject, from a readable pose to a
  settled pose, with the camera locked. The video model holds these reliably
  in a stylised picture:
  - Reliable: a head turn or lift; a glance up or down; the shoulders
    dropping or rising; a slump or a straightening; leaning in or back;
    sitting down in place; lifting, lowering or setting down one large
    object; one slow arm gesture.
  - Fails: walking or running toward or away from the camera (the model
    follows with the camera instead); any camera move; fine finger or hand
    work with small items; fast action; lip-sync or speaking; two subjects
    moving; props that open, split or transform; anything with text; effects
    nobody asked for (rain, extra mist); a face turned away for most of the
    clip; crossing the frame sideways or standing up (the face turns away or
    leaves the frame).
  - Too small to read: a clench or a twitch on its own reads as a frozen
    frame. Make it part of a bigger movement, or pick another action.
  - Secondary only: one breath, a blink, a change of expression, or hair or
    clothes stirring end in the pose they began in, so the clip is judged
    frozen. Use them only as the optional secondary motion, never as the
    one action.
- A nomination whose video_motion_intent names a camera move, or a subject
  moving toward or away from the camera, is not taken up; the next-ranked
  nomination gets the slot instead.

### video_motion_intent

- On every nominated shot, set video_motion_intent to the motion you would
  want if that shot became video. It is sent to the video model word for word
  as its whole instruction, after a fixed "static shot" opening, so describe
  only what moves in the picture -- never the camera. Under 40 words: no
  reasons, no re-description of the scene, no quotation marks, and never
  text. Leave it empty on shots you did not nominate.
- Follow this template: [Subject] [one action, start pose to end pose],
  slowly or gently, then holds still. [Optionally one secondary motion: hair
  or cloth sways.] Face stays turned to the viewer.
  For example: The man lifts his head from his hands and looks up, slowly,
  then holds still. His coat stirs. Face stays turned to the viewer.
- Write "turned to the viewer", never "toward the viewer" or "toward the
  camera": those words read as a subject moving in depth, and the shot loses
  its clip.
- The action should finish within the first 60% or so of the clip and then
  hold; the end of a generated clip may not be shown.

## Self-review

Before returning, score and revise your plan yourself, in your own
reasoning, using the rubric in [references/self-review.md](references/self-review.md).
