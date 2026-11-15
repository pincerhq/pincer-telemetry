"""init_async(): async provider lifecycle and off-loop OTel flush/shutdown."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from conftest import FakeProvider, OtelGlobals

from pincer_telemetry import AsyncTelemetry, PostHogProvider, get_provider, init, init_async


async def test_init_async_awaits_start_async(fake_provider: FakeProvider) -> None:
    tel = await init_async(fake_provider, service_name="svc", version="1.0")  # type: ignore[arg-type]
    assert isinstance(tel, AsyncTelemetry)
    assert fake_provider.calls == ["start_async"]
    assert get_provider() is fake_provider
    await tel.shutdown()


async def test_atexit_registers_only_otel(fake_provider: FakeProvider, otel_globals: OtelGlobals) -> None:
    tel = await init_async(fake_provider, service_name="svc", version="1.0")  # type: ignore[arg-type]
    otel_globals.atexit.register.assert_called_once_with(tel._otel.shutdown)
    await tel.shutdown()
    otel_globals.atexit.unregister.assert_called_once_with(tel._otel.shutdown)


async def test_flush_and_shutdown_use_to_thread(fake_provider: FakeProvider) -> None:
    tel = await init_async(fake_provider, service_name="svc", version="1.0")  # type: ignore[arg-type]
    real_to_thread = asyncio.to_thread
    offloaded: list[Any] = []

    async def _spy(fn: Any, *args: Any, **kwargs: Any) -> Any:
        offloaded.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    with patch("pincer_telemetry._api.asyncio.to_thread", side_effect=_spy):
        await tel.flush()
        assert offloaded == [tel._otel.flush]
        assert fake_provider.calls[-1] == "flush_async"

        await tel.shutdown()
        assert offloaded == [tel._otel.flush, tel._otel.shutdown]
        assert fake_provider.calls[-1] == "stop_async"

        await tel.shutdown()  # idempotent
        assert fake_provider.calls.count("stop_async") == 1
    assert get_provider() is None


async def test_start_async_failure_releases_guard() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        await init_async(FakeProvider(fail_start=True), service_name="svc", version="1.0")  # type: ignore[arg-type]
    assert get_provider() is None
    tel = await init_async(FakeProvider(), service_name="svc", version="1.0")  # type: ignore[arg-type]
    await tel.shutdown()


async def test_sync_and_async_share_guard(fake_provider: FakeProvider) -> None:
    tel = await init_async(fake_provider, service_name="svc", version="1.0")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="already initialised"):
        init(FakeProvider(), service_name="svc", version="1.0")  # type: ignore[arg-type]
    await tel.shutdown()


async def test_posthog_async_end_to_end() -> None:
    with patch("posthog.AsyncPosthog") as cls:
        client = cls.return_value
        client.flush = AsyncMock()
        client.shutdown = AsyncMock()
        provider = PostHogProvider("https://phc_x@us.i.posthog.com")
        with (
            patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"),
            patch("opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter"),
        ):
            tel = await init_async(provider, service_name="svc", version="1.0", collectors={"traces", "logs"})

        current = get_provider()
        assert isinstance(current, PostHogProvider)
        assert current.client is client

        await tel.flush()
        client.flush.assert_awaited_once()
        await tel.shutdown()
        client.shutdown.assert_awaited_once()
    assert get_provider() is None
