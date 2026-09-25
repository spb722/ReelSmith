"""Fresh-context visual-QA reviewer for one generated shot asset (Story 4.5, FR12).

A separate top-level `query()` the orchestrator runs inside a shot's task
once the producer reports an approved seed and clip, or still. It never sees
the producer's session or its QA summary. It opens the asset files and the
character references itself, with a Read that a hook confines to exactly
those files plus its skills, and it has no generation tool.
"""

from __future__ import annotations

import json
from contextlib import aclosing
from pathlib import Path

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage

from orchestrator.agents._skills import (
    PIPELINE_ENV,
    PLUGIN,
    assert_isolated,
    check_init_message,
    file_read_rules,
    plugin_and_files_read_guard,
    plugin_read_rule,
    qualified,
    resolved_files,
)
from orchestrator.agents._stream import log_tool_uses
from orchestrator.contracts.review import CHARACTER_LIKENESS_CODE, VisualQAVerdictContract
from orchestrator.settings import load_settings
from orchestrator.tracing import agent_query

# Resolved through the SDK module at call time, so the tracing instrumentation
# takes effect; tests replace this name (Story 4.6).
query = agent_query("visual_reviewer")


SKILLS = qualified("visual-qa", "character-likeness-qa")
TOOLS = ["Skill", "Read"]


visual_reviewer = AgentDefinition(
    description="Independently judge one generated seed and clip, or still, before it is approved.",
    tools=TOOLS,
    prompt=f"""You are an independent visual-QA reviewer for exactly one shot of a
short-form reel. You did not generate the images you are given and have no
stake in them. Judge them; never generate, edit or fix anything.

YOUR SKILLS
Before judging, load book-reels:visual-qa and book-reels:character-likeness-qa
with the Skill tool and apply them. Where character-likeness-qa speaks of
character references in "the tool result", read that as the character
reference files listed in your input: open them with Read. When the input
lists no character reference, the scene has no character to check.

WHAT TO OPEN
Open every file under "images_under_review" with Read and judge each one. They
are the ONLY images under QA. Character references are for comparing the
person only, never for judging background, pose, crop or framing. Read works
only on those files and your skills' files.
- A VEO shot lists its seed first, then preview frames of the clip made from it.
  A defect in the seed is failure_stage "seed_qa"; a defect that appears only in
  the clip's frames is "clip_qa".
- A STILL shot lists one image; any defect is "still_qa".
The input's "ladder_note", when present, says which character wording the image
model actually accepted. Follow it: never reject for something it says was not
asked for.

YOUR VERDICT
Set approved to true only when every image passes. Otherwise set approved to
false, failure_stage, a short upper-case failure_code, and one reason per
defect. When the defect is that the person does not look like the referenced
character, failure_code must be "{CHARACTER_LIKENESS_CODE}". Always write a
one-to-three sentence summary. Set produced_by to "visual_reviewer". Return
only the supplied JSON schema. Do not ask the operator questions.
""",
)


async def review_visual_asset(
    *,
    generation_mode: str,
    shot: dict,
    subtitle_text: str,
    images_under_review: list[str],
    character_reference_paths: list[str],
    ladder_note: str,
    max_budget_usd: float,
) -> ResultMessage:
    files = resolved_files([*images_under_review, *character_reference_paths])
    payload = {
        "generation_mode": generation_mode,
        "shot": shot,
        "subtitle_text": subtitle_text,
        "images_under_review": [str(path) for path in resolved_files(images_under_review)],
        "character_reference_paths": [str(path) for path in resolved_files(character_reference_paths)],
        "ladder_note": ladder_note,
    }
    prompt = "Review the generated images for this one shot:\n" + json.dumps(payload, ensure_ascii=False)
    options = ClaudeAgentOptions(
        model=load_settings().reviewer_model,
        system_prompt=visual_reviewer.prompt,
        tools=TOOLS,
        allowed_tools=[plugin_read_rule(), *file_read_rules(files)],
        skills=SKILLS,
        plugins=[PLUGIN],
        env=dict(PIPELINE_ENV),
        hooks=plugin_and_files_read_guard(files),
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        cwd=Path.cwd(),
        max_budget_usd=max_budget_usd,
        # Two skill loads plus one Read per image; max_budget_usd bounds spend.
        max_turns=24,
        output_format={"type": "json_schema", "schema": VisualQAVerdictContract.model_json_schema()},
        max_buffer_size=20 * 1024 * 1024,
    )
    assert_isolated(options)
    result = None
    async with aclosing(query(prompt=prompt, options=options)) as stream:
        async for message in stream:
            check_init_message(message, SKILLS)
            log_tool_uses(message)
            if isinstance(message, ResultMessage):
                result = message
    if result is not None:
        return result
    raise RuntimeError("Visual reviewer returned no ResultMessage")
