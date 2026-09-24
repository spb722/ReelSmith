---
name: asset-analysis
description: Rules for analyzing one source screenshot into the analysis fields of the analyzed-assets contract - faithful transcription, text blocks, artwork and subjects with regions, concepts, entities, tone, story roles, crop needs, cleanup and uncertainties. Use when analyzing the screenshots listed in an asset manifest, and before returning the analyses.
---

# Asset analysis

## Analyzing each screenshot

- Transcribe visible story text faithfully, separating app UI.
- Identify text blocks, artwork and visual subjects, with approximate regions in
  normalized 0..1000 coordinates.
- Extract supported concepts, named entities with evidence, emotional tone,
  possible story roles, crop/recomposition needs, cleanup and uncertainties.
- Do not invent facts, complete truncated text, write narration, or choose story order.
- Empty strings/lists represent absent content; retain every required field.

## Before returning

Check each analysis against its screenshot for fidelity and completeness.
