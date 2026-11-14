"""Build a provider from environment variables."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pincer_telemetry.providers.posthog import DEFAULT_HOST, PostHogProvider
from pincer_telemetry.providers.uptrace import UptraceProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pincer_telemetry.providers.base import Provider


def provider_from_env(env: Mapping[str, str] | None = None) -> Provider | None:
    """
    Pick a provider from environment variables.

    - ``TELEMETRY_PROVIDER=uptrace|posthog`` selects explicitly; the matching
      credentials are then required (``ValueError`` if missing).
    - Otherwise Uptrace when ``UPTRACE_DSN`` (or legacy ``OTEL_DSN``) is set,
      else PostHog when ``POSTHOG_API_KEY`` is set.
    - ``POSTHOG_HOST`` optionally overrides the PostHog host.

    Returns ``None`` when nothing is configured, so callers can skip telemetry.
    """
    env = os.environ if env is None else env
    kind = env.get("TELEMETRY_PROVIDER", "").strip().lower()
    dsn = env.get("UPTRACE_DSN") or env.get("OTEL_DSN")
    api_key = env.get("POSTHOG_API_KEY")
    host = env.get("POSTHOG_HOST") or DEFAULT_HOST

    if kind == "uptrace":
        if not dsn:
            raise ValueError("TELEMETRY_PROVIDER=uptrace requires UPTRACE_DSN (or OTEL_DSN)")
        return UptraceProvider(dsn)
    if kind == "posthog":
        if not api_key:
            raise ValueError("TELEMETRY_PROVIDER=posthog requires POSTHOG_API_KEY")
        return PostHogProvider(api_key, host=host)
    if kind:
        raise ValueError(f"unknown TELEMETRY_PROVIDER {kind!r}; expected 'uptrace' or 'posthog'")
    if dsn:
        return UptraceProvider(dsn)
    if api_key:
        return PostHogProvider(api_key, host=host)
    return None
