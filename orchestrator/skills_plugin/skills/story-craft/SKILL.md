---
name: story-craft
description: How to write the spoken narration and its scenes for a short-form reel - voice, a fast hook, a 90-115 word script, scene sequencing, verbatim impact_text, unused assets and integrity notes - plus the self-review rubric to score and revise the draft. Use when writing, reviewing or revising narration from an analyzed-assets bundle.
---

# Story craft

What the narration may claim is governed by the source-fidelity skill.

## Writing the narration

- Build ONE clear narrative rather than reading source cards in sequence.
- Do not trust manifest/array order; infer the best story order from meaning.
- Deduplicate overlapping information across cards describing the same idea.
- Narration must sound spoken: short, natural sentences, not textbook prose.
- Voice: mature, calm, confident, thoughtful, slightly intense, emotionally
  controlled. Never clickbait, ad copy, or movie-trailer parody.
- Hook fast: the first one or two sentences create tension, curiosity,
  contradiction, or a compelling question.
- Prefer a concrete person/event/example to carry an abstract lesson.
- Give a central paradox, striking quotation, or reversal its own beat.
- Explain the wisdom clearly enough that the viewer needs no screenshots.
- End with reflection turned back toward the viewer, not a generic call-to-action.
- narration_script must be 90-115 words.
- Do not create video transitions, music cues, or frame-accurate timing.

## Scenes

- sequence starts at 1 and increases continuously with no gaps.
- Concatenating every scene's narration in order, joined with single
  spaces, must reconstruct narration_script exactly (same words, same order).
- estimated_duration_seconds per scene must be positive; the total across
  all scenes must land within about 40-50 seconds.
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
