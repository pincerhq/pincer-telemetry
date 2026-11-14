"""Build, install and tear down the OpenTelemetry SDK providers."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pincer_telemetry._types import Collector

if TYPE_CHECKING:
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    from pincer_telemetry._types import OtelTuning, SignalExporters


@dataclass(eq=False)
class OtelProviders:
    """The OTel SDK providers built for one ``init`` call."""

    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    logger_provider: LoggerProvider | None = None
    log_handler: logging.Handler | None = None
    _closed: bool = field(default=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _built(self) -> list[Any]:
        return [p for p in (self.tracer_provider, self.meter_provider, self.logger_provider) if p is not None]

    def install(self) -> None:
        """Register the built providers as the OTel globals and attach the logging bridge."""
        from opentelemetry import _logs, metrics, trace

        if self.tracer_provider is not None:
            trace.set_tracer_provider(self.tracer_provider)
        if self.meter_provider is not None:
            metrics.set_meter_provider(self.meter_provider)
        if self.logger_provider is not None:
            _logs.set_logger_provider(self.logger_provider)
            from opentelemetry.instrumentation.logging.handler import LoggingHandler

            self.log_handler = LoggingHandler(level=logging.NOTSET, logger_provider=self.logger_provider)
            logging.getLogger().addHandler(self.log_handler)

    def flush(self) -> None:
        """Force-flush every built provider (blocking)."""
        if self._closed:
            return
        for p in self._built():
            p.force_flush()

    def shutdown(self) -> None:
        """Detach the logging bridge and shut down every built provider. Safe to call repeatedly."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler = None
        for p in self._built():
            p.shutdown()


def build_otel(
    resource: Resource,
    exporters: SignalExporters,
    collectors: frozenset[Collector],
    tuning: OtelTuning,
) -> OtelProviders:
    """
    Build SDK providers for the selected collectors, without touching any global state.

    Call :meth:`OtelProviders.install` afterwards to make them the process-wide providers.
    """
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    batch_kw: dict[str, Any] = {}
    if tuning.max_queue_size is not None:
        batch_kw["max_queue_size"] = tuning.max_queue_size
    if tuning.max_export_batch_size is not None:
        batch_kw["max_export_batch_size"] = tuning.max_export_batch_size

    pairs = {
        Collector.TRACES: exporters.span,
        Collector.METRICS: exporters.metric,
        Collector.LOGS: exporters.log,
    }
    missing = sorted(c.value for c in collectors if pairs[c] is None)
    if missing:
        raise ValueError(f"provider supplied no exporter for collector(s): {', '.join(missing)}")

    out = OtelProviders()
    try:
        if Collector.TRACES in collectors:
            out.tracer_provider = TracerProvider(resource=resource)
            out.tracer_provider.add_span_processor(BatchSpanProcessor(exporters.span, **batch_kw))
        if Collector.METRICS in collectors:
            reader_kw: dict[str, Any] = {}
            if tuning.export_interval_millis is not None:
                reader_kw["export_interval_millis"] = tuning.export_interval_millis
            out.meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[PeriodicExportingMetricReader(exporters.metric, **reader_kw)],
            )
        if Collector.LOGS in collectors:
            out.logger_provider = LoggerProvider(resource=resource)
            out.logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporters.log, **batch_kw))
    except BaseException:
        out.shutdown()
        raise
    return out
