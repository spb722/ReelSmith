from __future__ import annotations

import json

from claude_agent_sdk import ResultMessage

import orchestrator.agents.veo_agent as veo_agent_module
from orchestrator.contracts.veo import shot_fingerprint
from orchestrator.contracts.visual_plan import Shot


def shot_dict() -> dict:
    return Shot(
        sequence=1,
        generation_mode="VEO",
        visual_treatment="AI_VIDEO_CANDIDATE",
        source_asset_ids=["img_aaaaaaaaaaaa"],
        shot_goal="Show the scene.",
        frame_composition="Center the subject.",
        motion_plan="Use quiet motion.",
        text_overlay="",
        source_support="Directly supported.",
    ).model_dump(mode="json")


def failure_output() -> dict:
    shot = Shot.model_validate(shot_dict())
    return {
        "produced_by": "veo_agent",
        "status": "FAILURE",
        "shot_sequence": 1,
        "generation_mode": "VEO",
        "shot_fingerprint": shot_fingerprint(shot),
        "result": None,
        "failure": {
            "produced_by": "veo_agent",
            "shot_sequence": 1,
            "shot_fingerprint": shot_fingerprint(shot),
            "stage": "seed_qa",
            "code": "SEED_QA_REJECTED",
            "reason": "The image retained app UI.",
            "retryable": True,
            "attempt": 1,
            "operation_name": None,
            "operation_dump_path": None,
            "rai_media_filtered_count": 0,
            "rai_media_filtered_reasons": [],
            "operation_error": None,
            "partial_artifact_paths": ["generated/veo_seeds/shot_01_seed.png"],
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

    monkeypatch.setattr(veo_agent_module, "query", fake_query)
    result = await veo_agent_module.generate_veo_asset(
        shot=shot_dict(),
        asset={"asset_id": "img_aaaaaaaaaaaa", "source_path": "source_images/a.png"},
        subtitle_text="Narration context.",
        settings={"image_model": "image", "veo_model": "veo"},
        attempt=1,
        max_budget_usd=2.0,
        correction="Remove retained UI.",
        previous_outcome={"status": "FAILURE"},
    )
    return calls, result


def test_agent_is_limited_to_seed_and_veo_tools_and_receives_correction(monkeypatch):
    import asyncio

    calls, result = asyncio.run(_collect(monkeypatch))
    assert result.structured_output["status"] == "FAILURE"
    prompt, options = calls[0]
    assert options.max_budget_usd == 2.0
    assert options.tools == options.allowed_tools == [
        "mcp__gemini__generate_veo_seed",
        "mcp__veo__generate_veo_clip",
    ]
    assert set(options.mcp_servers) == {"gemini", "veo"}
    assert "Read" not in options.tools and "Bash" not in options.tools
    assert "production_assets" not in json.dumps(options.output_format)
    assert "Remove retained UI." in prompt
    assert '"generation_mode": "VEO"' in prompt


def test_prompt_requires_the_seed_and_clip_to_hold_the_character_face():
    from orchestrator.agents.veo_agent import veo_agent

    prompt = veo_agent.prompt
    assert "character reference" in prompt
    assert "never reject it for a missing character" in prompt
    assert "stable and recognisable in every preview frame" in prompt
