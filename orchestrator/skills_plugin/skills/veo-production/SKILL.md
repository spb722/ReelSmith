---
name: veo-production
description: The step-by-step procedure for producing one VEO-mode shot - calling generate_veo_seed once with the supplied settings, inspecting or confirming the seed, approving it, calling generate_veo_clip once, and judging the returned clip previews. Use when generating and visually validating one already-assigned VEO shot.
---

# Veo production

Whenever step 1 calls for a full inspection, judge the seed with the visual-qa
skill's reject list and, when the tool result includes character reference
image(s), with the character-likeness-qa skill.

## 1. Seed

Call generate_veo_seed exactly once this session using the supplied shot,
cited analyzed asset, subtitle context, and settings -- you must still call it
even on a retry, since you hold no seed object across attempts. Pass every
field of the supplied settings through to the tool exactly as given, including
seed_prompt_level: the orchestrator sets it, it is not yours to choose, and
dropping it silently redraws a seed Veo has already rejected.

- If the supplied previous attempt outcome's failure.stage is "veo_generation"
  or "clip_qa" (the seed already passed QA before; only the clip needed
  correction), do not re-litigate the seed: confirm it is still structurally
  sound and approve it without repeating full visual scrutiny, so the correction
  budget goes to the clip.
- Otherwise -- no previous outcome, or its failure.stage is "seed_qa" or
  "seed_generation" -- give the seed your normal full inspection: reject it if
  it fails the visual-qa reject list (including its VEO seed check: no room
  for the planned gesture, or the face turned away), shows the wrong scene or
  emotion, or has a composition that does not serve the shot, and judge any
  character reference with character-likeness-qa.

On rejection, return a structured retryable seed_qa failure. Include the seed's
cost and path in partial_artifact_paths. Do not call Veo.

## 2. Clip

On seed approval, copy the seed payload exactly, set approved=true, and add a
specific qa_summary describing what you verified. Call generate_veo_clip exactly
once with that approved seed and the supplied settings/attempt.

The correction you pass is added to the video model's prompt, and only its
first 60 words are used. Distil the supplied correction into one or two plain
sentences about the subject's motion alone -- what should move, which way, and
how much -- never a re-description of the scene, never camera directions (the
prompt already fixes a static camera), never quotation marks. The tools draw
the shot from the saved plan, so editing the shot you pass changes nothing.

## 3. Tool failure

If the tool returns FAILURE, build your outcome by following
[references/failure-handling.md](references/failure-handling.md).

## 4. Clip QA

If it returns an unapproved result, inspect every returned preview image.
Approve only if the clip preserves the seed's scene/style, has coherent restrained
motion, no UI/text/extra subjects/distortion, and serves the shot. If the approved seed showed the character, approve the clip only if that
character's face stays stable and recognisable in every preview frame -- reject
warping, melting, face swapping, a face drifting toward a different person, or a
face dissolving into a blank or generic figure.

Apply the visual-qa skill's VEO clip checks to the previews too. Compare the
first and last previews against the shot's video_motion_intent: when it names
a subject action and that body part has not visibly changed pose, the clip is
frozen and fails (an atmosphere-only intent is exempt). The face must be inside
the frame and recognisable in every preview -- a planned push-in never excuses
it leaving. Sleeves, limbs or clothes changing shape within the clip, and
effects the intent did not ask for (rain, particles, extra mist), also fail.
The previews are sampled from the part of the clip the reel actually shows.

Judge the camera by the result's camera_motion, measured from the clip's
pixels -- still previews cannot tell a camera move from a subject move. Every
clip is generated with a static camera, so it must stay within zoom
x0.92-x1.08 and pan within 5%, whatever the plan's wording says. Outside that
is a clip_qa failure; say the measured numbers in your reason. When it could
not be measured, judge the camera from the previews. Copy camera_motion into
your result unchanged.

Return SUCCESS with result.approved=true and a specific clip qa_summary. If no
preview images are available or visual QA fails, return a retryable clip_qa
failure as described in [references/failure-handling.md](references/failure-handling.md).
