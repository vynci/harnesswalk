"""OpenTelemetry export of the event stream (spec/events.md section 8, tracer.md P4).

Off by default. When `HARNESSWALK_OTLP_ENDPOINT` (or `trace.otel.endpoint`) is set and the
optional extra `otel` is installed (`uv sync --extra otel`), every turn is exported over OTLP
(HTTP) when it ends, and every ops event as a span of its own.

The mapping is a pure function, `spans_from_events`, so it reads (and tests) without the SDK:

    turn_start .. turn_end        root span `harnesswalk.turn`
    node_enter .. node_exit/error one span per node, nested as the stream nests them
                                  (`chat {model}`, `execute_tool {tool}`, `invoke_agent` for
                                  model_layer, tools and loop; the node id otherwise)
    point events and edges        span events on the innermost open span
    ops events                    one zero-length span `harnesswalk.<type>`

Span ids are the first 16 hex characters of the sha256 of the opening event's `event_id`, so an
export is the same every time. Export runs on a worker thread and never fails a turn.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import queue
import threading
from typing import Any

from harnesswalk.events import Event, EventBus, parse_iso

log = logging.getLogger("harnesswalk.otel")

OPERATION = {"model_layer": "chat", "tools": "execute_tool", "loop": "invoke_agent"}


def span_id_of(event_id: str) -> str:
    digest = hashlib.sha256(event_id.encode()).hexdigest()[:16]
    return digest if digest != "0" * 16 else "0" * 15 + "1"


def _ns(ts: str) -> int:
    return int(round(parse_iso(ts) * 1_000_000_000))


def _attr(value: Any) -> Any:
    """An OpenTelemetry attribute value: scalars and lists of scalars; anything else as JSON."""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list) and all(isinstance(v, (str, bool, int, float)) for v in value):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# Summary keys that carry text a person or the model wrote (redacted for the UI, but still
# content): never exported. GenAI semantic conventions keep message content out of telemetry
# unless a deployment opts in, and in dev the reply can carry the user's own values back.
CONTENT_KEYS = frozenset(
    {"reply", "delta", "text", "message", "preview", "args_preview", "note", "query", "content"}
)
# Point events whose whole point is content (one chunk of the reply).
CONTENT_EVENTS = frozenset({"message_delta"})


def _summary_attrs(summary: dict[str, Any] | None) -> dict[str, Any]:
    return {
        f"harnesswalk.{k}": _attr(v)
        for k, v in (summary or {}).items()
        if v is not None and k not in CONTENT_KEYS
    }


def _name(node_id: str, summary: dict[str, Any]) -> str:
    if node_id == "model_layer" and summary.get("model"):
        return f"chat {summary['model']}"
    if node_id == "tools" and summary.get("tool"):
        return f"execute_tool {summary['tool']}"
    if node_id == "loop":
        return "invoke_agent"
    return node_id


def _node_attrs(enter: Event, close: Event) -> dict[str, Any]:
    node_id = enter["node_id"]
    summary = {**(enter.get("summary") or {}), **(close.get("summary") or {})}
    attrs = {
        "harnesswalk.node.id": node_id,
        "harnesswalk.event.id": enter["event_id"],
        "harnesswalk.event.seq": enter["seq"],
        **_summary_attrs(summary),
    }
    if node_id in OPERATION:
        attrs["gen_ai.operation.name"] = OPERATION[node_id]
    if node_id == "knowledge" and close.get("cost"):
        attrs["gen_ai.operation.name"] = "embeddings"
    cost = close.get("cost")
    if cost:
        attrs["gen_ai.usage.input_tokens"] = cost["input_tokens"]
        attrs["gen_ai.usage.output_tokens"] = cost["output_tokens"]
        attrs["harnesswalk.cost.usd"] = cost["usd"]
    if node_id == "model_layer":
        if summary.get("provider"):
            attrs["gen_ai.provider.name"] = summary["provider"]
        if summary.get("model"):
            attrs["gen_ai.request.model"] = summary["model"]
            attrs["gen_ai.response.model"] = summary["model"]
        if summary.get("finish_reason"):
            attrs["gen_ai.response.finish_reasons"] = [summary["finish_reason"]]
    if node_id == "tools":
        if summary.get("tool"):
            attrs["gen_ai.tool.name"] = summary["tool"]
        if summary.get("call_id"):
            attrs["gen_ai.tool.call.id"] = summary["call_id"]
    if close.get("error"):
        attrs["error.type"] = close["error"]["code"]
    if close.get("detail_ref"):
        attrs["harnesswalk.detail_ref"] = close["detail_ref"]
    return attrs


def spans_from_events(events: list[Event]) -> list[dict[str, Any]]:
    """One finished turn as span records (dicts), parents before children."""
    if not events or events[0]["type"] != "turn_start":
        return []
    start = events[0]
    end = next((e for e in reversed(events) if e["type"] == "turn_end"), events[-1])
    root_id = span_id_of(start["event_id"])
    root: dict[str, Any] = {
        "name": "harnesswalk.turn",
        "trace_id": start["trace_id"],
        "span_id": root_id,
        "parent_span_id": None,
        "start_ns": _ns(start["ts"]),
        "end_ns": _ns(end["ts"]),
        "attributes": {
            "harnesswalk.turn.id": start["turn_id"],
            "harnesswalk.tenant.id": start["tenant_id"],
            "gen_ai.conversation.id": start["session_id"],
            **_summary_attrs(
                {k: v for k, v in (start.get("summary") or {}).items() if k != "release"}
            ),
            **_summary_attrs(
                {k: v for k, v in (end.get("summary") or {}).items() if k in ("exit", "steps")}
            ),
        },
        "events": [],
        "status": "ERROR" if end.get("status") == "error" else "UNSET",
        "status_description": None,
    }
    if end.get("cost"):
        root["attributes"]["gen_ai.usage.input_tokens"] = end["cost"]["input_tokens"]
        root["attributes"]["gen_ai.usage.output_tokens"] = end["cost"]["output_tokens"]
        root["attributes"]["harnesswalk.cost.usd"] = end["cost"]["usd"]
    spans = [root]
    stack: list[tuple[Event, dict[str, Any]]] = []
    for event in events[1:]:
        kind = event["type"]
        if kind == "node_enter":
            parent = stack[-1][1]["span_id"] if stack else root_id
            span = {
                "name": event["node_id"],
                "trace_id": event["trace_id"],
                "span_id": span_id_of(event["event_id"]),
                "parent_span_id": parent,
                "start_ns": _ns(event["ts"]),
                "end_ns": None,
                "attributes": {},
                "events": [],
                "status": "UNSET",
                "status_description": None,
            }
            spans.append(span)
            stack.append((event, span))
        elif kind in ("node_exit", "error") and event.get("node_id") and stack:
            enter, span = stack.pop()
            span["end_ns"] = _ns(event["ts"])
            summary = {**(enter.get("summary") or {}), **(event.get("summary") or {})}
            span["name"] = _name(enter["node_id"], summary)
            span["attributes"] = _node_attrs(enter, event)
            if kind == "error":
                span["status"] = "ERROR"
                span["status_description"] = event["error"]["message"]
        elif kind in CONTENT_EVENTS:
            continue
        elif kind not in ("turn_start", "turn_end"):
            target = stack[-1][1] if stack else root
            attrs = {
                "harnesswalk.event.id": event["event_id"],
                "harnesswalk.event.seq": event["seq"],
            }
            if event.get("edge_id"):
                attrs["harnesswalk.edge.id"] = event["edge_id"]
            attrs.update(_summary_attrs(event.get("summary")))
            target["events"].append({"name": kind, "ts_ns": _ns(event["ts"]), "attributes": attrs})
        elif kind == "turn_end":
            break
    for _, span in stack:  # never closed (a cut-off stream): end at the turn's end
        span["end_ns"] = root["end_ns"]
    return spans


def span_from_ops_event(event: Event) -> dict[str, Any]:
    ts = _ns(event["ts"])
    attrs = {
        "harnesswalk.tenant.id": event["tenant_id"],
        "harnesswalk.event.id": event["event_id"],
        "harnesswalk.event.seq": event["seq"],
        "harnesswalk.node.id": event["node_id"],
        **_summary_attrs(event.get("summary")),
    }
    if event.get("turn_id"):
        attrs["harnesswalk.turn.id"] = event["turn_id"]
    if event.get("session_id"):
        attrs["gen_ai.conversation.id"] = event["session_id"]
    return {
        "name": f"harnesswalk.{event['type']}",
        "trace_id": event["trace_id"],
        "span_id": span_id_of(event["event_id"]),
        "parent_span_id": None,
        "start_ns": ts,
        "end_ns": ts,
        "attributes": attrs,
        "events": [],
        "status": "ERROR" if event.get("status") == "error" else "UNSET",
        "status_description": None,
    }


def to_readable(records: list[dict[str, Any]]) -> list[Any]:
    """Span records as OpenTelemetry SDK ReadableSpans (needs `opentelemetry-sdk`)."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import Event as OtelEvent
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

    from harnesswalk import __version__

    resource = Resource.create({"service.name": "harnesswalk", "service.version": __version__})
    scope = InstrumentationScope("harnesswalk", __version__)

    def context(trace_id: str, span_id: str) -> SpanContext:
        return SpanContext(
            int(trace_id, 16), int(span_id, 16), False, TraceFlags(TraceFlags.SAMPLED)
        )

    out = []
    for r in records:
        parent = context(r["trace_id"], r["parent_span_id"]) if r["parent_span_id"] else None
        code = {"ERROR": StatusCode.ERROR, "OK": StatusCode.OK}.get(r["status"], StatusCode.UNSET)
        out.append(
            ReadableSpan(
                name=r["name"],
                context=context(r["trace_id"], r["span_id"]),
                parent=parent,
                resource=resource,
                attributes=r["attributes"],
                events=[OtelEvent(e["name"], e["attributes"], e["ts_ns"]) for e in r["events"]],
                kind=SpanKind.INTERNAL,
                status=Status(code, r["status_description"]),
                start_time=r["start_ns"],
                end_time=r["end_ns"],
                instrumentation_scope=scope,
            )
        )
    return out


