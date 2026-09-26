"""Story 4.6: Langfuse tracing for every run.

No network and no real keys. Spans go to an in-memory OpenTelemetry exporter
passed to the Langfuse client; the SDK is faked at `claude_agent_sdk.query`
(the function the OpenInference instrumentor patches), and every vendor call
is a fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import claude_agent_sdk
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, ToolUseBlock
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from PIL import Image

import orchestrator.run as run
from orchestrator import tracing
from orchestrator.agents import stills_agent as stills_agent_module
from orchestrator.agents import story_agent as story_agent_module
from orchestrator.agents._guard import PaidToolGuard
from orchestrator.agents._skills import PLUGIN_NAME
from orchestrator.contracts.veo import shot_fingerprint
from orchestrator.contracts.visual_plan import Shot
from orchestrator.preflight import PreflightResult
from orchestrator.tests.test_delivery_stage import _write_delivery_inputs, delivery_chain  # noqa: F401
from orchestrator.tests.test_full_chain_resume import chain  # noqa: F401 -- a fixture
from orchestrator.tests.test_story_agent import stage  # noqa: F401 -- a fixture
from orchestrator.tests.test_preflight import make_settings
from orchestrator.tools import gemini_tools, veo_tools

RUN_ID = "run_20260925T101500"
META = "langfuse.observation.metadata."
SDK_QUERY_MODULE = importlib.import_module("claude_agent_sdk.query")


# --- fixtures and helpers -----------------------------------------------------------

@pytest.fixture
def trace(monkeypatch):
    """Returns `start(exporter=None) -> exporter`; the trace is always ended after the test."""

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"pk-lf-test-{uuid.uuid4().hex}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-0123456789abcdef")
    # Never contacted: every span goes to the in-memory exporter.
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(tracing, "_warned", False)

    def start(exporter=None, project="demo_project"):
        exporter = exporter if exporter is not None else InMemorySpanExporter()
        assert tracing.init_run_trace(project, RUN_ID, span_exporter=exporter)
        return exporter

    yield start
    tracing.end_run_trace(None)


def finished(exporter) -> list:
    tracing.end_run_trace(0)
    return list(exporter.get_finished_spans())


def by_name(spans, name: str) -> list:
    return [span for span in spans if span.name == name]


def one(spans, name: str):
    found = by_name(spans, name)
    assert len(found) == 1, (name, [span.name for span in spans])
    return found[0]


def parent_of(spans, span):
    if span.parent is None:
        return None
    return next((s for s in spans if s.context.span_id == span.parent.span_id), None)


def ancestors(spans, span) -> list[str]:
    names = []
    current = parent_of(spans, span)
    while current is not None:
        names.append(current.name)
        current = parent_of(spans, current)
    return names


def attr(span, key: str):
    return (span.attributes or {}).get(key)


def meta(span, key: str):
    value = attr(span, META + key)
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def result_message(output=None, cost=0.25) -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
        session_id="s", total_cost_usd=cost, usage={"input_tokens": 10, "output_tokens": 5},
        structured_output=output,
    )


def install_fake_sdk(monkeypatch, fake):
    """Stand in for the SDK's own `query`, where the instrumentor patches it."""

    monkeypatch.setattr(SDK_QUERY_MODULE, "query", fake)
    monkeypatch.setattr(claude_agent_sdk, "query", fake)


def init_message(options) -> SystemMessage:
    return SystemMessage(subtype="init", data={
        "skills": list(options.skills), "plugins": [{"name": PLUGIN_NAME}],
    })


async def fire_hooks(options, event: str, tool_name: str, **payload) -> list:
    """Call every matcher bound for `event`, as the CLI does for one tool call."""

    results = []
    for matcher in (options.hooks or {}).get(event, []):
        if matcher.matcher not in (None, tool_name):
            continue
        for hook in matcher.hooks:
            results.append(await hook(
                {"hook_event_name": event, "tool_name": tool_name, **payload}, payload.get("tool_use_id"), {},
            ))
    return results


# --- configuration and no-op --------------------------------------------------------

