"""Langfuse tracing for every run (Story 4.6, AD-19).

Opt-in by environment only: with `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` set, a run opens one Langfuse trace, grouped in the
session `<project>:<run_id>` and tagged with the project name. `LANGFUSE_BASE_URL`
picks the region; `LANGFUSE_HOST` is the legacy name, and the Langfuse SDK reads
both. Without the keys nothing here imports Langfuse or OpenTelemetry, and every
helper is a no-op.

- Stage, shot, vendor-call and deterministic-step spans come from `span()`.
- Claude turns come from OpenInference's `ClaudeAgentSDKInstrumentor`. Each
  agent module binds its `query` name to `agent_query(...)`, which looks up
  `claude_agent_sdk.query` at call time, so the instrumented function is
  the one that runs, whenever the agent module was imported. That wrapper
  also adds what the instrumentation doesn't capture: the system prompt and
  the skill names.

Tracing fails open: a Langfuse or exporter problem is logged once and never
fails or stalls the run. The exit flush runs in a daemon thread that is joined
with a timeout, because Langfuse's `flush()` takes none.

Nothing secret, and no media bytes, reach a span. `redact()` cleans every value
we pass to Langfuse, and `_RedactingSpanProcessor` cleans every finished span
before Langfuse's exporter sees it. The exporter also covers OpenInference's
tool outputs, where our image tools' base64 blocks would otherwise land. Text
longer than `LANGFUSE_MAX_CHARS` (default 20000) is truncated.
"""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import claude_agent_sdk

from orchestrator.progress import log

DEFAULT_MAX_CHARS = 20000
FLUSH_TIMEOUT_SECONDS = 5.0
ROOT_SPAN_NAME = "book_reels_run"

_SECRET_ENV_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
_SECRET_PATTERNS = (
    re.compile(r"\b[sp]k-lf-[A-Za-z0-9-]{8,}"),               # Langfuse keys
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),                   # Anthropic / OpenAI keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                  # Google API keys
    re.compile(r"\bya29\.[0-9A-Za-z_.-]{16,}"),               # Google OAuth / ADC access tokens
    re.compile(r"\b1//[0-9A-Za-z_-]{20,}"),                   # Google refresh tokens
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)
# `"refresh_token": "..."`, `api_key=...`: the value goes, the key name stays.
# Backslashes allowed before the quotes: JSON text nested inside a JSON attribute.
_SECRET_FIELD = re.compile(
    r"(?i)(\\*\"?(?:client_secret|refresh_token|access_token|id_token|private_key|api[_-]?key|secret[_-]?key)"
    r"\\*\"?\s*[:=]\s*\\*\"?)"
    r"[^\"\\\s,}]+"
)
_DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]+")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{256,}={0,2}")
REDACTED = "[REDACTED]"


@dataclass
class _Tracing:
    client: Any
    provider: Any
    exits: contextlib.ExitStack
    root: Any
    instrumentor: Any = None
    secret_values: list[str] = field(default_factory=list)
    last_halt: dict | None = None


_state: _Tracing | None = None
_warned = False


def _warn_once(what: str, exc: BaseException) -> None:
    global _warned
    if not _warned:
        _warned = True
        log(f"Tracing problem, the run carries on without it: {what}: {type(exc).__name__}: {exc}", error=True)


def enabled() -> bool:
    return _state is not None


def max_chars() -> int:
    try:
        value = int(os.environ.get("LANGFUSE_MAX_CHARS", DEFAULT_MAX_CHARS))
    except ValueError:
        return DEFAULT_MAX_CHARS
    return value if value > 0 else DEFAULT_MAX_CHARS


def _env_secret_values() -> list[str]:
    values = {
        value for name, value in os.environ.items()
        if _SECRET_ENV_NAME.search(name) and len(value) >= 8
    }
    # Longest first, so a secret that contains another is removed whole.
    return sorted(values, key=len, reverse=True)


