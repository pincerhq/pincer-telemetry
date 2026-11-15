"""PostHogProvider: OTLP endpoints, client lifecycle and the exception handler."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.exporter.otlp.proto.http import Compression

from pincer_telemetry import DEFAULT_COLLECTORS, Collector, PostHogProvider, TelemetryConfigError
from pincer_telemetry.providers.posthog import _PostHogExceptionHandler

if TYPE_CHECKING:
    from collections.abc import Iterator

_HTTP_PKG = "opentelemetry.exporter.otlp.proto.http"
_US = "https://phc_x@us.i.posthog.com"
_EU = "https://phc_x@eu.i.posthog.com"


@pytest.fixture
def sync_client_cls() -> Iterator[MagicMock]:
    with patch("posthog.Posthog") as cls:
        yield cls


@pytest.fixture
def async_client_cls() -> Iterator[MagicMock]:
    with patch("posthog.AsyncPosthog") as cls:
        cls.return_value.flush = AsyncMock()
        cls.return_value.shutdown = AsyncMock()
        yield cls


def _handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, _PostHogExceptionHandler)]


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dsn", "base"),
    [
        ("https://phc_test@us.i.posthog.com", "https://us.i.posthog.com"),
        ("https://phc_test@eu.i.posthog.com/", "https://eu.i.posthog.com"),
    ],
)
def test_exporter_endpoints_and_bearer_header(dsn: str, base: str) -> None:
    with (
        patch(f"{_HTTP_PKG}.trace_exporter.OTLPSpanExporter") as span,
        patch(f"{_HTTP_PKG}.metric_exporter.OTLPMetricExporter") as metric,
        patch(f"{_HTTP_PKG}._log_exporter.OTLPLogExporter") as log,
    ):
        PostHogProvider(dsn).exporters(DEFAULT_COLLECTORS)

    for mock, signal in ((span, "traces"), (metric, "metrics"), (log, "logs")):
        mock.assert_called_once_with(
            endpoint=f"{base}/i/v1/{signal}",
            headers={"Authorization": "Bearer phc_test"},
            compression=Compression.Gzip,
        )


def test_only_requested_exporters_built() -> None:
    result = PostHogProvider(_US).exporters(frozenset({Collector.TRACES}))
    assert result.span is not None
    assert result.metric is None
    assert result.log is None
    result.span.shutdown()


def test_tuning_uses_sdk_defaults() -> None:
    t = PostHogProvider(_US).tuning()
    assert t.max_queue_size is None
    assert t.max_export_batch_size is None
    assert t.export_interval_millis is None


def test_empty_dsn_rejected() -> None:
    with pytest.raises(ValueError):  # TelemetryConfigError is a ValueError
        PostHogProvider("")


def test_repr_hides_api_key() -> None:
    assert "phc_secret" not in repr(PostHogProvider("https://phc_secret@us.i.posthog.com"))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_client_before_start_raises() -> None:
    with pytest.raises(RuntimeError, match="not started"):
        _ = PostHogProvider(_US).client


def test_start_sync_builds_posthog_with_autocapture(sync_client_cls: MagicMock) -> None:
    p = PostHogProvider(_EU, client_options={"debug": True})
    p.start_sync()

    sync_client_cls.assert_called_once_with(
        "phc_x", host="https://eu.i.posthog.com", enable_exception_autocapture=True, debug=True
    )
    assert p.client is sync_client_cls.return_value
    assert p.started
    assert len(_handlers()) == 1
    p.stop_sync()


def test_start_sync_without_exception_capture(sync_client_cls: MagicMock) -> None:
    p = PostHogProvider(_US, capture_exceptions=False)
    p.start_sync()
    assert sync_client_cls.call_args.kwargs["enable_exception_autocapture"] is False
    assert _handlers() == []
    p.stop_sync()


def test_stop_sync_shuts_down_and_removes_handler(sync_client_cls: MagicMock) -> None:
    p = PostHogProvider(_US)
    p.start_sync()
    p.flush_sync()
    sync_client_cls.return_value.flush.assert_called_once_with()

    p.stop_sync()

    sync_client_cls.return_value.shutdown.assert_called_once_with()
    assert _handlers() == []
    assert not p.started
    with pytest.raises(RuntimeError):
        _ = p.client


async def test_start_async_builds_async_posthog(async_client_cls: MagicMock) -> None:
    p = PostHogProvider(_EU)
    await p.start_async()

    async_client_cls.assert_called_once_with("phc_x", host="https://eu.i.posthog.com")
    assert p.client is async_client_cls.return_value
    assert len(_handlers()) == 1

    await p.flush_async()
    async_client_cls.return_value.flush.assert_awaited_once()

    await p.stop_async()
    async_client_cls.return_value.shutdown.assert_awaited_once()
    assert _handlers() == []
    assert not p.started


def test_flush_and_stop_before_start_are_noops() -> None:
    p = PostHogProvider(_US)
    p.flush_sync()
    p.stop_sync()
    assert not p.started


# ---------------------------------------------------------------------------
# Exception handler
# ---------------------------------------------------------------------------


def test_handler_forwards_only_records_with_exc_info() -> None:
    client = MagicMock()
    handler = _PostHogExceptionHandler(client)
    log = logging.getLogger("tests.posthog.handler")
    log.addHandler(handler)
    log.propagate = False
    try:
        log.error("no exception here")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")
        log.warning("not an error", exc_info=ValueError("ignored"))
    finally:
        log.removeHandler(handler)
        log.propagate = True

    client.capture_exception.assert_called_once()
    exc = client.capture_exception.call_args.args[0]
    assert isinstance(exc, ValueError)
    assert str(exc) == "boom"


def test_handler_ignores_posthog_own_loggers() -> None:
    client = MagicMock()
    handler = _PostHogExceptionHandler(client)
    record = logging.LogRecord("posthog", logging.ERROR, __file__, 1, "upload failed", None, None)
    try:
        raise RuntimeError("x")
    except RuntimeError:
        record.exc_info = sys.exc_info()
    handler.emit(record)
    client.capture_exception.assert_not_called()


def test_handler_swallows_client_errors(capsys: pytest.CaptureFixture[str]) -> None:
    client = MagicMock()
    client.capture_exception.side_effect = RuntimeError("client down")
    handler = _PostHogExceptionHandler(client)
    record = logging.LogRecord("app", logging.ERROR, __file__, 1, "msg", None, (ValueError, ValueError("v"), None))
    handler.emit(record)  # must not raise
    client.capture_exception.assert_called_once()


# ---------------------------------------------------------------------------
# DSN parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dsn", "host"),
    [
        ("https://phc_key@us.i.posthog.com", "https://us.i.posthog.com"),
        ("https://phc_key@eu.i.posthog.com/", "https://eu.i.posthog.com"),
        ("http://phc_key@127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://phc_key@[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_dsn_parsed(dsn: str, host: str) -> None:
    p = PostHogProvider(dsn)
    assert p.api_key == "phc_key"
    assert p.host == host
    assert p.dsn == dsn


def test_dsn_key_is_percent_decoded() -> None:
    assert PostHogProvider("https://phc%2Bkey@us.i.posthog.com").api_key == "phc+key"


@pytest.mark.parametrize(
    ("dsn", "match"),
    [
        ("https://us.i.posthog.com", "API key"),
        ("us.i.posthog.com", "scheme"),
        ("ftp://phc_key@us.i.posthog.com", "scheme"),
        ("https://phc_key@", "host"),
        ("https://phc_key:pw@us.i.posthog.com", "password"),
        ("https://phc_key@us.i.posthog.com/i/v1", "path"),
        ("https://phc_key@us.i.posthog.com?x=1", "query"),
        ("https://phc_key@us.i.posthog.com:notaport", "valid URL"),
    ],
)
def test_dsn_malformed(dsn: str, match: str) -> None:
    with pytest.raises(TelemetryConfigError, match=match) as exc:
        PostHogProvider(dsn)
    assert "phc_key" not in str(exc.value)
