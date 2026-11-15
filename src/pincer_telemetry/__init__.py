"""
Pluggable telemetry: OpenTelemetry traces, metrics and logs for Uptrace or PostHog.

Sync::

    from pincer_telemetry import UptraceProvider, init

    tel = init(UptraceProvider(dsn="https://<token>@api.uptrace.dev?grpc=4317"), service_name="svc", version="1.0.0")

Async (e.g. a FastAPI lifespan)::

    from pincer_telemetry import PostHogProvider, init_async

    tel = await init_async(PostHogProvider(dsn="https://phc_...@us.i.posthog.com"), service_name="svc", version="1.0.0")
    ...
    await tel.shutdown()

Anywhere else in the app, use the OTel globals (``trace.get_tracer``,
``metrics.get_meter``, ``logging.getLogger``) and :func:`get_provider` for
vendor-specific features.
"""

from pincer_telemetry._api import AsyncTelemetry, Telemetry, get_provider, init, init_async
from pincer_telemetry._env import provider_from_env, provider_from_prefixed_env
from pincer_telemetry._errors import TelemetryConfigError
from pincer_telemetry._types import DEFAULT_COLLECTORS, Collector, OtelTuning, SignalExporters
from pincer_telemetry.providers import PostHogProvider, Provider, UptraceProvider

__all__ = [
    "DEFAULT_COLLECTORS",
    "AsyncTelemetry",
    "Collector",
    "OtelTuning",
    "PostHogProvider",
    "Provider",
    "SignalExporters",
    "Telemetry",
    "TelemetryConfigError",
    "UptraceProvider",
    "get_provider",
    "init",
    "init_async",
    "provider_from_env",
    "provider_from_prefixed_env",
]
