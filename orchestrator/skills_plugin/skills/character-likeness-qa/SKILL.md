---
name: character-likeness-qa
description: How to judge whether a generated image shows the referenced character - what to compare, what to reject, what is not under QA, and what to do when no character reference is returned. Use when a generation tool result includes, or could include, character reference image(s) after the generated seed or still.
---

# Character likeness QA

"The generated image" below is the seed or still your tool just returned.

"The source" below means the scene the shot describes: recomposed from a
screenshot, or, in a reel with no source art, drawn new from the shot's own
description and the reel's style guide. Either way, a returned reference means
the shot's main person must be drawn as that character.

## When the tool result includes character reference image(s)

When the tool result includes character reference image(s) after the generated
image, the scene is one that already contained a person and that person must now
be the referenced character. Those references are NOT under QA -- never judge the
generated image against a reference's background, pose, crop, or framing.

Approve only if the person in the generated image is unmistakably that character:
same face shape, eyes, eyebrows, nose, mouth, beard, hairstyle, hair colour and
skin tone, with the face clearly visible, in focus, and detailed.

- Reject a faceless, blank, featureless, blurred, obscured or turned-away figure.
- Reject a face simplified into dots or a plain oval.
- Reject a generic person who is not the reference.
- Reject a frame whose whole scene has been restyled to match the character --
  only the person may be drawn in the character's style, the scene keeps the
  source's art style, palette and lighting.
- Other people in the scene, such as background silhouettes, must stay as the
  source drew them.

When the reference is a turnaround sheet showing the character from several
angles, follow [references/turnaround-sheets.md](references/turnaround-sheets.md).

## When the tool result includes NO character reference

The source scene has no person in it: the generated image is correct without
one -- never reject it for a missing character and never ask for a person to be
added.
