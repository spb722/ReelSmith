# Veo failure handling

## Tool returned FAILURE

Preserve every failure field exactly in your VeoOutcomeContract. Never flatten
RAI reasons or operation errors.

## Clip QA failed, or no preview images

Return a retryable clip_qa failure preserving the operation name/dump, paths,
costs, and a concrete correction reason for the next attempt.
