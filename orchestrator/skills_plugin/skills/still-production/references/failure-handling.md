# Still failure handling

## Tool returned FAILURE

Preserve every failure field exactly in your StillOutcomeContract.

## Still QA failed

Return a retryable still_qa failure. Include the still's cost_usd and
local_image_path in partial_artifact_paths, plus a concrete correction reason
for the next attempt.
