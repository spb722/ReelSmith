---
name: still-production
description: The step-by-step procedure for producing one STILL-mode shot - calling generate_still once with the supplied settings, then judging the returned image for text, photorealism, duplicated focal subjects, composition and subtitle space before approving or rejecting it. Use when generating and visually validating one already-assigned STILL shot.
---

# Still production

Judge the image with the visual-qa skill's reject list and, when the tool result
includes character reference image(s), with the character-likeness-qa skill.

## 1. Generate

Call generate_still exactly once this session using the supplied shot,
cited analyzed asset, subtitle context, and settings -- you must still call it
even on a retry, since you hold no image object across attempts.

## 2. Tool failure

If the tool returns FAILURE, build your outcome by following
[references/failure-handling.md](references/failure-handling.md).

## 3. Still QA

If it returns an unapproved result, inspect the returned image. Reject it
if it fails the visual-qa reject list -- including letters, logos, or
watermarks -- or -- the concrete defect this stage exists to catch -- shows
the same focal subject or scene duplicated anywhere in frame (e.g. two
tombstones, two of the same figure). Approve only if it shows exactly one
instance of the focal subject, matches the shot's composition and goal,
leaves clean space for later Remotion subtitles/typography, and passes
character-likeness-qa.

Return SUCCESS with result.approved=true and a specific qa_summary describing
what you verified. If visual QA fails, return a retryable still_qa failure as
described in [references/failure-handling.md](references/failure-handling.md).
