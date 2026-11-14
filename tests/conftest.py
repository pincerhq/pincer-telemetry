"""Shared fixtures: isolate OTel globals, the init-once slot and atexit."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pincer_telemetry import Collector, OtelTuning, SignalExporters
from pincer_telemetry._api import _reset_for_tests

if TYPE_CHECKING:
    from collections.abc import Iterator

SET_TRACER = "opentelemetry.trace.set_tracer_provider"
SET_METER = "opentelemetry.metrics.set_meter_provider"
SET_LOGGER = "opentelemetry._logs.set_logger_provider"


class FakeMetricExporter(MetricExporter):
    def export(self, metrics_data: Any, timeout_millis: float = 10_000, **kwargs: Any) -> MetricExportResult:
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        return None


class FakeProvider:
    """A provider with in-memory exporters and recorded lifecycle calls."""

    name = "fake"

    def __init__(self, *, fail_start: bool = False) -> None:
        self.started = False
        self.fail_start = fail_start
        self.calls: list[str] = []
        self.requested: frozenset[Collector] | None = None
        self.built: SignalExporters | None = None

    def exporters(self, collectors: frozenset[Collector]) -> SignalExporters:
        self.requested = collectors
        self.built = SignalExporters.for_collectors(
            collectors, span=InMemorySpanExporter, metric=FakeMetricExporter, log=InMemoryLogRecordExporter
        )
        return self.built

    def tuning(self) -> OtelTuning:
        return OtelTuning()

    def start_sync(self) -> None:
        self.calls.append("start_sync")
        if self.fail_start:
            raise RuntimeError("boom")
        self.started = True

    async def start_async(self) -> None:
        self.calls.append("start_async")
        if self.fail_start:
            raise RuntimeError("boom")
        self.started = True

    def flush_sync(self) -> None:
        self.calls.append("flush_sync")

    async def flush_async(self) -> None:
        self.calls.append("flush_async")

    def stop_sync(self) -> None:
        self.calls.append("stop_sync")
        self.started = False

    async def stop_async(self) -> None:
        self.calls.append("stop_async")
        self.started = False


class OtelGlobals:
    def __init__(self, tracer: MagicMock, meter: MagicMock, logger: MagicMock, atexit: MagicMock) -> None:
        self.set_tracer = tracer
        self.set_meter = meter
        self.set_logger = logger
        self.atexit = atexit


@pytest.fixture(autouse=True)
def otel_globals() -> Iterator[OtelGlobals]:
    root_handlers = list(logging.getLogger().handlers)
    _reset_for_tests()
    with (
        patch(SET_TRACER) as mt,
        patch(SET_METER) as mm,
        patch(SET_LOGGER) as ml,
        patch("pincer_telemetry._api.atexit") as mock_atexit,
    ):
        yield OtelGlobals(mt, mm, ml, mock_atexit)
        # Run whatever would have run at exit so background export threads stop.
        for call in mock_atexit.register.call_args_list:
            call.args[0]()
    _reset_for_tests()
    logging.getLogger().handlers[:] = root_handlers


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()