def test_without_keys_nothing_initialises_and_query_is_the_sdk_function(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    sentinel = object()
    calls = []

    def fake(*, prompt, options):
        calls.append(prompt)
        return sentinel

    install_fake_sdk(monkeypatch, fake)
    assert tracing.init_run_trace("p", RUN_ID) is False
    assert not tracing.enabled()
    # The agent's `query` hands back the SDK's own stream object, untouched.
    assert story_agent_module.query(prompt="hi", options=None) is sentinel
    with tracing.span("anything") as handle:
        handle.update(output="ignored")
    assert tracing.traced_tool_call("t", lambda args: {"ok": args}, {"a": 1}, metadata={}) == {"ok": {"a": 1}}
    tracing.record_halt({"reason_code": "x"})
    tracing.end_run_trace(0)
    assert calls == ["hi"]


def test_without_keys_a_run_never_imports_langfuse_or_the_instrumentation():
    env = {key: value for key, value in os.environ.items() if not key.startswith("LANGFUSE_")}
    code = (
        "import sys, orchestrator.run, orchestrator.tracing as t;"
        "assert t.init_run_trace('p', 'r') is False;"
        "t.end_run_trace(0);"
        "bad = [m for m in sys.modules if m.startswith(('langfuse', 'openinference'))];"
        "assert not bad, bad"
    )
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr


def test_one_key_alone_leaves_tracing_off(monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-only")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert tracing.init_run_trace("p", RUN_ID) is False
    assert not tracing.enabled()
    assert "LANGFUSE_SECRET_KEY" in capsys.readouterr().err


def test_langfuse_default_filter_exports_the_openinference_scope(trace):
    from langfuse.span_filter import is_default_export_span

    exporter = trace()
    tracer = tracing._state.provider.get_tracer("openinference.instrumentation.claude_agent_sdk")
    tracer.start_span("scope-probe").end()
    spans = finished(exporter)
    probe = one(spans, "scope-probe")
    assert is_default_export_span(probe)


# --- the run trace ------------------------------------------------------------------

def test_a_mocked_run_is_one_trace_with_a_span_per_stage_and_the_halt_fields(tmp_path, monkeypatch, trace):
    (tmp_path / "source_images").mkdir()
    exporter = InMemorySpanExporter()
    original_init = tracing.init_run_trace
    monkeypatch.setattr(tracing, "init_run_trace", lambda project, run_id: original_init(
        project, run_id, span_exporter=exporter,
    ))
    monkeypatch.setattr(run, "load_settings", lambda: make_settings())
    monkeypatch.setattr(run, "run_preflight", lambda **kwargs: PreflightResult(passed=True))
    monkeypatch.setattr(run, "resolve_character_references", lambda: [])
    monkeypatch.setattr(run, "configure_vendor_limits", lambda **kwargs: None)

    async def ok_screenshots(source, settings, manifest):
        return 0

    async def ok(settings, manifest):
        return 0

    async def halting_delivery(settings, manifest):
        return run._halt(
            "delivery", "render_remotion", "Remotion is out of quota", reason_code="vendor_limit",
            failed_shots=[{"sequence": 2, "reason_code": "vendor_limit"}],
        )

    monkeypatch.setattr(run, "run_screenshot_stage", ok_screenshots)
    for name in ("run_narration_stage", "run_visual_stage", "run_voice_stage", "run_production_stage"):
        monkeypatch.setattr(run, name, ok)
    monkeypatch.setattr(run, "run_delivery_stage", halting_delivery)

    assert run.main(["--project", tmp_path.name]) == 1
    spans = list(exporter.get_finished_spans())
    run_id = next(Path(run.RUN_STATE_DIR).glob("run_*.log")).stem

    root = one(spans, tracing.ROOT_SPAN_NAME)
    assert root.parent is None
    assert {span.context.trace_id for span in spans} == {root.context.trace_id}
    stages = sorted((s for s in spans if parent_of(spans, s) is root), key=lambda s: s.start_time)
    assert [s.name for s in stages] == [
        "preflight", "asset_analyst", "story_agent", "visual_agent", "voice", "production", "delivery",
    ]
    for span in spans:
        assert attr(span, "session.id") == f"{tmp_path.name}:{run_id}"
        assert list(attr(span, "langfuse.trace.tags")) == [tmp_path.name]
    assert meta(one(spans, "preflight"), "passed") is None  # output, not metadata
    assert json.loads(attr(one(spans, "preflight"), "langfuse.observation.output"))["passed"] is True
    delivery = one(spans, "delivery")
    for halted in (delivery, root):
        assert meta(halted, "reason_code") == "vendor_limit"
        assert meta(halted, "resumable") is True
        assert meta(halted, "failed_shots") == [{"sequence": 2, "reason_code": "vendor_limit"}]
        assert attr(halted, "langfuse.observation.level") == "ERROR"
    assert json.loads(attr(root, "langfuse.observation.output")) == {"exit_code": 1}
    assert not tracing.enabled()


def test_the_voice_steps_record_tts_stt_and_cue_count(trace, monkeypatch):
    exporter = trace()

    def tool_returning(payload):
        async def handler(args):
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        return SimpleNamespace(handler=handler)

    monkeypatch.setattr(run, "selected_voice_name", lambda: "Kore")
    monkeypatch.setattr(run, "load_audio_cache", lambda *args: None)
    monkeypatch.setattr(run, "save_audio_cache", lambda *args: {"audio": "cached"})
    monkeypatch.setattr(run, "_check_budget", lambda *args: None)
    monkeypatch.setattr(run, "_atomic_write_json", lambda *args: None)
    monkeypatch.setattr(run, "generate_narration_audio", tool_returning({
        "audio_path": "audio/narration.wav", "duration_seconds": 12.0, "prompt_word_count": 40,
    }))
    monkeypatch.setattr(run, "extract_word_timing", tool_returning({
        "alignment_stats": {"exact_match_ratio": 0.97}, "attempted_audio_seconds": 12.0,
        "words": [{"word": "a"}, {"word": "b"}],
    }))
    monkeypatch.setattr(run, "build_subtitle_cues", tool_returning({
        "alignment_ratio": 0.97, "cues": [{"cue_id": "cue_001"}, {"cue_id": "cue_002"}, {"cue_id": "cue_003"}],
    }))
    plan = SimpleNamespace(
        narration_script="A short script.",
        voice_direction=SimpleNamespace(model_dump=lambda mode: {"pace": "calm"}),
        scenes=[], story_arc=SimpleNamespace(viewer_reflection="Think."),
    )
    result = asyncio.run(run.run_voice_pipeline(plan, make_settings(), 0.0))
    assert result.subtype == "success"

    spans = finished(exporter)
    tts = one(spans, "tts")
    assert json.loads(attr(tts, "langfuse.observation.input"))["narration_script"] == "A short script."
    assert meta(tts, "voice") == "Kore"
    assert meta(tts, "model") == run.TTS_MODEL
    stt = json.loads(attr(one(spans, "stt"), "langfuse.observation.output"))
    assert stt["alignment_stats"] == {"exact_match_ratio": 0.97}
    assert stt["word_count"] == 2
    cues = json.loads(attr(one(spans, "subtitle_cues"), "langfuse.observation.output"))
    assert cues == {"cue_count": 3, "alignment_ratio": 0.97}


def test_delivery_records_the_timeline_render_command_and_output(delivery_chain, monkeypatch, trace):
    exporter = trace()
    _write_delivery_inputs(delivery_chain)

    class Sync:
        async def handler(self, args):
            return {"content": [{"type": "text", "text": "{}"}]}

    class Render:
        async def handler(self, args):
            output = Path(args["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"mp4")
            return {"content": [{"type": "text", "text": json.dumps({
                "output_path": str(output), "command": ["npx", "remotion", "render", "BookReel", str(output)],
                "cwd": "/abs/remotion",
            })}]}

    monkeypatch.setattr(run, "sync_remotion_assets", Sync())
    monkeypatch.setattr(run, "render_remotion", Render())
    monkeypatch.setattr(run, "check_remotion_delivery_toolchain", lambda: None)
    assert asyncio.run(run.run_delivery_stage(make_settings(), run.load_run_manifest(run.RUN_STATE_DIR))) == 0

    render = one(finished(exporter), "render_remotion")
    request = json.loads(attr(render, "langfuse.observation.input"))
    assert request["timeline_path"] == str(run.TIMELINE_FILE)
    assert request["output_path"] == str(run.DEFAULT_REMOTION_RENDER_OUTPUT)
    reported = json.loads(attr(render, "langfuse.observation.output"))[0]
    assert reported["output_path"].endswith(".mp4")
    assert reported["command"][:3] == ["npx", "remotion", "render"]
    assert reported["cwd"] == "/abs/remotion"


def test_a_halt_inside_a_step_still_marks_its_stage(delivery_chain, monkeypatch, trace):
    exporter = trace()
    _write_delivery_inputs(delivery_chain)

    class Sync:
        async def handler(self, args):
            return {"content": [{"type": "text", "text": "{}"}]}

    class FailingRender:
        async def handler(self, args):
            raise RuntimeError("Remotion render failed: out of memory")

    async def ok(settings, manifest):
        return 0

    async def ok_screenshots(source, settings, manifest):
        return 0

    monkeypatch.setattr(run, "sync_remotion_assets", Sync())
    monkeypatch.setattr(run, "render_remotion", FailingRender())
    monkeypatch.setattr(run, "check_remotion_delivery_toolchain", lambda: None)
    monkeypatch.setattr(run, "configure_vendor_limits", lambda **kwargs: None)
    monkeypatch.setattr(run, "run_screenshot_stage", ok_screenshots)
    for name in ("run_narration_stage", "run_visual_stage", "run_voice_stage", "run_production_stage"):
        monkeypatch.setattr(run, name, ok)
    manifest = run.load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_stages(Path("source_images"), make_settings(), manifest)) == 1

    spans = finished(exporter)
    render, delivery = one(spans, "render_remotion"), one(spans, "delivery")
    assert parent_of(spans, render) is delivery
    for halted in (render, delivery):
        assert attr(halted, "langfuse.observation.level") == "ERROR"
        assert meta(halted, "stage_id") == "delivery"
        assert meta(halted, "reason_code") == "unclassified"
        assert meta(halted, "resumable") is False
    assert json.loads(attr(delivery, "langfuse.observation.output")) == {"exit_code": 1}
    assert attr(one(spans, "production"), "langfuse.observation.level") is None


# --- Claude turns ------------------------------------------------------------------

def test_the_instrumentation_takes_effect_on_an_agent_imported_before_it(trace, monkeypatch):
    prompts = []

    async def fake_sdk_query(*, prompt, options):
        prompts.append(prompt)
        yield init_message(options)
        yield result_message({"produced_by": "story_agent"}, cost=0.42)

    install_fake_sdk(monkeypatch, fake_sdk_query)
    exporter = trace()
    # story_agent was imported long before instrumentation; its `query` still
    # reaches the instrumented function.
    result = asyncio.run(story_agent_module.write_story([{"asset_id": "img_1"}], max_budget_usd=1.0))
    assert result.total_cost_usd == 0.42
    spans = finished(exporter)

    agent_span = one(spans, "story_agent.query")
    sdk_span = one(spans, "ClaudeAgentSDK.query")
    assert parent_of(spans, sdk_span) is agent_span
    assert sdk_span.instrumentation_scope.name == "openinference.instrumentation.claude_agent_sdk"
    assert attr(sdk_span, "input.value") == prompts[0]
    assert attr(sdk_span, "llm.cost.total") == 0.42
    # Our own attributes: the system prompt and the skills.
    assert meta(agent_span, "system_prompt") == story_agent_module.story_agent.prompt
    assert meta(agent_span, "skills_requested") == story_agent_module.skills()
    assert meta(agent_span, "skills_loaded") == story_agent_module.skills()
    assert meta(agent_span, "total_cost_usd") == 0.42
    assert json.loads(attr(agent_span, "langfuse.observation.output")) == {"produced_by": "story_agent"}
    # Uninstrumented again once the run ends.
    assert claude_agent_sdk.query is fake_sdk_query


# --- vendor spans across two concurrent shots ---------------------------------------

def still_shot(sequence: int) -> dict:
    return Shot(
        sequence=sequence, generation_mode="STILL", visual_treatment="USE_EXISTING_ART",
        source_asset_ids=["img_aaaaaaaaaaaa"], shot_goal="Show it.", frame_composition="Centered.",
        motion_plan="None.", text_overlay="", source_support="Direct.",
    ).model_dump(mode="json")


def test_two_concurrent_still_shots_nest_their_own_vendor_spans_and_both_hook_sets_fire(
    tmp_path, monkeypatch, trace,
):
    monkeypatch.chdir(tmp_path)
    Path("source_images").mkdir()
    Image.new("RGB", (8, 8)).save("source_images/a.png")
    face_prompt = "Draw the character's face. Auth ya29.a0AfB_byC0123456789abcdefghij"
    exact_prompt = {1: "Shot 1: keep the pose, apply the character.", 2: "Shot 2: keep the pose, apply the character."}

    def fake_spec(shot, asset, subtitle_text):
        sequence = shot["sequence"]
        return {
            "shot_sequence": sequence,
            "shot_fingerprint": shot_fingerprint(Shot.model_validate(shot)),
            "source_asset_id": "img_aaaaaaaaaaaa",
            "source_image_path": "source_images/a.png",
            "output_image_path": f"generated/stills/shot_{sequence:02d}.png",
            "prompt": face_prompt,
            "prompt_ladder": [
                {"level": 0, "prompt": face_prompt, "reference_image_paths": []},
                {"level": 1, "prompt": exact_prompt[sequence], "reference_image_paths": []},
            ],
        }

    # Both shots must be inside a vendor call at once to pass (threads, via to_thread).
    overlap = threading.Barrier(2, timeout=5)

    def fake_image_edit(provider, *, prompt, **kwargs):
        if "face" in prompt:
            raise gemini_tools.ImageEditRefused("refused the face", reason="SAFETY")
        overlap.wait()
        noise = Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3))
        return noise, "done"

    monkeypatch.setattr(gemini_tools, "build_still_spec", fake_spec)
    monkeypatch.setattr(gemini_tools, "_run_image_edit", fake_image_edit)

    tool = "mcp__gemini__generate_still"

    async def fake_sdk_query(*, prompt, options):
        payload = json.loads(prompt.split("\n", 1)[1])
        sequence = payload["shot"]["sequence"]
        tool_use_id = f"toolu_{sequence}"
        tool_input = {
            "shot": payload["shot"], "asset": payload["asset"], "subtitle_text": "",
            "project_id": "p", "location": "global", "image_model": "gemini-image",
            "image_provider": "codex", "codex_timeout_seconds": 1.0, "cost_usd": 0.04,
        }
        yield init_message(options)
        yield AssistantMessage(content=[ToolUseBlock(id=tool_use_id, name=tool, input=tool_input)], model="claude")
        await fire_hooks(options, "PreToolUse", tool, tool_input=tool_input, tool_use_id=tool_use_id)
        # Like the SDK: the in-process tool handler runs in a task spawned here.
        response = await asyncio.get_running_loop().create_task(gemini_tools.generate_still.handler(tool_input))
        await fire_hooks(
            options, "PostToolUse", tool,
            tool_input=tool_input, tool_use_id=tool_use_id, tool_response=response["content"],
        )
        yield result_message({"produced_by": "stills_agent"}, cost=0.1)

    install_fake_sdk(monkeypatch, fake_sdk_query)
    exporter = trace()
    guards = {1: PaidToolGuard(limit_usd=1.0, costs={tool: 0.04}), 2: PaidToolGuard(limit_usd=1.0, costs={tool: 0.04})}

    async def one_shot(sequence: int):
        with tracing.span(f"shot_{sequence:02d}"):
            return await stills_agent_module.generate_still_asset(
                shot=still_shot(sequence), asset={"asset_id": "img_aaaaaaaaaaaa"}, subtitle_text="",
                settings={}, attempt=1, max_budget_usd=1.0, guard=guards[sequence],
            )

    async def both():
        await asyncio.gather(one_shot(1), one_shot(2))

    asyncio.run(both())
    spans = finished(exporter)

    for sequence in (1, 2):
        shot_name, other = f"shot_{sequence:02d}", f"shot_{3 - sequence:02d}"
        tool_spans = [s for s in by_name(spans, "generate_still") if shot_name in ancestors(spans, s)]
        assert len(tool_spans) == 1
        tool_span = tool_spans[0]
        assert other not in ancestors(spans, tool_span)
        assert ancestors(spans, tool_span)[:3] == ["ClaudeAgentSDK.query", "stills_agent.query", shot_name]
        assert meta(tool_span, "provider") == "codex"
        assert meta(tool_span, "model") == "codex"
        assert meta(tool_span, "cost_usd") == 0.04
        output = attr(tool_span, "langfuse.observation.output")
        assert f"generated/stills/shot_{sequence:02d}.png" in output
        assert "[image block omitted" in output

        rungs = sorted(
            (s for s in by_name(spans, "image_edit") if parent_of(spans, s) is tool_span),
            key=lambda s: meta(s, "rung"),
        )
        assert [meta(s, "rung") for s in rungs] == [0, 1]
        assert json.loads(attr(rungs[1], "langfuse.observation.input"))["prompt"] == exact_prompt[sequence]
        assert json.loads(attr(rungs[0], "langfuse.observation.output"))["refused"] is True
        assert meta(rungs[1], "provider") == "codex"
        # The ADC-style token in the rung-0 prompt never reaches the trace.
        assert "ya29." not in attr(rungs[0], "langfuse.observation.input")

        # Both hook sets fired: ours (the guard saw and billed the call) ...
        assert guards[sequence].allowed_usd == pytest.approx(0.04)
        assert guards[sequence].billed_usd == pytest.approx(0.04)
        # ... and the instrumentor's PostToolUse, which is what ended its tool span.
        oi_tool = [s for s in by_name(spans, tool) if shot_name in ancestors(spans, s)]
        assert len(oi_tool) == 1
        tool_output = attr(oi_tool[0], "output.value")
        assert tool_output and "base64 removed" in tool_output
        assert len(tool_output) < 5000


def test_the_veo_tool_span_records_model_uri_duration_prompt_and_failure(trace, tmp_path, monkeypatch):
    from orchestrator.tests.test_veo_tools import RaisingClient, seed_for, shot_for

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(veo_tools.genai, "Client", lambda **kwargs: RaisingClient())
    exporter = trace()
    shot = shot_for()
    seed = seed_for(shot)
    args = {
        "shot": shot.model_dump(mode="json"), "seed": seed.model_dump(mode="json"), "project_id": "p",
        "location": "us-central1", "model": "veo-3", "gcs_output_uri": "gs://bucket/prefix/",
        "resolution": "720p", "duration_seconds": 8, "poll_seconds": 0.01, "max_poll_seconds": 1,
        "attempt": 1, "cost_usd": 1.2, "correction": "",
    }
    response = asyncio.run(veo_tools.generate_veo_clip.handler(args))
    assert json.loads(response["content"][0]["text"])["failure"]["code"] == "SDK_ERROR"

    spans = finished(exporter)
    clip = one(spans, "generate_veo_clip")
    assert meta(clip, "provider") == "gemini"
    assert meta(clip, "model") == "veo-3"
    assert meta(clip, "gcs_output_uri") == "gs://bucket/prefix/"
    assert meta(clip, "aspect_ratio") == "9:16"
    assert meta(clip, "cost_usd") == 1.2
    failure = json.loads(attr(clip, "langfuse.observation.output"))[0]["failure"]
    assert failure["code"] == "SDK_ERROR"
    assert "rai_media_filtered_count" in failure

    request = one(spans, "veo.generate_videos")
    assert parent_of(spans, request) is clip
    prompt, negative = veo_tools.build_video_prompt(shot, seed, "")
    sent = json.loads(attr(request, "langfuse.observation.input"))
    assert sent["prompt"] == prompt
    assert sent["negative_prompt"] == negative
    assert meta(request, "duration_seconds") == veo_tools.select_clip_duration_seconds(
        shot.end_seconds - shot.start_seconds, 8,
    )
    assert meta(request, "aspect_ratio") == "9:16"


# --- redaction and truncation ---------------------------------------------------------

def test_redaction_removes_secrets_media_and_truncates(monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "plainsecretvalue42")
    monkeypatch.setenv("LANGFUSE_MAX_CHARS", "300")
    blob = "A" * 400
    text = (
        "keys pk-lf-1234abcd-ef sk-lf-99999999-aaaa sk-ant-api03-abcdefghijklmnop AIza" + "x" * 35
        + " token ya29.abcdefghijklmnopqrstu Bearer abcdefghijklmnopqrstuvwxyz"
        + ' {"refresh_token": "1//0gAAAAAAAAAAAAAAAAAAAAAAA", "api_key": "short"}'
        + " env plainsecretvalue42 image data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    )
    cleaned = tracing.redact_text(text)
    for secret in ("pk-lf-1234", "sk-lf-9999", "sk-ant-", "AIza", "ya29.", "1//0g", "plainsecretvalue42",
                   "iVBORw0KGgo", "abcdefghijklmnopqrstuvwxyz", '"short"'):
        assert secret not in cleaned, secret
    assert "[media data URI removed]" in cleaned
    assert tracing.redact_text(f"x {blob} y") == "x [base64 removed: 400 chars] y"
    long_text = "word " * 200
    truncated = tracing.redact_text(long_text)
    assert truncated.startswith(long_text[:300])
    assert truncated.endswith("[truncated 700 chars]")
    assert tracing.redact({"raw": b"\x89PNG....", "path": Path("generated/x.png"), "n": 3}) == {
        "raw": "[8 bytes removed]", "path": "generated/x.png", "n": 3,
    }


def test_the_span_processor_redacts_what_the_instrumentation_records(trace):
    exporter = trace()
    tracer = tracing._state.provider.get_tracer("openinference.instrumentation.claude_agent_sdk")
    span = tracer.start_span("probe", attributes={"input.value": "key sk-lf-abcdef123456-x"})
    span.set_attribute("output.value", json.dumps({"type": "image", "data": "Q" * 5000, "mimeType": "image/png"}))
    span.add_event("exception", {"exception.message": "failed with AIza" + "y" * 35})
    span.end()
    probe = one(finished(exporter), "probe")
    assert "sk-lf-" not in attr(probe, "input.value")
    assert "Q" * 300 not in attr(probe, "output.value")
    assert json.loads(attr(probe, "output.value"))["data"] == "[base64 removed: 5000 chars]"
    assert "AIza" not in probe.events[0].attributes["exception.message"]


def test_langfuse_keys_never_reach_a_span(trace, monkeypatch):
    # Shaped like no key pattern, so only the env-value pass can catch them.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"plainpublic{uuid.uuid4().hex}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "plainsecretvaluewithnopattern")
    exporter = trace()
    public, secret = os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]
    with tracing.span("leaky", input=f"public {public} secret {secret}", metadata={"env": dict(os.environ)}):
        pass
    dumped = json.dumps([dict(span.attributes or {}) for span in finished(exporter)])
    assert public not in dumped
    assert secret not in dumped


# --- failing open and the bounded flush ---------------------------------------------

class RaisingExporter(InMemorySpanExporter):
    def export(self, spans):
        raise RuntimeError("Langfuse is down")


def test_an_exporter_that_raises_never_fails_the_run(trace):
    trace(RaisingExporter())
    with tracing.span("stage") as handle:
        handle.update(output={"fine": True})
    started = time.monotonic()
    tracing.end_run_trace(0)
    assert time.monotonic() - started < 5
    assert not tracing.enabled()


def test_a_broken_langfuse_client_leaves_the_code_running(trace, monkeypatch):
    trace()

    def broken(**kwargs):
        raise RuntimeError("client broken")

    monkeypatch.setattr(tracing._state.client, "start_as_current_observation", broken)
    monkeypatch.setattr(tracing._state.client, "update_current_span", broken)
    ran = []
    with tracing.span("stage") as handle:
        handle.update(output="x")
        ran.append(True)
    tracing.record_halt({"reason_code": "vendor_limit"})
    assert tracing.traced_tool_call("t", lambda args: "done", {}, metadata={}) == "done"
    assert ran == [True]


def test_exceptions_in_a_span_still_propagate(trace):
    exporter = trace()
    with pytest.raises(ValueError, match="boom"):
        with tracing.span("stage"):
            raise ValueError("boom")
    stage = one(finished(exporter), "stage")
    assert attr(stage, "langfuse.observation.level") == "ERROR"


def test_a_start_failure_leaves_tracing_off(monkeypatch, trace):
    import langfuse

    def broken(**kwargs):
        raise RuntimeError("bad config")

    monkeypatch.setattr(langfuse, "Langfuse", broken)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-x")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-y")
    assert tracing.init_run_trace("p", RUN_ID) is False
    assert not tracing.enabled()


