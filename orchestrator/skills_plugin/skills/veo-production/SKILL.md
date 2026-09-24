---
name: veo-production
description: The step-by-step procedure for producing one VEO-mode shot - calling generate_veo_seed once with the supplied settings, inspecting or confirming the seed, approving it, calling generate_veo_clip once, and judging the returned clip previews. Use when generating and visually validating one already-assigned VEO shot.
---

# Veo production

Judge images with the visual-qa skill's reject list and, when the tool result
includes character reference image(s), with the character-likeness-qa skill.

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
  it fails the visual-qa reject list, shows the wrong scene or emotion, or has
  a composition that does not serve the shot, and judge any character
  reference with character-likeness-qa.

On rejection, return a structured retryable seed_qa failure. Include the seed's
cost and path in partial_artifact_paths. Do not call Veo.

## 2. Clip

On seed approval, copy the seed payload exactly, set approved=true, and add a
specific qa_summary describing what you verified. Call generate_veo_clip exactly
once with that approved seed and the supplied settings/attempt/correction.

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

Return SUCCESS with result.approved=true and a specific clip qa_summary. If no
preview images are available or visual QA fails, return a retryable clip_qa
failure as described in [references/failure-handling.md](references/failure-handling.md).
