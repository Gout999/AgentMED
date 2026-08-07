"""OTel 请求级 trace。OTLP 端点可配；无 collector 时降级 no-op（span 照常建，仅不导出）。

trace_id 会写入 /logs 的 LogEntry.trace_id，供控制面关联。
"""
from __future__ import annotations

import os
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from app.config import get_settings

_tracer: Optional[trace.Tracer] = None


def _setup() -> trace.Tracer:
    settings = get_settings()
    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})

    endpoint = (settings.otel_exporter_otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    provider = TracerProvider(resource=resource)
    if endpoint:
        try:
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"))
            )
        except Exception:  # noqa: BLE001 —— OTLP 配置失败不阻塞业务
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        # 降级：内存导出器不读取（相当于 no-op 收集，span 上下文仍可用）
        provider.add_span_processor(BatchSpanProcessor(InMemorySpanExporter()))

    trace.set_tracer_provider(provider)
    return trace.get_tracer("demo-app-xiaozhi")


def get_tracer() -> trace.Tracer:
    global _tracer
    if _tracer is None:
        _tracer = _setup()
    return _tracer


def current_trace_id() -> str:
    """当前 span 的 trace_id（hex）；无活动 span 时生成短 id。"""
    try:
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            return format(span.get_span_context().trace_id, "032x")
    except Exception:  # noqa: BLE001
        pass
    from app.ids import new_trace_id

    return new_trace_id()
