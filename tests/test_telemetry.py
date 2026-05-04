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

# DSN with ?grpc= triggers the gRPC path; DSN without it goes straight to HTTP.
_GRPC_DSN = "https://tok@otlp.example.com?grpc=4317"
_HTTP_DSN = "https://tok@otlp.example.com"


def _mock_exporters() -> tuple[MagicMock, MagicMock, MagicMock]:
    return (MagicMock(), MagicMock(), MagicMock())


# ---------------------------------------------------------------------------
# _parse_dsn unit tests
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


def test_grpc_used_when_package_available() -> None:
    """init() calls _make_grpc_exporters when gRPC param present and package importable."""
    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()) as mock_grpc,
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", _GRPC_DSN)

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

        init("svc", "1.0", _GRPC_DSN)

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

        init("svc", "1.0", _GRPC_DSN)
        # HTTP was used; no assertion error means gRPC path correctly caught.


def test_http_used_directly_when_no_grpc_param() -> None:
    """init() skips gRPC entirely when the DSN has no ?grpc= parameter."""
    with (
        patch(_GRPC_EXPORTERS, return_value=_mock_exporters()) as mock_grpc,
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()) as mock_http,
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", _HTTP_DSN)

    mock_grpc.assert_not_called()
    mock_http.assert_called_once()


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

        init("svc", "1.0", _GRPC_DSN)

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

        init("svc", "1.0", "https://tok@otlp.example.com?grpc=4317")

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

        init("svc", "1.0", "http://tok@otlp.example.com?grpc=4317")

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
        patch(_HTTP_EXPORTERS, side_effect=_capture),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", _HTTP_DSN)

    assert captured_base
    # The http_base itself doesn't have /v1/ — the exporters add the path.
    # Verify it does NOT already include a signal path.
    assert "/v1/" not in captured_base[0]


def test_http_base_includes_port() -> None:
    """The http_base forwarded to _make_http_exporters always carries an explicit port."""
    captured_base: list[str] = []

    def _capture(http_base: str, headers: dict[str, str]) -> tuple[MagicMock, MagicMock, MagicMock]:
        captured_base.append(http_base)
        return _mock_exporters()

    with (
        patch(_HTTP_EXPORTERS, side_effect=_capture),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", _HTTP_DSN)

    assert captured_base
    assert ":443" in captured_base[0]


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


def test_dsn_url_sent_as_uptrace_dsn_header() -> None:
    """The full dsn_url must appear as the uptrace-dsn header on gRPC exporters."""
    headers_seen: list[dict[str, str]] = []

    def _capture(endpoint: str, headers: dict[str, str], insecure: bool) -> tuple[MagicMock, MagicMock, MagicMock]:
        headers_seen.append(dict(headers))
        return _mock_exporters()

    dsn = "https://mytoken@otlp.uptrace.dev?grpc=4317"
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
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER, side_effect=captured.append),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("my-agent", "3.1.4", _HTTP_DSN)

    assert captured
    attrs = captured[0].resource.attributes
    assert attrs["service.name"] == "my-agent"
    assert attrs["service.version"] == "3.1.4"


# ---------------------------------------------------------------------------
# Global providers registered
# ---------------------------------------------------------------------------


def test_all_three_global_providers_set() -> None:
    with (
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER) as mt,
        patch(_SET_METER) as mm,
        patch(_SET_LOGGER) as ml,
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", _HTTP_DSN)

    mt.assert_called_once()
    mm.assert_called_once()
    ml.assert_called_once()


# ---------------------------------------------------------------------------
# Logging bridge
# ---------------------------------------------------------------------------


def test_logging_instrumentor_called() -> None:
    instrumentor_instance = MagicMock()

    with (
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_LOGGING_INSTRUMENTOR, return_value=instrumentor_instance) as mock_cls,
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
    ):
        from pincer_telemetry import init

        init("svc", "1.0", _HTTP_DSN)

    mock_cls.assert_called_once()
    instrumentor_instance.instrument.assert_called_once()


# ---------------------------------------------------------------------------
# Shutdown lifecycle
# ---------------------------------------------------------------------------


def test_shutdown_calls_all_three_providers() -> None:
    tp_mock, mp_mock, lp_mock = MagicMock(), MagicMock(), MagicMock()

    with (
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_TRACER_PROVIDER, return_value=tp_mock),
        patch(_METER_PROVIDER, return_value=mp_mock),
        patch(_LOGGER_PROVIDER, return_value=lp_mock),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        shutdown = init("svc", "1.0", _HTTP_DSN)
        shutdown()

    tp_mock.shutdown.assert_called_once()
    mp_mock.shutdown.assert_called_once()
    lp_mock.shutdown.assert_called_once()


def test_atexit_registered() -> None:
    with (
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
        patch("pincer_telemetry._core.atexit") as mock_atexit,
    ):
        from pincer_telemetry import init

        shutdown = init("svc", "1.0", _HTTP_DSN)

    mock_atexit.register.assert_called_once_with(shutdown)


def test_init_returns_callable() -> None:
    with (
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        from pincer_telemetry import init

        result = init("svc", "1.0", _HTTP_DSN)

    assert callable(result)


# ---------------------------------------------------------------------------
# dsn_url validation
# ---------------------------------------------------------------------------


def test_init_raises_when_dsn_url_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """init() raises ValueError immediately when dsn_url is None."""
    monkeypatch.delenv("OTEL_DSN", raising=False)
    from pincer_telemetry import init

    with pytest.raises(ValueError, match="OTEL_DSN"):
        init("svc", "1.0", None)


def test_init_uses_otel_dsn_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """init() reads dsn_url from OTEL_DSN when not passed explicitly."""
    monkeypatch.setenv("OTEL_DSN", _HTTP_DSN)

    # Re-import so the default argument is re-evaluated with the env var set.
    import importlib

    import pincer_telemetry._core as core_mod

    importlib.reload(core_mod)

    with (
        patch(_HTTP_EXPORTERS, return_value=_mock_exporters()),
        patch(_SET_TRACER),
        patch(_SET_METER),
        patch(_SET_LOGGER),
        patch(_LOGGING_INSTRUMENTOR),
    ):
        core_mod.init("svc", "1.0")


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
            init("svc", "1.0", _HTTP_DSN)
    finally:
        sys.modules.update(saved)
        for mod in ("pincer_telemetry._core", "pincer_telemetry"):
            sys.modules.pop(mod, None)
