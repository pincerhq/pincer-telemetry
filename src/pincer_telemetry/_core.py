"""OpenTelemetry initialisation — gRPC (preferred) with OTLP/HTTP fallback."""

from __future__ import annotations

import atexit
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from collections.abc import Callable

_log = logging.getLogger(__name__)


def _parse_dsn(dsn_url: str) -> tuple[str, str | None, bool]:
    """
    Decompose a DSN URL into the parts needed to configure OTLP exporters.

    The token may be embedded as URL credentials
    (e.g. ``https://mytoken@otlp.uptrace.dev``).

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

    return http_base, grpc_endpoint, insecure


def _make_grpc_exporters(
    grpc_endpoint: str,
    headers: dict[str, str],
    insecure: bool,
) -> tuple[Any, Any, Any]:
    """
    Create gRPC OTLP exporters for traces, metrics, and logs.

    Raises
    ------
    ImportError
        When ``opentelemetry-exporter-otlp-proto-grpc`` is not installed.
    """
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    kw: dict[str, Any] = {"endpoint": grpc_endpoint, "headers": headers, "insecure": insecure}
    return (
        OTLPSpanExporter(**kw),
        OTLPMetricExporter(**kw),
        OTLPLogExporter(**kw),
    )


def _make_http_exporters(http_base: str, headers: dict[str, str]) -> tuple[Any, Any, Any]:
    """Create HTTP OTLP exporters for traces, metrics, and logs."""
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return (
        OTLPSpanExporter(endpoint=f"{http_base}/v1/traces", headers=headers),
        OTLPMetricExporter(endpoint=f"{http_base}/v1/metrics", headers=headers),
        OTLPLogExporter(endpoint=f"{http_base}/v1/logs", headers=headers),
    )


def init(
    project_name: str,
    version: str,
    dsn_url: str,
) -> Callable[[], None]:
    """
    Initialise OpenTelemetry traces, metrics, and logs.

    gRPC transport is attempted first (requires
    ``opentelemetry-exporter-otlp-proto-grpc``).  When the package is absent
    the exporter silently falls back to OTLP/HTTP.

    Configures the three global OTel providers (TracerProvider, MeterProvider,
    LoggerProvider) and bridges the stdlib ``logging`` root logger into OTel
    logs.  All export is asynchronous and never blocks the calling thread.

    Parameters
    ----------
    project_name:
        Value of the ``service.name`` resource attribute.
    version:
        Value of the ``service.version`` resource attribute.
    dsn_url:
        Full DSN URL of the OTLP ingest endpoint.  The token may be embedded
        as URL credentials (e.g. ``https://mytoken@otlp.uptrace.dev``).
        The entire URL is forwarded as the ``uptrace-dsn`` auth header.

    Returns
    -------
    Callable[[], None]
        Shutdown function that flushes and closes all exporters.  An ``atexit``
        hook is registered automatically; call it explicitly only when you need
        a guaranteed flush before process exit (e.g. in a SIGTERM handler).

    Raises
    ------
    ImportError
        When ``pincer-telemetry`` is not installed.
    """
    try:
        from opentelemetry import metrics, trace
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise ImportError(
            "OpenTelemetry packages are required for telemetry support. "
            'Install them with: pip install "pincer-telemetry"'
        ) from exc

    # Stdlib logging bridge — prefer instrumentation package, fall back to SDK handler.
    _instrument_logging: Callable[[Any], None]
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        def _do_instrument(lp: Any) -> None:
            LoggingInstrumentor().instrument(logger_provider=lp)  # type: ignore[no-untyped-call]

        _instrument_logging = _do_instrument
    except ImportError:
        from opentelemetry.sdk._logs import LoggingHandler  # type: ignore[assignment]

        def _do_fallback(lp: Any) -> None:
            logging.getLogger().addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=lp))

        _instrument_logging = _do_fallback

    resource = Resource.create(
        {
            "service.name": project_name,
            "service.version": version,
        }
    )
    headers: dict[str, str] = {"uptrace-dsn": dsn_url}
    http_base, grpc_endpoint, insecure = _parse_dsn(dsn_url)

    # gRPC preferred when a grpc query param is present; HTTP fallback otherwise
    # or when the gRPC package is not installed.
    transport = "gRPC"
    if grpc_endpoint is None:
        span_exp, metric_exp, log_exp = _make_http_exporters(http_base, headers)
        transport = "HTTP"
    else:
        try:
            span_exp, metric_exp, log_exp = _make_grpc_exporters(grpc_endpoint, headers, insecure)
        except ImportError:
            span_exp, metric_exp, log_exp = _make_http_exporters(http_base, headers)
            transport = "HTTP"

    _log.debug("OTel transport: %s", transport)

    # ── Traces ──────────────────────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exp))
    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ─────────────────────────────────────────────────────────────
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(metric_exp)],
    )
    metrics.set_meter_provider(meter_provider)

    # ── Logs ────────────────────────────────────────────────────────────────
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exp))
    set_logger_provider(logger_provider)
    _instrument_logging(logger_provider)

    def _shutdown() -> None:
        tracer_provider.shutdown()
        meter_provider.shutdown()
        logger_provider.shutdown()

    atexit.register(_shutdown)
    return _shutdown
