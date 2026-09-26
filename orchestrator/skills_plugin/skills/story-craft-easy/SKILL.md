---
name: story-craft-easy
description: How to write the spoken narration and its scenes for a short-form reel that a first-time viewer who never read the book can follow and remember - one memorable story carrying one clear lesson, a 90-140 word script, scene sequencing, verbatim impact_text, unused assets and integrity notes - plus the self-review rubric to score and revise the draft. Use when writing, reviewing or revising easy-to-follow narration from an analyzed-assets bundle.
---

# Story craft (easy to follow)

What the narration may claim is governed by the source-fidelity skill.

The viewer has never read this book, is scrolling, and hears every word
once. The goal: after 60 seconds they remember the story, understand the
lesson, and could explain it to a friend in one sentence.

## Writing the narration

- Build ONE clear narrative rather than reading source cards in sequence.
- Do not trust manifest/array order; infer the best story order from meaning.
- Deduplicate overlapping information across cards describing the same idea.
- ONE lesson per reel. If the sources hold several ideas, pick the strongest
  and drop or subordinate the rest -- a reel that teaches one thing well
  beats one that mentions three.
- The story carries the lesson. Follow one concrete person, situation, or
  everyday moment the viewer can picture, and let the lesson come out of
  what happens to them. Make the viewer feel the problem before explaining
  the answer.
- Show the "how", not just the "what". Never say something works "exactly
  this way" or is "the best you can do" without saying, in plain words,
  what actually happens. Walk through the mechanism step by step.
- Everyday words only. Explain every term, name, number, or metaphor the
  first time it appears, in words a 12-year-old would follow -- or leave it
  out. An author's name or a book's label for the idea is optional; the
  idea itself is not.
- One idea per sentence. Short, spoken sentences. Give each step a moment
  to land before the next one arrives.
- Name the lesson once, in one short line people can remember and repeat.
  That line is usually the best impact_text.
- Voice: warm, calm, confident, like a friend explaining something that
  changed how they think. Engaging, never clickbait, ad copy, or
  movie-trailer parody.
- Hook fast: the first one or two sentences create tension, curiosity, or a
  question the viewer recognizes from their own life.
- Give a surprising turn or reversal its own beat -- it is what makes the
  lesson stick.
- End with reflection turned back toward the viewer, not a generic call-to-action.
- narration_script must be 90-140 words. Use the extra room for clarity --
  the mechanism, the example, a beat to breathe -- not for more ideas.
- Do not create video transitions, music cues, or frame-accurate timing.

## Scenes

- sequence starts at 1 and increases continuously with no gaps.
- Concatenating every scene's narration in order, joined with single
  spaces, must reconstruct narration_script exactly (same words, same order).
- estimated_duration_seconds per scene must be positive; the total across
  all scenes must land within about 40-60 seconds.
- source_asset_ids must be non-empty and drawn only from the asset ids
  supplied in this manifest -- never invented ids.
- impact_text is optional on-screen highlight text, ideally seven words or
  fewer; use an empty string when none is needed.
- CRITICAL: if impact_text is non-empty, it MUST appear verbatim inside
  narration_script (and that scene's narration) -- same words, same order.
  Copy a short phrase from the narration; never paraphrase, reorder, or
  invent a slogan that is not already spoken. Downstream subtitle IMPACT
  matching will reject paraphrases.
- List every supplied asset id you did not use in unused_assets with a
  reason, or leave it empty if every asset was used.
- Record any truncation, duplication, ambiguity, or source limitation you
  navigated in source_integrity_notes.

## Self-review

Before returning, score and revise your draft yourself, in your own
reasoning, using the rubric in [references/self-review.md](references/self-review.md).