def test_the_exit_flush_is_bounded_when_export_hangs(trace):
    release = threading.Event()

    class HangingExporter(InMemorySpanExporter):
        def export(self, spans):
            release.wait(30)
            return SpanExportResult.SUCCESS

    trace(HangingExporter())
    with tracing.span("stage"):
        pass
    started = time.monotonic()
    try:
        tracing.end_run_trace(0, timeout_seconds=0.3)
        assert time.monotonic() - started < 3
    finally:
        release.set()


def test_the_production_stage_opens_one_span_per_concurrent_shot(chain, monkeypatch, trace):
    from orchestrator.tests.test_concurrent_shots import install, mixed_plan, success
    from orchestrator.tests.test_concurrent_shots import make_settings as shot_settings

    _, asset_id = chain
    plan = mixed_plan(asset_id, veo=0, stills=2)
    overlap = threading.Barrier(2, timeout=5)

    def vendor_call(sequence: int) -> None:
        # A stand-in for an offloaded tool body: it runs in a worker thread.
        with tracing.span("vendor_probe", metadata={"sequence": sequence}):
            overlap.wait()

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        await asyncio.to_thread(vendor_call, sequence)
        return success(plan, sequence)

    install(monkeypatch, agent)
    exporter = trace()
    manifest = run.load_run_manifest(run.RUN_STATE_DIR)
    with tracing.span("production"):
        assert asyncio.run(run.run_production_stage(shot_settings(shot_concurrency=2), manifest)) == 0

    spans = finished(exporter)
    for probe in by_name(spans, "vendor_probe"):
        sequence = meta(probe, "sequence")
        assert ancestors(spans, probe)[:2] == [f"shot_{sequence:02d}", "production"]
    shot = one(spans, "shot_01")
    assert meta(shot, "generation_mode") == "STILL"
    assert meta(shot, "agent") == "stills_agent"
    assert len(by_name(spans, "vendor_probe")) == 2


