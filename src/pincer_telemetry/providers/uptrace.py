"""Uptrace provider — plain OpenTelemetry OTLP export, gRPC preferred with HTTP fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import parse_qs, urlparse

from pincer_telemetry._types import OtelTuning, SignalExporters
from pincer_telemetry.providers.base import ClientlessProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pincer_telemetry._types import Collector

_log = logging.getLogger(__name__)


class _ParsedDsn(NamedTuple):
    """The parts of an Uptrace DSN needed to configure OTLP exporters (see :func:`_parse_dsn`)."""

    http_base: str
    grpc_endpoint: str | None
    insecure: bool


@dataclass(frozen=True)
class _ExportOptions:
    """Settings shared by the gRPC and HTTP exporter builders."""

    headers: Mapping[str, str]
    collectors: frozenset[Collector]
    compression: bool


def _parse_dsn(dsn_url: str) -> _ParsedDsn:
    """
    Decompose a DSN URL into the parts needed to configure OTLP exporters.

    The token may be embedded as URL credentials
    (e.g. ``https://mytoken@api.uptrace.dev``).

    Parameters
    ----------
    dsn_url:
        Full DSN URL, optionally containing credentials in the ``user@`` position.

    Returns
    -------
    http_base:
        ``scheme://host:port`` string (credentials stripped) used as the
        prefix for HTTP signal endpoints.  Port defaults to ``80`` for
        ``http`` and ``443`` for ``https`` when not specified in the URL.
    grpc_endpoint:
        ``host:port`` string for the gRPC channel, sourced from the ``grpc``
        query parameter (e.g. ``?grpc=4317``).  ``None`` when the parameter is
        absent or empty — callers should fall back to HTTP in that case.
    insecure:
        ``True`` when the scheme is ``http`` — uses a plain-text gRPC channel
        instead of TLS.
    """
    parsed = urlparse(dsn_url)
    host = parsed.hostname or ""
    scheme = parsed.scheme or "https"
    insecure = scheme == "http"

    # HTTP base: credentials stripped, port always explicit.
    port = parsed.port or (80 if insecure else 443)
    http_base = f"{scheme}://{host}:{port}"

    # gRPC endpoint: derived from the `grpc` query param; None when absent/empty.
    grpc_port_values = parse_qs(parsed.query).get("grpc", [])
    grpc_port = grpc_port_values[0].strip() if grpc_port_values else ""
    grpc_endpoint: str | None = f"{host}:{grpc_port}" if grpc_port else None

    return _ParsedDsn(http_base, grpc_endpoint, insecure)


def _delta_temporality() -> dict[type, Any]:
    """Uptrace-recommended metric temporality: DELTA for counters and histograms."""
    from opentelemetry.sdk.metrics import Counter, Histogram, ObservableCounter
    from opentelemetry.sdk.metrics.export import AggregationTemporality

    return {
        Counter: AggregationTemporality.DELTA,
        Histogram: AggregationTemporality.DELTA,
        ObservableCounter: AggregationTemporality.DELTA,
    }


def _make_grpc_exporters(*, grpc_endpoint: str, insecure: bool, options: _ExportOptions) -> SignalExporters:
    """
    Create gRPC OTLP exporters for the requested collectors.

    Raises
    ------
    ImportError
        When ``opentelemetry-exporter-otlp-proto-grpc`` (or ``grpc``) is not installed.
    """
    import grpc
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    kw: dict[str, Any] = {
        "endpoint": grpc_endpoint,
        "headers": dict(options.headers),
        "insecure": insecure,
        "compression": grpc.Compression.Gzip if options.compression else grpc.Compression.NoCompression,
    }
    return SignalExporters.for_collectors(
        options.collectors,
        span=lambda: OTLPSpanExporter(**kw),
        metric=lambda: OTLPMetricExporter(**kw, preferred_temporality=_delta_temporality()),
        log=lambda: OTLPLogExporter(**kw),
    )


def _make_http_exporters(*, http_base: str, options: _ExportOptions) -> SignalExporters:
    """Create OTLP/HTTP (protobuf) exporters for the requested collectors."""
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    kw: dict[str, Any] = {
        "headers": dict(options.headers),
        "compression": Compression.Gzip if options.compression else Compression.NoCompression,
    }
    return SignalExporters.for_collectors(
        options.collectors,
        span=lambda: OTLPSpanExporter(endpoint=f"{http_base}/v1/traces", **kw),
        metric=lambda: OTLPMetricExporter(
            endpoint=f"{http_base}/v1/metrics", **kw, preferred_temporality=_delta_temporality()
        ),
        log=lambda: OTLPLogExporter(endpoint=f"{http_base}/v1/logs", **kw),
    )


class UptraceProvider(ClientlessProvider):
    """
    Export to Uptrace (or any OTLP collector that accepts the ``uptrace-dsn`` header).

    gRPC is used when the DSN carries ``?grpc=<port>`` and the gRPC exporter is
    importable; otherwise OTLP/HTTP. Uptrace has no vendor client, so the
    lifecycle methods come from :class:`ClientlessProvider` and only track state.

    Parameters
    ----------
    dsn:
        Uptrace DSN, e.g. ``https://<token>@api.uptrace.dev?grpc=4317``. Sent
        verbatim as the ``uptrace-dsn`` header.
    compression:
        gzip-compress export payloads (Uptrace recommends this).
    """

    name = "uptrace"

    def __init__(self, dsn: str, *, compression: bool = True) -> None:
        if not dsn:
            raise ValueError("UptraceProvider requires a non-empty dsn")
        super().__init__()
        self.dsn = dsn
        self.compression = compression

    def __repr__(self) -> str:
        # Never leak the DSN token into logs/reprs.
        return f"UptraceProvider(compression={self.compression!r}, started={self.started!r})"

    def exporters(self, collectors: frozenset[Collector]) -> SignalExporters:
        options = _ExportOptions(headers={"uptrace-dsn": self.dsn}, collectors=collectors, compression=self.compression)
        dsn = _parse_dsn(self.dsn)
        if dsn.grpc_endpoint is not None:
            try:
                exporters = _make_grpc_exporters(
                    grpc_endpoint=dsn.grpc_endpoint, insecure=dsn.insecure, options=options
                )
                _log.debug("Uptrace transport: gRPC")
                return exporters
            except ImportError:
                _log.debug("gRPC exporter unavailable, falling back to OTLP/HTTP")
        _log.debug("Uptrace transport: HTTP")
        return _make_http_exporters(http_base=dsn.http_base, options=options)

    def tuning(self) -> OtelTuning:
        return OtelTuning(max_queue_size=1000, max_export_batch_size=1000, export_interval_millis=60_000)
