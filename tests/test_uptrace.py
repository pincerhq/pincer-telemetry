"""UptraceProvider: DSN parsing, transport selection, headers, compression and temporality."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import grpc
import pytest
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.sdk.metrics import Counter, Histogram, ObservableCounter, UpDownCounter
from opentelemetry.sdk.metrics.export import AggregationTemporality

from pincer_telemetry import DEFAULT_COLLECTORS, Collector, SignalExporters, UptraceProvider
from pincer_telemetry.providers.uptrace import _parse_dsn

_GRPC_EXPORTERS = "pincer_telemetry.providers.uptrace._make_grpc_exporters"
_HTTP_EXPORTERS = "pincer_telemetry.providers.uptrace._make_http_exporters"

_HTTP_PKG = "opentelemetry.exporter.otlp.proto.http"
_GRPC_PKG = "opentelemetry.exporter.otlp.proto.grpc"

# DSN with ?grpc= triggers the gRPC path; DSN without it goes straight to HTTP.
_GRPC_DSN = "https://tok@api.uptrace.dev?grpc=4317"
_HTTP_DSN = "https://tok@api.uptrace.dev"


# ---------------------------------------------------------------------------
# _parse_dsn
# ---------------------------------------------------------------------------


def test_parse_dsn_basic_https() -> None:
    http_base, grpc_endpoint, insecure = _parse_dsn("https://otlp.uptrace.dev")
    assert http_base == "https://otlp.uptrace.dev:443"
    assert grpc_endpoint is None
    assert insecure is False


def test_parse_dsn_http_scheme_is_insecure() -> None:
    _, _, insecure = _parse_dsn("http://otlp.example.com")
    assert insecure is True


def test_parse_dsn_credentials_stripped_from_http_base() -> None:
    http_base, grpc_endpoint, _ = _parse_dsn("https://mytoken@otlp.uptrace.dev")
    assert "mytoken" not in http_base
    assert http_base == "https://otlp.uptrace.dev:443"
    assert grpc_endpoint is None


def test_parse_dsn_non_standard_port_kept_in_http_base() -> None:
    http_base, _, _ = _parse_dsn("https://otlp.example.com:4318")
    assert http_base == "https://otlp.example.com:4318"


def test_parse_dsn_explicit_https_port_in_http_base() -> None:
    http_base, _, _ = _parse_dsn("https://otlp.example.com:443")
    assert http_base == "https://otlp.example.com:443"


def test_parse_dsn_http_base_default_https_port() -> None:
    http_base, _, _ = _parse_dsn("https://otlp.example.com")
    assert http_base == "https://otlp.example.com:443"


def test_parse_dsn_http_base_default_http_port() -> None:
    http_base, _, _ = _parse_dsn("http://otlp.example.com")
    assert http_base == "http://otlp.example.com:80"


def test_parse_dsn_grpc_endpoint_from_query_param() -> None:
    _, grpc_endpoint, _ = _parse_dsn("https://otlp.example.com?grpc=4317")
    assert grpc_endpoint == "otlp.example.com:4317"


def test_parse_dsn_grpc_endpoint_custom_port_from_query_param() -> None:
    _, grpc_endpoint, _ = _parse_dsn("https://otlp.example.com?grpc=9000")
    assert grpc_endpoint == "otlp.example.com:9000"


def test_parse_dsn_no_grpc_query_param_returns_none() -> None:
    _, grpc_endpoint, _ = _parse_dsn("https://otlp.example.com")
    assert grpc_endpoint is None


def test_parse_dsn_empty_grpc_query_param_returns_none() -> None:
    _, grpc_endpoint, _ = _parse_dsn("https://otlp.example.com?grpc=")
    assert grpc_endpoint is None


def test_parse_dsn_grpc_endpoint_has_no_scheme() -> None:
    _, grpc_endpoint, _ = _parse_dsn("https://otlp.example.com?grpc=4317")
    assert grpc_endpoint is not None
    assert "://" not in grpc_endpoint


# ---------------------------------------------------------------------------
# Transport selection
# ---------------------------------------------------------------------------


def test_grpc_used_when_dsn_has_grpc_param() -> None:
    with (
        patch(_GRPC_EXPORTERS, return_value=SignalExporters()) as mock_grpc,
        patch(_HTTP_EXPORTERS, return_value=SignalExporters()) as mock_http,
    ):
        UptraceProvider(_GRPC_DSN).exporters(DEFAULT_COLLECTORS)

    mock_grpc.assert_called_once()
    mock_http.assert_not_called()
    kw = mock_grpc.call_args.kwargs
    assert kw["grpc_endpoint"] == "api.uptrace.dev:4317"
    assert kw["insecure"] is False
    assert kw["options"].collectors == DEFAULT_COLLECTORS


def test_grpc_insecure_true_for_http_scheme() -> None:
    with patch(_GRPC_EXPORTERS, return_value=SignalExporters()) as mock_grpc:
        UptraceProvider("http://tok@localhost?grpc=4317").exporters(DEFAULT_COLLECTORS)
    assert mock_grpc.call_args.kwargs["insecure"] is True


def test_http_fallback_when_grpc_unavailable() -> None:
    with (
        patch(_GRPC_EXPORTERS, side_effect=ImportError("no grpc")),
        patch(_HTTP_EXPORTERS, return_value=SignalExporters()) as mock_http,
    ):
        UptraceProvider(_GRPC_DSN).exporters(DEFAULT_COLLECTORS)

    mock_http.assert_called_once()
    assert mock_http.call_args.kwargs["http_base"] == "https://api.uptrace.dev:443"


def test_http_used_directly_when_no_grpc_param() -> None:
    with (
        patch(_GRPC_EXPORTERS) as mock_grpc,
        patch(_HTTP_EXPORTERS, return_value=SignalExporters()) as mock_http,
    ):
        UptraceProvider(_HTTP_DSN).exporters(DEFAULT_COLLECTORS)

    mock_grpc.assert_not_called()
    mock_http.assert_called_once()
    assert "/v1/" not in mock_http.call_args.kwargs["http_base"]


@pytest.mark.parametrize("target", [_GRPC_EXPORTERS, _HTTP_EXPORTERS])
def test_dsn_sent_as_uptrace_dsn_header(target: str) -> None:
    dsn = _GRPC_DSN if target == _GRPC_EXPORTERS else _HTTP_DSN
    with patch(target, return_value=SignalExporters()) as mock_make:
        UptraceProvider(dsn).exporters(DEFAULT_COLLECTORS)
    assert mock_make.call_args.kwargs["options"].headers == {"uptrace-dsn": dsn}


def test_compression_flag_forwarded() -> None:
    with patch(_HTTP_EXPORTERS, return_value=SignalExporters()) as mock_http:
        UptraceProvider(_HTTP_DSN, compression=False).exporters(DEFAULT_COLLECTORS)
    assert mock_http.call_args.kwargs["options"].compression is False


# ---------------------------------------------------------------------------
# Real exporter construction: endpoints, gzip, DELTA temporality, subsets
# ---------------------------------------------------------------------------


def _expected_temporality() -> dict[type, AggregationTemporality]:
    return {
        Counter: AggregationTemporality.DELTA,
        Histogram: AggregationTemporality.DELTA,
        ObservableCounter: AggregationTemporality.DELTA,
    }


def _patch_exporters(pkg: str) -> tuple[Any, Any, Any]:
    return (
        patch(f"{pkg}.trace_exporter.OTLPSpanExporter"),
        patch(f"{pkg}.metric_exporter.OTLPMetricExporter"),
        patch(f"{pkg}._log_exporter.OTLPLogExporter"),
    )


def test_http_exporters_kwargs() -> None:
    ps, pm, pl = _patch_exporters(_HTTP_PKG)
    with ps as span, pm as metric, pl as log:
        UptraceProvider(_HTTP_DSN).exporters(DEFAULT_COLLECTORS)

    headers = {"uptrace-dsn": _HTTP_DSN}
    span.assert_called_once_with(
        endpoint="https://api.uptrace.dev:443/v1/traces", headers=headers, compression=Compression.Gzip
    )
    log.assert_called_once_with(
        endpoint="https://api.uptrace.dev:443/v1/logs", headers=headers, compression=Compression.Gzip
    )
    metric.assert_called_once_with(
        endpoint="https://api.uptrace.dev:443/v1/metrics",
        headers=headers,
        compression=Compression.Gzip,
        preferred_temporality=_expected_temporality(),
    )
    assert UpDownCounter not in metric.call_args.kwargs["preferred_temporality"]


def test_http_exporters_no_compression() -> None:
    ps, pm, pl = _patch_exporters(_HTTP_PKG)
    with ps as span, pm, pl:
        UptraceProvider(_HTTP_DSN, compression=False).exporters(frozenset({Collector.TRACES}))
    assert span.call_args.kwargs["compression"] is Compression.NoCompression


def test_grpc_exporters_kwargs() -> None:
    ps, pm, pl = _patch_exporters(_GRPC_PKG)
    with ps as span, pm as metric, pl as log:
        UptraceProvider(_GRPC_DSN).exporters(DEFAULT_COLLECTORS)

    common = {
        "endpoint": "api.uptrace.dev:4317",
        "headers": {"uptrace-dsn": _GRPC_DSN},
        "insecure": False,
        "compression": grpc.Compression.Gzip,
    }
    span.assert_called_once_with(**common)
    log.assert_called_once_with(**common)
    metric.assert_called_once_with(**common, preferred_temporality=_expected_temporality())


def test_only_requested_exporters_built() -> None:
    ps, pm, pl = _patch_exporters(_HTTP_PKG)
    with ps as span, pm as metric, pl as log:
        result = UptraceProvider(_HTTP_DSN).exporters(frozenset({Collector.LOGS}))

    span.assert_not_called()
    metric.assert_not_called()
    log.assert_called_once()
    assert result.span is None
    assert result.metric is None
    assert result.log is log.return_value


# ---------------------------------------------------------------------------
# Tuning, lifecycle, repr
# ---------------------------------------------------------------------------


def test_tuning_matches_uptrace_recommendations() -> None:
    t = UptraceProvider(_HTTP_DSN).tuning()
    assert t.max_queue_size == 1000
    assert t.max_export_batch_size == 1000
    assert t.export_interval_millis == 60_000


async def test_lifecycle_tracks_started() -> None:
    p = UptraceProvider(_HTTP_DSN)
    p.start_sync()
    assert p.started
    p.flush_sync()
    p.stop_sync()
    assert not p.started
    await p.start_async()
    assert p.started
    await p.flush_async()
    await p.stop_async()
    assert not p.started


def test_repr_hides_dsn() -> None:
    assert "tok" not in repr(UptraceProvider(_HTTP_DSN))


def test_empty_dsn_rejected() -> None:
    with pytest.raises(ValueError):
        UptraceProvider("")