def test_the_story_critic_span_sits_inside_the_story_stage_with_its_verdict(stage, monkeypatch, trace):
    from orchestrator.tests.test_reviewers import ISSUE, critique, run_narration, story_critic, story_writer

    _, _, plan = stage
    writes, reviews = [], []
    monkeypatch.setattr(run, "write_story", story_writer(plan, writes))
    monkeypatch.setattr(run, "critique_story", story_critic([critique("REVISE", [ISSUE]), critique()], reviews))
    exporter = trace()
    with tracing.span("story_agent"):
        assert run_narration() == 0

    spans = finished(exporter)
    critics = sorted(by_name(spans, "story_critic"), key=lambda s: s.start_time)
    assert len(critics) == 2
    for critic in critics:
        assert parent_of(spans, critic).name == "story_agent"
    first, second = (json.loads(attr(c, "langfuse.observation.output")) for c in critics)
    assert first["approved"] is False and "Open on the conflict." in first["feedback"]
    assert second == {"approved": True, "feedback": ""}


def test_a_failed_shot_span_is_an_error_carrying_its_report_entry(chain, monkeypatch, trace):
    from orchestrator.tests.test_concurrent_shots import install, success, write_plan
    from orchestrator.tests.test_concurrent_shots import make_settings as shot_settings
    from orchestrator.tests.test_full_chain_resume import failed_still_outcome_for, still_visual_plan_for
    from orchestrator.tests.test_guard_hooks import result_message as guard_result

    _, asset_id = chain
    plan = write_plan(still_visual_plan_for(asset_id, count=2))

    async def agent(**kwargs):
        sequence = kwargs["shot"]["sequence"]
        if sequence == 2:
            failed = failed_still_outcome_for(plan.shots[1], retryable=False, reason="policy blocked")
            return guard_result(failed.model_dump(mode="json"), cost=0.1)
        return success(plan, sequence)

    install(monkeypatch, agent)
    exporter = trace()
    manifest = run.load_run_manifest(run.RUN_STATE_DIR)
    assert asyncio.run(run.run_production_stage(shot_settings(shot_concurrency=2), manifest)) == 1

    spans = finished(exporter)
    failed, ok = one(spans, "shot_02"), one(spans, "shot_01")
    assert attr(failed, "langfuse.observation.level") == "ERROR"
    entry = json.loads(attr(failed, "langfuse.observation.output"))
    assert (entry["sequence"], entry["scope"]) == (2, "shot")
    assert "policy blocked" in attr(failed, "langfuse.observation.status_message")
    assert attr(ok, "langfuse.observation.level") is None


