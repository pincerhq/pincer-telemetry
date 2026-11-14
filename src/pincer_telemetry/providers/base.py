"""The vendor-neutral provider protocol and a base for providers without a backend client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pincer_telemetry._types import Collector, OtelTuning, SignalExporters


class Provider(Protocol):
    """
    A telemetry backend.

    A provider supplies OTLP exporters for the requested collectors and owns any
    vendor client (e.g. the PostHog SDK client). Providers are stateful and
    single-use: ``start_*`` creates the client, ``stop_*`` tears it down.
    """

    name: str
    started: bool

    def exporters(self, collectors: frozenset[Collector]) -> SignalExporters:
        """Build exporters for exactly the given collectors."""
        ...

    def tuning(self) -> OtelTuning:
        """Batch sizes and export interval recommended for this backend."""
        ...

    def start_sync(self) -> None:
        """Create and store the backend client (if any) for sync use."""
        ...

    async def start_async(self) -> None:
        """Create and store the backend client (if any); must run inside the target event loop."""
        ...

    def flush_sync(self) -> None:
        """Flush the backend client (if any)."""
        ...

    async def flush_async(self) -> None:
        """Flush the backend client (if any)."""
        ...

    def stop_sync(self) -> None:
        """Flush and close the backend client (if any)."""
        ...

    async def stop_async(self) -> None:
        """Flush and close the backend client (if any)."""
        ...


class ClientlessProvider:
    """
    Base for providers that only export OTLP and own no backend client.

    Subclasses implement ``name``, ``exporters`` and ``tuning``; the lifecycle
    methods here only track ``started``.
    """

    name: str

    def __init__(self) -> None:
        self.started = False

    def start_sync(self) -> None:
        self.started = True

    async def start_async(self) -> None:
        self.started = True

    def flush_sync(self) -> None:
        return None

    async def flush_async(self) -> None:
        return None

    def stop_sync(self) -> None:
        self.started = False

    async def stop_async(self) -> None:
        self.started = False
