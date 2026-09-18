from __future__ import annotations

import json

from claude_agent_sdk import ResultMessage

import orchestrator.agents.stills_agent as stills_agent_module
from orchestrator.contracts.veo import shot_fingerprint
from orchestrator.contracts.visual_plan import Shot


def shot_dict() -> dict:
    return Shot(
        sequence=1,
        generation_mode="STILL",
        visual_treatment="USE_EXISTING_ART",
        source_asset_ids=["img_aaaaaaaaaaaa"],
        shot_goal="Show the scene.",
        frame_composition="Center the subject.",
        motion_plan="Slow push-in.",
        text_overlay="",
        source_support="Directly supported.",
    ).model_dump(mode="json")


def failure_output() -> dict:
    shot = Shot.model_validate(shot_dict())
    return {
        "produced_by": "stills_agent",
        "status": "FAILURE",
        "shot_sequence": 1,
        "generation_mode": "STILL",
        "shot_fingerprint": shot_fingerprint(shot),
        "result": None,
        "failure": {
            "produced_by": "stills_agent",
            "shot_sequence": 1,
            "shot_fingerprint": shot_fingerprint(shot),
            "stage": "still_qa",
            "code": "STILL_QA_REJECTED",
            "reason": "The image duplicated the tombstone.",
            "retryable": True,
            "attempt": 1,
            "partial_artifact_paths": ["generated/stills/shot_01.png"],
            "cost_usd": 0.04,
        },
    }


async def _collect(monkeypatch):
    calls = []

    async def fake_query(*, prompt, options):
        calls.append((prompt, options))
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=2,
            session_id="session",
            total_cost_usd=0.1,
            structured_output=failure_output(),
        )

    monkeypatch.setattr(stills_agent_module, "query", fake_query)
    result = await stills_agent_module.generate_still_asset(
        shot=shot_dict(),
        asset={"asset_id": "img_aaaaaaaaaaaa", "source_path": "source_images/a.png"},
        subtitle_text="Narration context.",
        settings={"image_model": "image"},
        attempt=1,
        max_budget_usd=2.0,
        correction="Remove the duplicated subject.",
        previous_outcome={"status": "FAILURE"},
    )
    return calls, result


def test_agent_is_limited_to_still_tool_and_receives_correction(monkeypatch):
    import asyncio

    calls, result = asyncio.run(_collect(monkeypatch))
    assert result.structured_output["status"] == "FAILURE"
    prompt, options = calls[0]
    assert options.max_budget_usd == 2.0
    assert options.tools == options.allowed_tools == ["mcp__gemini__generate_still"]
    assert set(options.mcp_servers) == {"gemini"}
    assert "Read" not in options.tools and "Bash" not in options.tools
    assert "production_assets" not in json.dumps(options.output_format)
    assert "Remove the duplicated subject." in prompt
    assert '"generation_mode": "STILL"' in prompt
