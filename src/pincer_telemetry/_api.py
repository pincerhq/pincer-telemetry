"""Public entry points: ``init``, ``init_async``, ``get_provider`` and the telemetry handles."""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pincer_telemetry._otel import OtelProviders, build_otel
from pincer_telemetry._types import DEFAULT_COLLECTORS, Collector

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    from pincer_telemetry.providers.base import Provider

# Module-level state. ``_claimed`` is the init-once guard: OpenTelemetry's global
# ``set_*_provider`` calls only take effect once per process, so once an init has
# installed them, any later init must fail loudly instead of being silently ignored.
# ``_current`` is what ``get_provider()`` returns; it is set only after a fully
# successful init and cleared at the start of shutdown. ``_used`` remembers every
# provider that completed an init, so a stopped provider cannot be passed in again.
_log = logging.getLogger(__name__)
_lock = threading.Lock()
_claimed = False
_current: Provider | None = None
_used: weakref.WeakSet[Any] = weakref.WeakSet()


def get_provider() -> Provider | None:
    """Return the provider passed to the successful ``init``/``init_async``, or ``None``."""
    return _current


def _reset_for_tests() -> None:
    global _claimed, _current
    with _lock:
        _claimed = False
        _current = None


def _claim(provider: Provider) -> None:
    global _claimed
    with _lock:
        if _claimed:
            raise RuntimeError("telemetry already initialised")
        if provider.started or provider in _used:
            raise RuntimeError(f"{type(provider).__name__} was already started; providers are single-use")
        _claimed = True


def _release_claim() -> None:
    global _claimed
    with _lock:
        _claimed = False


def _publish(provider: Provider) -> None:
    global _current
    with _lock:
        _current = provider
        _used.add(provider)


def _unpublish(provider: Provider) -> None:
    global _current
    with _lock:
        if _current is provider:
            _current = None


def _validate_collectors(collectors: Iterable[Collector | str]) -> frozenset[Collector]:
    result = frozenset(Collector(c) for c in collectors)
    if not result:
        raise ValueError("collectors must not be empty")
    return result


def _prepare(
    provider: Provider,
    service_name: str,
    version: str,
    collectors: frozenset[Collector],
    resource_attributes: Mapping[str, Any] | None,
) -> OtelProviders:
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create(
        {"service.name": service_name, "service.version": version, **(resource_attributes or {})}
    )
    return build_otel(resource, provider.exporters(collectors), collectors, provider.tuning())


@contextmanager
def _initialising(
    provider: Provider,
    service_name: str,
    version: str,
    collectors: Iterable[Collector | str],
    resource_attributes: Mapping[str, Any] | None,
) -> Iterator[tuple[frozenset[Collector], OtelProviders]]:
    """
    Shared init sequence around the provider's start step (run in the ``with`` body).

    Claims the init-once guard, validates collectors and builds the OTel providers
    without installing them. If anything (including the body) fails, it shuts down
    what was built and releases the guard, so nothing global is left behind. On
    success it installs the OTel globals and publishes the provider.
    """
    _claim(provider)
    otel: OtelProviders | None = None
    try:
        selected = _validate_collectors(collectors)
        otel = _prepare(provider, service_name, version, selected, resource_attributes)
        yield selected, otel
    except BaseException:
        if otel is not None:
            otel.shutdown()
        _release_claim()
        raise
    otel.install()
    _publish(provider)
    _log.debug("telemetry initialised: provider=%s collectors=%s", provider.name, ",".join(sorted(selected)))


@dataclass(eq=False)
class _TelemetryBase:
    provider: Provider
    collectors: frozenset[Collector]
    _otel: OtelProviders = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def tracer_provider(self) -> TracerProvider | None:
        return self._otel.tracer_provider

    @property
    def meter_provider(self) -> MeterProvider | None:
        return self._otel.meter_provider

    @property
    def logger_provider(self) -> LoggerProvider | None:
        return self._otel.logger_provider

    def _begin_close(self) -> bool:
        """Mark closed and clear the global slot. Returns ``False`` if already closed."""
        if self._closed:
            return False
        self._closed = True
        _unpublish(self.provider)
        return True


@dataclass(eq=False)
class Telemetry(_TelemetryBase):
    """Handle returned by :func:`init`. ``shutdown`` is also registered with ``atexit``."""

    def flush(self) -> None:
        """Flush OTel exporters and the backend client."""
        if self._closed:
            return
        self._otel.flush()
        self.provider.flush_sync()

    def shutdown(self) -> None:
        """Flush and close everything. Safe to call more than once."""
        if not self._begin_close():
            return
        atexit.unregister(self.shutdown)
        try:
            self._otel.shutdown()
        finally:
            self.provider.stop_sync()


@dataclass(eq=False)
class AsyncTelemetry(_TelemetryBase):
    """
    Handle returned by :func:`init_async`.

    You must ``await tel.shutdown()`` before the event loop closes (e.g. in a
    FastAPI lifespan); async backend clients cannot be flushed from ``atexit``.
    """

    async def flush(self) -> None:
        """Flush OTel exporters (in a worker thread) and the backend client."""
        if self._closed:
            return
        await asyncio.to_thread(self._otel.flush)
        await self.provider.flush_async()

    async def shutdown(self) -> None:
        """Flush and close everything. Safe to call more than once."""
        if not self._begin_close():
            return
        atexit.unregister(self._otel.shutdown)
        try:
            await asyncio.to_thread(self._otel.shutdown)
        finally:
            await self.provider.stop_async()


def init(
    provider: Provider,
    *,
    service_name: str,
    version: str,
    collectors: Iterable[Collector | str] = DEFAULT_COLLECTORS,
    resource_attributes: Mapping[str, Any] | None = None,
) -> Telemetry:
    """
    Initialise telemetry in sync mode.

    Builds OTel providers for ``collectors`` (all three by default), starts the
    provider's backend client, installs the OTel globals, bridges stdlib logging
    (when ``logs`` is collected) and registers ``Telemetry.shutdown`` with ``atexit``.

    Raises
    ------
    ValueError
        When ``collectors`` is empty or contains an unknown name.
    RuntimeError
        When telemetry was already initialised in this process, or ``provider``
        was already used by an earlier init (providers are single-use).
    """
    with _initialising(provider, service_name, version, collectors, resource_attributes) as (selected, otel):
        provider.start_sync()
    tel = Telemetry(provider=provider, collectors=selected, _otel=otel)
    atexit.register(tel.shutdown)
    return tel


async def init_async(
    provider: Provider,
    *,
    service_name: str,
    version: str,
    collectors: Iterable[Collector | str] = DEFAULT_COLLECTORS,
    resource_attributes: Mapping[str, Any] | None = None,
) -> AsyncTelemetry:
    """
    Initialise telemetry in async mode; call from inside the running event loop.

    Same as :func:`init`, but the provider's backend client is created for the
    current loop (e.g. ``AsyncPosthog``). Only the OTel part is registered with
    ``atexit`` as a safety net — you must ``await tel.shutdown()`` yourself.
    """
    with _initialising(provider, service_name, version, collectors, resource_attributes) as (selected, otel):
        await provider.start_async()
    atexit.register(otel.shutdown)
    return AsyncTelemetry(provider=provider, collectors=selected, _otel=otel)
