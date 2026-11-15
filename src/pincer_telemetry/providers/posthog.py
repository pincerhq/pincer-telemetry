"""PostHog provider — OTLP/HTTP export to PostHog plus the PostHog SDK client."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote, urlsplit

from pincer_telemetry._errors import TelemetryConfigError
from pincer_telemetry._types import Collector, OtelTuning, SignalExporters

if TYPE_CHECKING:
    from posthog import AsyncPosthog, Posthog


class _PostHogExceptionHandler(logging.Handler):
    """
    Forward ``logger.exception(...)``-style records to PostHog error tracking.

    Only records at ERROR or above that carry ``exc_info`` are forwarded.
    Records from PostHog's own loggers are ignored to avoid feedback loops.
    """

    def __init__(self, client: Posthog | AsyncPosthog) -> None:
        super().__init__(level=logging.ERROR)
        self._client = client

    def emit(self, record: logging.LogRecord) -> None:
        if not record.exc_info or record.exc_info[1] is None:
            return
        if record.name == "posthog" or record.name.startswith("posthog."):
            return
        try:
            self._client.capture_exception(record.exc_info[1])
        except Exception:
            self.handleError(record)


def _parse_dsn(dsn: str) -> tuple[str, str]:
    """
    Split a PostHog DSN, ``scheme://<api_key>@host[:port]``, into ``(api_key, host)``.

    ``host`` is ``scheme://host[:port]``, without the key.

    Raises
    ------
    TelemetryConfigError
        When the scheme, host or key is missing, or the URL has a password, path, query
        or fragment. The message never includes the DSN, which carries the key.
    """
    try:
        parts = urlsplit(dsn.strip())
        port = parts.port
    except ValueError:
        raise TelemetryConfigError("PostHog DSN is not a valid URL") from None
    if parts.scheme not in ("http", "https"):
        raise TelemetryConfigError("PostHog DSN must use the http or https scheme")
    if not parts.hostname:
        raise TelemetryConfigError("PostHog DSN has no host")
    if not parts.username:
        raise TelemetryConfigError("PostHog DSN has no API key; expected scheme://<api_key>@host")
    if parts.password is not None:
        raise TelemetryConfigError("PostHog DSN must not contain a password; expected scheme://<api_key>@host")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise TelemetryConfigError("PostHog DSN must not have a path, query or fragment")
    hostname = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname  # IPv6 literal
    netloc = hostname if port is None else f"{hostname}:{port}"
    return unquote(parts.username), f"{parts.scheme}://{netloc}"


class PostHogProvider:
    """
    Export OTel traces, metrics and logs to PostHog and expose the PostHog client.

    Parameters
    ----------
    dsn:
        PostHog DSN, ``scheme://<api_key>@host[:port]``, e.g.
        ``https://phc_xxx@us.i.posthog.com`` or ``https://phc_xxx@eu.i.posthog.com``.
        The userinfo is the project token (``api_key``); ``scheme://host[:port]`` is
        the ingest ``host``.
    capture_exceptions:
        Send exceptions to PostHog error tracking: ``logger.exception()`` records
        (both modes) and uncaught exceptions via autocapture (sync mode only).
    client_options:
        Extra keyword arguments for ``Posthog``/``AsyncPosthog``.

    Raises
    ------
    TelemetryConfigError
        When the DSN is malformed (see :func:`_parse_dsn`). The message never includes the DSN.
    """

    name = "posthog"

    def __init__(
        self,
        dsn: str,
        *,
        capture_exceptions: bool = True,
        client_options: dict[str, Any] | None = None,
    ) -> None:
        self.api_key, self.host = _parse_dsn(dsn)
        self.dsn = dsn
        self.capture_exceptions = capture_exceptions
        self.client_options: dict[str, Any] = dict(client_options or {})
        self.started = False
        self._client: Posthog | AsyncPosthog | None = None
        self._handler: _PostHogExceptionHandler | None = None

    def __repr__(self) -> str:
        return f"PostHogProvider(host={self.host!r}, started={self.started!r})"

    @property
    def client(self) -> Posthog | AsyncPosthog:
        """The PostHog client: ``Posthog`` after ``init()``, ``AsyncPosthog`` after ``init_async()``."""
        if self._client is None:
            raise RuntimeError("PostHogProvider is not started; call init() or init_async() first")
        return self._client

    # ── OTel ────────────────────────────────────────────────────────────────

    def _endpoint(self, collector: Collector) -> str:
        return f"{self.host}/i/v1/{collector.value}"

    def exporters(self, collectors: frozenset[Collector]) -> SignalExporters:
        from opentelemetry.exporter.otlp.proto.http import Compression
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        kw: dict[str, Any] = {
            "headers": {"Authorization": f"Bearer {self.api_key}"},
            "compression": Compression.Gzip,
        }
        return SignalExporters.for_collectors(
            collectors,
            span=lambda: OTLPSpanExporter(endpoint=self._endpoint(Collector.TRACES), **kw),
            metric=lambda: OTLPMetricExporter(endpoint=self._endpoint(Collector.METRICS), **kw),
            log=lambda: OTLPLogExporter(endpoint=self._endpoint(Collector.LOGS), **kw),
        )

    def tuning(self) -> OtelTuning:
        return OtelTuning()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _activate(self, client: Posthog | AsyncPosthog) -> None:
        self._client = client
        if self.capture_exceptions:
            self._handler = _PostHogExceptionHandler(client)
            logging.getLogger().addHandler(self._handler)
        self.started = True

    def _deactivate(self) -> None:
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler = None
        self._client = None
        self.started = False

    def start_sync(self) -> None:
        import posthog

        self._activate(
            posthog.Posthog(
                self.api_key,
                host=self.host,
                enable_exception_autocapture=self.capture_exceptions,
                **self.client_options,
            )
        )

    async def start_async(self) -> None:
        import posthog

        self._activate(posthog.AsyncPosthog(self.api_key, host=self.host, **self.client_options))

    def flush_sync(self) -> None:
        if self._client is not None:
            self._client.flush()

    async def flush_async(self) -> None:
        if self._client is not None:
            await cast("AsyncPosthog", self._client).flush()

    def stop_sync(self) -> None:
        client = self._client
        self._deactivate()
        if client is not None:
            client.shutdown()

    async def stop_async(self) -> None:
        client = self._client
        self._deactivate()
        if client is not None:
            await cast("AsyncPosthog", client).shutdown()
