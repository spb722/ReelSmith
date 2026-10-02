---
name: visual-qa
description: The shared reject list for judging a generated seed or still image - app UI, visible text, extra or duplicate subjects, and photorealistic drift. Use when inspecting a generated seed or still before approving it.
---

# Visual QA

Reject a generated seed or still if it shows any of:

- app UI;
- visible text;
- extra or duplicate subjects;
- photorealistic drift.

For a VEO shot, also reject:

- a seed with no room in the frame for the gesture its video_motion_intent
  plans, or with the character's face turned away from the viewer;
- a clip whose video_motion_intent names a subject action (a hand, head, arm,
  leg or body moving) when that body part shows the same pose in the first and
  last preview frames: the clip is frozen. Intents that move only atmosphere
  (steam, mist, light, leaves) are exempt;
- a clip where, in any preview frame, the character's face leaves the frame,
  is cut off, turns away, or is no longer recognisable. A planned push-in is
  never a reason to accept it;
- a clip where the body or clothes morph between frames: sleeves, limbs,
  hands or garments changing shape, length or colour;
- a clip with effects the video_motion_intent did not ask for, such as rain,
  particles, sparks or extra mist.

Your production skill adds the checks specific to its stage.
