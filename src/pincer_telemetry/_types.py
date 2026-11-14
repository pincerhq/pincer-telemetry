"""Shared value types: collectors, per-signal exporters and OTel tuning knobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from opentelemetry.sdk._logs.export import LogRecordExporter
    from opentelemetry.sdk.metrics.export import MetricExporter
    from opentelemetry.sdk.trace.export import SpanExporter


class Collector(StrEnum):
    """An OpenTelemetry signal that ``init`` can collect and export."""

    TRACES = "traces"
    METRICS = "metrics"
    LOGS = "logs"


DEFAULT_COLLECTORS: frozenset[Collector] = frozenset(Collector)


@dataclass(frozen=True)
class SignalExporters:
    """Exporters a provider built for the requested collectors (``None`` = not requested)."""

    span: SpanExporter | None = None
    metric: MetricExporter | None = None
    log: LogRecordExporter | None = None

    @classmethod
    def for_collectors(
        cls,
        collectors: frozenset[Collector],
        *,
        span: Callable[[], SpanExporter],
        metric: Callable[[], MetricExporter],
        log: Callable[[], LogRecordExporter],
    ) -> SignalExporters:
        """Call only the factories whose collector was requested; the rest stay ``None``."""
        return cls(
            span=span() if Collector.TRACES in collectors else None,
            metric=metric() if Collector.METRICS in collectors else None,
            log=log() if Collector.LOGS in collectors else None,
        )


@dataclass(frozen=True)
class OtelTuning:
    """Processor/reader settings a provider recommends. ``None`` keeps the OTel SDK default."""

    max_queue_size: int | None = None
    max_export_batch_size: int | None = None
    export_interval_millis: float | None = None