def redact_text(text: str, limit: int | None = None) -> str:
    """Secrets, data URIs and long base64 runs out; then truncate."""

    secrets = _state.secret_values if _state is not None else _env_secret_values()
    for value in secrets:
        if value in text:
            text = text.replace(value, REDACTED)
    text = _DATA_URI.sub("[media data URI removed]", text)
    text = _BASE64_RUN.sub(lambda match: f"[base64 removed: {len(match.group(0))} chars]", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _SECRET_FIELD.sub(lambda match: match.group(1) + REDACTED, text)
    limit = max_chars() if limit is None else limit
    if len(text) > limit:
        text = text[:limit] + f"... [truncated {len(text) - limit} chars]"
    return text


def redact(value: Any) -> Any:
    """A JSON-ready copy of `value` with every string passed through `redact_text`."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[{len(value)} bytes removed]"
    if isinstance(value, Path):
        return redact_text(str(value))
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return redact(model_dump(mode="json"))
        except Exception:
            pass
    return redact_text(str(value))


def _redact_attribute(key: str, value: Any) -> Any:
    # Langfuse's own attributes are JSON that `redact()` already truncated string
    # by string; cutting the serialized JSON again would leave it invalid.
    limit = sys.maxsize if key.startswith("langfuse.") else None
    if isinstance(value, str):
        return redact_text(value, limit)
    if isinstance(value, (list, tuple)):
        return tuple(redact_text(item, limit) if isinstance(item, str) else item for item in value)
    return value


class _RedactingSpanProcessor:
    """Cleans each finished span before any exporter sees it.

    Registered on the tracer provider ahead of Langfuse's own processor, and
    the SDK hands every processor the same finished-span snapshot. So
    redaction here happens before Langfuse queues the span, and before its
    exporter's media processing could upload base64 found in an attribute.
    """

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def _on_ending(self, span: Any) -> None:
        return None

    def on_end(self, span: Any) -> None:
        try:
            from opentelemetry.sdk.trace import Event
            from opentelemetry.trace import Status

            span._attributes = {
                key: _redact_attribute(key, value) for key, value in (span.attributes or {}).items()
            }
            span._events = tuple(
                Event(
                    event.name,
                    {key: _redact_attribute(key, value) for key, value in (event.attributes or {}).items()},
                    event.timestamp,
                )
                for event in span.events
            )
            if span.status is not None and span.status.description:
                span._status = Status(span.status.status_code, redact_text(span.status.description))
        except Exception as exc:
            # A span that could not be cleaned is dropped, never exported raw.
            span._attributes = {}
            span._events = ()
            with contextlib.suppress(Exception):
                span._status = type(span.status)(span.status.status_code)
            _warn_once("span redaction", exc)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _NoopSpan:
    def update(self, **_: Any) -> None:
        return None


_NOOP = _NoopSpan()


class _SpanHandle:
    def __init__(self, observation: Any) -> None:
        self._observation = observation

    def update(self, *, output: Any = None, metadata: Any = None, level: str | None = None,
               status_message: str | None = None) -> None:
        try:
            self._observation.update(
                output=redact(output),
                metadata=redact(metadata),
                level=level,
                status_message=redact_text(status_message) if status_message else None,
            )
        except Exception as exc:
            _warn_once("span update", exc)


@contextlib.contextmanager
def span(name: str, *, as_type: str = "span", input: Any = None, metadata: Any = None) -> Iterator[Any]:
    """A child of the current span, current for the block; a no-op when tracing is off.

    It works in worker threads too: `asyncio.to_thread` copies contextvars, so
    a span opened in an offloaded tool body nests under the caller's span.
    """

    state = _state
    if state is None:
        yield _NOOP
        return
    try:
        manager = state.client.start_as_current_observation(
            name=name, as_type=as_type, input=redact(input), metadata=redact(metadata),
        )
        observation = manager.__enter__()
    except Exception as exc:
        _warn_once(f"opening span {name}", exc)
        yield _NOOP
        return
    handle = _SpanHandle(observation)
    try:
        yield handle
    except BaseException as exc:
        if isinstance(exc, Exception):
            handle.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
        _exit_quietly(manager, sys.exc_info())
        raise
    else:
        _exit_quietly(manager, (None, None, None))


def _exit_quietly(manager: Any, exc_info: tuple) -> None:
    try:
        manager.__exit__(*exc_info)
    except BaseException as exc:
        if exc is not exc_info[1] and isinstance(exc, Exception):
            _warn_once("closing span", exc)


def tool_response_for_trace(response: Any) -> Any:
    """An MCP tool response with JSON text parsed and image blocks reduced to a note."""

    try:
        if not isinstance(response, dict):
            return str(response)
        blocks = []
        for block in response.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text", "")
                try:
                    blocks.append(json.loads(text))
                except (TypeError, ValueError):
                    blocks.append(str(text))
            else:
                blocks.append(f"[{block.get('type')} block omitted: {block.get('mimeType', '')}]")
        return blocks
    except Exception:
        # Tracing never fails the step it describes.
        return "[unreadable tool response]"


def _charged_cost(blocks: Any) -> Any:
    first = blocks[0] if isinstance(blocks, list) and blocks else None
    if not isinstance(first, dict):
        return None
    # A failure or a Veo result carries the call's own charge; seeds and stills carry it flat.
    for key in ("failure", "result"):
        if isinstance(first.get(key), dict) and "cost_usd" in first[key]:
            return first[key]["cost_usd"]
    return first.get("cost_usd")


def traced_tool_call(name: str, body: Any, args: dict, *, metadata: dict) -> Any:
    """Run a vendor tool body inside a span recording its input, result and charge (FR14)."""

    if _state is None:
        return body(args)
    with span(name, as_type="tool", input=args, metadata=metadata) as handle:
        response = body(args)
        try:
            blocks = tool_response_for_trace(response)
            first = blocks[0] if isinstance(blocks, list) and blocks else None
            failure = first.get("failure") if isinstance(first, dict) else None
            handle.update(
                output=blocks, metadata={"cost_usd": _charged_cost(blocks)},
                level="ERROR" if isinstance(failure, dict) else None,
                status_message=str(failure.get("reason") or failure.get("code")) if isinstance(failure, dict) else None,
            )
        except Exception as exc:
            _warn_once(f"recording {name}", exc)
        return response


def record_halt(report: Any) -> None:
    """Put the failure report's halt fields on the halted span and on the run's root span."""

    state = _state
    if state is None:
        return
    try:
        state.last_halt = None
        data = dataclasses.asdict(report) if dataclasses.is_dataclass(report) else dict(report)
        fields = {
            "halted": True,
            "stage_id": data.get("stage_id"),
            "reason_code": data.get("reason_code"),
            "resumable": data.get("resumable"),
            "failed_shots": data.get("failed_shots") or [],
            "reason": data.get("reason"),
        }
        fields = redact(fields)
        state.last_halt = fields
        message = str(fields.get("reason") or "")
        state.client.update_current_span(metadata=fields, level="ERROR", status_message=message)
        state.root.update(metadata=fields, level="ERROR", status_message=message)
    except Exception as exc:
        _warn_once("recording the halt", exc)


def mark_stage_result(handle: Any, exit_code: int | None, **metadata: Any) -> None:
    """End-of-stage record: the exit code, and on a halt the halt fields and ERROR.

    `record_halt` tags whatever span was current, which may be a step inside
    the stage (the render, say); this puts the same fields on the stage itself.
    """

    state = _state
    halt = state.last_halt if state is not None and exit_code else None
    if state is not None:
        state.last_halt = None
    handle.update(
        output={"exit_code": exit_code},
        metadata={**metadata, **(halt or {})} or None,
        level="ERROR" if exit_code else None,
        status_message=str((halt or {}).get("reason") or f"exit code {exit_code}") if exit_code else None,
    )


def agent_query(agent_name: str):
    """The `query` an agent module calls: the SDK's, looked up at call time.

    The lookup through the module is what lets the instrumentor's patch take
    effect even though the agent modules were imported before tracing
    started. Tests still replace the agent module's `query` attribute.
    """

    def query(*, prompt: Any, options: Any = None, **kwargs: Any):
        if _state is None:
            return claude_agent_sdk.query(prompt=prompt, options=options, **kwargs)
        return _traced_query(agent_name, prompt, options, kwargs)

    return query


async def _traced_query(agent_name: str, prompt: Any, options: Any, kwargs: dict):
    skills = getattr(options, "skills", None)
    metadata = {
        "system_prompt": getattr(options, "system_prompt", None),
        "skills_requested": [skills] if isinstance(skills, str) else list(skills or []),
        "model": getattr(options, "model", None),
        "max_budget_usd": getattr(options, "max_budget_usd", None),
    }
    with span(f"{agent_name}.query", as_type="agent", input=prompt, metadata=metadata) as handle:
        # The inner stream is closed here, in this task, before the span ends,
        # so the instrumentation's own context is detached in order.
        async with contextlib.aclosing(claude_agent_sdk.query(prompt=prompt, options=options, **kwargs)) as stream:
            async for message in stream:
                _observe_message(handle, message)
                yield message


def _observe_message(handle: Any, message: Any) -> None:
    try:
        if isinstance(message, claude_agent_sdk.SystemMessage) and message.subtype == "init":
            data = message.data or {}
            handle.update(metadata={
                "skills_loaded": list(data.get("skills") or []),
                "plugins": [plugin.get("name") for plugin in data.get("plugins") or []],
            })
        elif isinstance(message, claude_agent_sdk.ResultMessage):
            output = message.structured_output if message.structured_output is not None else message.result
            handle.update(
                output=output,
                metadata={
                    "total_cost_usd": message.total_cost_usd,
                    "usage": message.usage,
                    "num_turns": message.num_turns,
                    "subtype": message.subtype,
                    "is_error": message.is_error,
                },
                level="ERROR" if message.is_error else None,
                status_message=str(message.errors or message.result or message.subtype) if message.is_error else None,
            )
    except Exception as exc:
        _warn_once("reading an agent message", exc)


def init_run_trace(project: str, run_id: str, *, span_exporter: Any = None) -> bool:
    """Open the run's trace when the Langfuse keys are set. Returns whether tracing is on."""

    global _state
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key and not secret_key:
        return False
    if not (public_key and secret_key):
        log("Langfuse tracing is off: set both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY", error=True)
        return False
    exits = contextlib.ExitStack()
    try:
        from langfuse import Langfuse, propagate_attributes
        from opentelemetry.sdk.trace import TracerProvider

        # Our own provider, never the global one, and not shut down by OTel's
        # atexit hook: the bounded flush below is the only exit work.
        provider = TracerProvider(shutdown_on_exit=False)
        provider.add_span_processor(_RedactingSpanProcessor())
        client = Langfuse(
            public_key=public_key, secret_key=secret_key,
            tracer_provider=provider, span_exporter=span_exporter,
        )
        resources = getattr(client, "_resources", None)
        if resources is not None:
            atexit.unregister(resources.shutdown)
        root = exits.enter_context(client.start_as_current_observation(
            name=ROOT_SPAN_NAME, as_type="span", input=redact({"project": project, "run_id": run_id}),
        ))
        exits.enter_context(propagate_attributes(session_id=f"{project}:{run_id}", tags=[project]))
        _state = _Tracing(client, provider, exits, root, secret_values=_env_secret_values())
    except Exception as exc:
        exits.close()
        _state = None
        _warn_once("starting Langfuse", exc)
        return False
    try:
        from openinference.instrumentation.claude_agent_sdk import ClaudeAgentSDKInstrumentor

        instrumentor = ClaudeAgentSDKInstrumentor()
        instrumentor.instrument(tracer_provider=provider)
        _state.instrumentor = instrumentor
    except Exception as exc:
        # Stage, shot and vendor spans still work; only Claude turn detail is lost.
        _warn_once("instrumenting the Claude Agent SDK", exc)
    log(f"Tracing this run in Langfuse (session {project}:{run_id})")
    return True


def end_run_trace(exit_code: int | None, *, timeout_seconds: float = FLUSH_TIMEOUT_SECONDS) -> None:
    """Close the root span and flush, waiting at most `timeout_seconds`."""

    global _state
    state = _state
    if state is None:
        return
    _state = None
    try:
        state.root.update(output={"exit_code": exit_code})
    except Exception as exc:
        _warn_once("closing the run trace", exc)
    try:
        state.exits.close()
    except Exception as exc:
        _warn_once("closing the run trace", exc)
    if state.instrumentor is not None:
        try:
            state.instrumentor.uninstrument()
        except Exception as exc:
            _warn_once("removing the Claude Agent SDK instrumentation", exc)

    def flush() -> None:
        try:
            state.client.shutdown()
            state.provider.shutdown()
        except Exception as exc:
            _warn_once("flushing to Langfuse", exc)

    worker = threading.Thread(target=flush, name="langfuse-flush", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        log(f"Langfuse did not finish sending within {timeout_seconds:.0f}s; some trace data may be missing", error=True)