def test_a_tool_failure_contract_marks_its_span_as_an_error(trace):
    exporter = trace()
    failure = {"failure": {"code": "STILL_GENERATION_FAILED", "reason": "quota", "cost_usd": 0.0}}

    def body(args):
        return {"content": [{"type": "text", "text": json.dumps(failure)}]}

    tracing.traced_tool_call("generate_still", body, {}, metadata={"provider": "gemini"})
    # An unreadable response never fails the call it describes.
    assert tracing.traced_tool_call("odd", lambda args: {"content": 5}, {}, metadata={}) == {"content": 5}
    spans = finished(exporter)
    tool = one(spans, "generate_still")
    assert attr(tool, "langfuse.observation.level") == "ERROR"
    assert attr(tool, "langfuse.observation.status_message") == "quota"
    assert meta(tool, "cost_usd") == 0.0
    assert attr(one(spans, "odd"), "langfuse.observation.output") == "[unreadable tool response]"


def test_secret_fields_inside_nested_json_are_redacted():
    inner = json.dumps({"refresh_token": "abcdefgh", "client_secret": "zzz", "api_key": "k1"})
    cleaned = tracing.redact_text(json.dumps({"tool_output": inner}))
    for value in ("abcdefgh", "zzz", "k1"):
        assert value not in cleaned
    assert json.loads(json.loads(cleaned)["tool_output"])["client_secret"] == tracing.REDACTED


def test_long_langfuse_json_stays_valid_while_instrumentation_text_is_truncated(trace, monkeypatch):
    monkeypatch.setenv("LANGFUSE_MAX_CHARS", "200")
    exporter = trace()
    with tracing.span("big", input={"a": "x " * 150, "b": "y " * 150}):
        pass
    tracer = tracing._state.provider.get_tracer("openinference.instrumentation.claude_agent_sdk")
    tracer.start_span("oi", attributes={"input.value": "z " * 500}).end()
    spans = finished(exporter)
    recorded = json.loads(attr(one(spans, "big"), "langfuse.observation.input"))
    assert recorded["a"].endswith("[truncated 100 chars]")
    assert attr(one(spans, "oi"), "input.value").endswith("[truncated 800 chars]")
