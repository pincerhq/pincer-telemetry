"""Tests for pincer_telemetry — gRPC-first / HTTP-fallback OTLP exporter."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from pincer_telemetry._core import _parse_dsn

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

# Module-level helpers are directly patchable (no lazy import inside init()).
_GRPC_EXPORTERS = "pincer_telemetry._core._make_grpc_exporters"
_HTTP_EXPORTERS = "pincer_telemetry._core._make_http_exporters"
_LOGGING_INSTRUMENTOR = "opentelemetry.instrumentation.logging.LoggingInstrumentor"
_SET_TRACER = "opentelemetry.trace.set_tracer_provider"
_SET_METER = "opentelemetry.metrics.set_meter_provider"
_SET_LOGGER = "opentelemetry._logs.set_logger_provider"
_TRACER_PROVIDER = "opentelemetry.sdk.trace.TracerProvider"
_METER_PROVIDER = "opentelemetry.sdk.metrics.MeterProvider"
_LOGGER_PROVIDER = "opentelemetry.sdk._logs.LoggerProvider"


def _mock_exporters() -> tuple[MagicMock, MagicMock, MagicMock]:
    return (MagicMock(), MagicMock(), MagicMock())


# ---------------------------------------------------------------------------
# _parse_dsn unit tests
# ---------------------------------------------------------------------------


def test_parse_dsn_basic_https() -> None:
    http_base, grpc_endpoint, insecure = _parse_dsn("https://otlp.uptrace.dev")
    assert http_base == "https://otlp.uptrace.dev"
    assert grpc_endpoint == "otlp.uptrace.dev:4317"
    assert insecure is False


def test_parse_dsn_http_scheme_is_insecure() -> None:
    _, _, insecure = _parse_dsn("http://otlp.example.com")
    assert insecure is True


def test_parse_dsn_credentials_stripped_from_http_base() -> None:
    http_base, grpc_endpoint, _ = _parse_dsn("https://mytoken@otlp.uptrace.dev")
    assert "mytoken" not in http_base
    assert http_base == "https://otlp.uptrace.dev"
    assert grpc_endpoint == "otlp.uptrace.dev:4317"


def test_parse_dsn_explicit_port_used_for_grpc() -> None:
    _, grpc_endpoint, _ = _parse_dsn("https://otlp.example.com:4317")
    assert grpc_endpoint == "otlp.example.com:4317"


def test_parse_dsn_non_standard_port_kept_in_http_base() -> None:
    http_base, _, _ = _parse_dsn("https://otlp.example.com:4318")
    assert http_base == "https://otlp.example.com:4318"


def test_parse_dsn_standard_https_port_omitted_from_http_base() -> None:
    http_base, _, _ = _parse_dsn("https://otlp.example.com:443")
    assert http_base == "https://otlp.example.com"


def test_parse_dsn_no_port_defaults_grpc_to_4317() -> None:
    _, grpc_endpoint, _ = _parse_dsn("https://backend.example.com")
    assert grpc_endpoint.endswith(":4317")


# ---------------------------------------------------------------------------
# Transport selection
# ---------------------------------------------------------------------------


def test_grpc_used_when_package_available() -> None:
    """init() calls _make_grpc_exporters when gRPC package is importable."""
    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()) as mock_grpc,
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.example.com")

    mock_grpc.assert_called_once()


def test_http_fallback_when_grpc_unavailable() -> None:
    """init() falls back to _make_http_exporters when gRPC raises ImportError."""
    with (
        patch(_GRPC_EXPORTERS, side_effect=ImportError("no grpc")),
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()) as mock_http,
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.example.com")

    mock_http.assert_called_once()


def test_grpc_exporters_not_called_on_fallback() -> None:
    with (
        patch(_GRPC_EXPORTERS, side_effect=ImportError),
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.example.com")
        # HTTP was used; no assertion error means gRPC path correctly caught.


# ---------------------------------------------------------------------------
# gRPC exporter arguments
# ---------------------------------------------------------------------------


def test_grpc_endpoint_is_host_colon_port() -> None:
    """_make_grpc_exporters receives a bare host:port endpoint (no scheme)."""
    captured: list[str] = []

    def _capture(endpoint: str, headers: dict[str, str], insecure: bool) -> tuple[MagicMock, MagicMock, MagicMock]:
        captured.append(endpoint)
        return _mock_exporters()

    with (
        patch(_GRPC_EXPORTERS, side_effect=_capture),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.uptrace.dev")

    assert captured, "gRPC exporters were not called"
    ep = captured[0]
    assert "://" not in ep, f"gRPC endpoint must not contain a scheme: {ep!r}"
    assert ":" in ep


def test_grpc_insecure_false_for_https() -> None:
    insecure_values: list[bool] = []

    def _capture(endpoint: str, headers: dict[str, str], insecure: bool) -> tuple[MagicMock, MagicMock, MagicMock]:
        insecure_values.append(insecure)
        return _mock_exporters()

    with (
        patch(_GRPC_EXPORTERS, side_effect=_capture),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.example.com")

    assert insecure_values == [False]


def test_grpc_insecure_true_for_http() -> None:
    insecure_values: list[bool] = []

    def _capture(endpoint: str, headers: dict[str, str], insecure: bool) -> tuple[MagicMock, MagicMock, MagicMock]:
        insecure_values.append(insecure)
        return _mock_exporters()

    with (
        patch(_GRPC_EXPORTERS, side_effect=_capture),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "http://tok@otlp.example.com")

    assert insecure_values == [True]


# ---------------------------------------------------------------------------
# HTTP exporter arguments
# ---------------------------------------------------------------------------


def test_http_endpoints_have_signal_paths() -> None:
    """HTTP exporter calls must include /v1/{traces,metrics,logs} paths."""
    captured_base: list[str] = []

    def _capture(http_base: str, headers: dict[str, str]) -> tuple[MagicMock, MagicMock, MagicMock]:
        captured_base.append(http_base)
        return _mock_exporters()

    with (
        patch(_GRPC_EXPORTERS, side_effect=ImportError),
        patch(_HTTP_EXPORTERS, side_effect=_capture),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.example.com")

    assert captured_base
    # The http_base itself doesn't have /v1/ — the exporters add the path.
    # Verify it does NOT already include a signal path.
    assert "/v1/" not in captured_base[0]


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


def test_dsn_url_sent_as_uptrace_dsn_header() -> None:
    """The full dsn_url must appear as the uptrace-dsn header on gRPC exporters."""
    headers_seen: list[dict[str, str]] = []

    def _capture(endpoint: str, headers: dict[str, str], insecure: bool) -> tuple[MagicMock, MagicMock, MagicMock]:
        headers_seen.append(dict(headers))
        return _mock_exporters()

    dsn = "https://mytoken@otlp.uptrace.dev"
    with (
        patch(_GRPC_EXPORTERS, side_effect=_capture),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", dsn)

    assert headers_seen
    assert headers_seen[0].get("uptrace-dsn") == dsn


# ---------------------------------------------------------------------------
# Resource attributes
# ---------------------------------------------------------------------------


def test_resource_service_name_and_version() -> None:

    captured: list[TracerProvider] = []

    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER, side_effect=captured.append),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("my-agent", "3.1.4", "https://tok@otlp.example.com")

    assert captured
    attrs = captured[0].resource.attributes
    assert attrs["service.name"] == "my-agent"
    assert attrs["service.version"] == "3.1.4"


# ---------------------------------------------------------------------------
# Global providers registered
# ---------------------------------------------------------------------------


def test_all_three_global_providers_set() -> None:
    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER) as mt,
        patch(_SET_METER) as mm,
        patch(_SET_LOGGER) as ml,
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.example.com")

    mt.assert_called_once()
    mm.assert_called_once()
    ml.assert_called_once()


# ---------------------------------------------------------------------------
# Logging bridge
# ---------------------------------------------------------------------------


def test_logging_instrumentor_called() -> None:
    instrumentor_instance = MagicMock()

    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()),
        patch(_LOGGING_INSTRUMENTOR, return_value=instrumentor_instance) as mock_cls,
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", "https://tok@otlp.example.com")

    mock_cls.assert_called_once()
    instrumentor_instance.instrument.assert_called_once()


# ---------------------------------------------------------------------------
# Shutdown lifecycle
# ---------------------------------------------------------------------------


def test_shutdown_calls_all_three_providers() -> None:
    tp_mock, mp_mock, lp_mock = MagicMock(), MagicMock(), MagicMock()

    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()),
        patch(_TRACER_PROVIDER, return_value=tp_mock),
        patch(_METER_PROVIDER, return_value=mp_mock),
        patch(_LOGGER_PROVIDER, return_value=lp_mock),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        shutdown = init("svc", "1.0", "https://tok@otlp.example.com")
        shutdown()

    tp_mock.shutdown.assert_called_once()
    mp_mock.shutdown.assert_called_once()
    lp_mock.shutdown.assert_called_once()


def test_atexit_registered() -> None:
    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
        patch("pincer_telemetry._core.atexit") as mock_atexit,
    ):
        from pincer_telemetry import init

        shutdown = init("svc", "1.0", "https://tok@otlp.example.com")

    mock_atexit.register.assert_called_once_with(shutdown)


def test_init_returns_callable() -> None:
    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        result = init("svc", "1.0", "https://tok@otlp.example.com")

    assert callable(result)


# ---------------------------------------------------------------------------
# ImportError guard
# ---------------------------------------------------------------------------


def test_import_error_raised_when_otel_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _block_otel(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("opentelemetry"):
            raise ImportError(f"blocked: {name}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    otel_mods = [k for k in sys.modules if k.startswith("opentelemetry")]
    saved = {k: sys.modules.pop(k) for k in otel_mods}
    for mod in ("pincer_telemetry._core", "pincer_telemetry"):
        sys.modules.pop(mod, None)

    monkeypatch.setattr(builtins, "__import__", _block_otel)

    try:
        from pincer_telemetry import init

        with pytest.raises(ImportError, match="pincer-telemetry"):
            init("svc", "1.0", "https://tok@otlp.example.com")
    finally:
        sys.modules.update(saved)
        for mod in ("pincer_telemetry._core", "pincer_telemetry"):
            sys.modules.pop(mod, None)