class OtelExporter:
    """Collects each turn's events from the bus and exports them when the turn ends."""

    def __init__(self, exporter: Any, bus: EventBus, ops: Any = None) -> None:
        self.exporter = exporter
        self._turns: dict[str, list[Event]] = {}
        self._queue: queue.Queue[list[dict[str, Any]] | None] = queue.Queue()
        self._unsubscribe = [bus.subscribe(self.on_event)]
        if ops is not None:
            self._unsubscribe.append(ops.subscribe(self.on_ops_event))
        self._worker = threading.Thread(target=self._run, name="harnesswalk-otel", daemon=True)
        self._worker.start()

    def on_event(self, event: Event) -> None:
        events = self._turns.setdefault(event["turn_id"], [])
        events.append(event)
        if event["type"] == "turn_end":
            self._queue.put(spans_from_events(self._turns.pop(event["turn_id"])))

    def on_ops_event(self, event: Event) -> None:
        self._queue.put([span_from_ops_event(event)])

    def _run(self) -> None:
        while True:
            records = self._queue.get()
            try:
                if records is None:
                    return
                if records:
                    self.exporter.export(to_readable(records))
            except Exception as exc:  # a collector that is down never fails a turn
                log.warning("OpenTelemetry export failed: %s: %s", type(exc).__name__, exc)
            finally:
                self._queue.task_done()

    def flush(self) -> None:
        """Wait until everything queued so far was handed to the exporter."""
        self._queue.join()

    def shutdown(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._queue.put(None)
        self._worker.join(5)
        with contextlib.suppress(Exception):
            self.exporter.shutdown()


def endpoint_of(settings: Any) -> str | None:
    raw = os.environ.get("HARNESSWALK_OTLP_ENDPOINT") or settings.get("trace.otel.endpoint", None)
    if not raw:
        return None
    raw = str(raw).rstrip("/")
    return raw if raw.endswith("/v1/traces") else raw + "/v1/traces"


def build_exporter(settings: Any, bus: EventBus, ops: Any = None) -> OtelExporter | None:
    """The exporter when an endpoint is set and the extra is installed, else None (a no-op)."""
    endpoint = endpoint_of(settings)
    if endpoint is None:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        log.warning(
            "HARNESSWALK_OTLP_ENDPOINT is set but the otel extra is not installed"
            " (uv sync --extra otel); nothing is exported"
        )
        return None
    return OtelExporter(OTLPSpanExporter(endpoint=endpoint), bus, ops)
