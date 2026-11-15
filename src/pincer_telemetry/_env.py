"""Build a provider from environment variables."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from pincer_telemetry._errors import TelemetryConfigError
from pincer_telemetry.providers.posthog import PostHogProvider
from pincer_telemetry.providers.uptrace import UptraceProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pincer_telemetry.providers.base import Provider

_POSTHOG_DEFAULT_HOST = "https://us.i.posthog.com"


def _posthog_from_api_key(api_key: str, host: str) -> PostHogProvider:
    """Build a PostHog provider from the legacy key + host pair by composing its DSN."""
    scheme, sep, netloc = host.strip().rstrip("/").partition("://")
    if not sep:
        raise TelemetryConfigError("POSTHOG_HOST must be scheme://host[:port], e.g. https://eu.i.posthog.com")
    return PostHogProvider(f"{scheme}://{quote(api_key.strip(), safe='')}@{netloc}")


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
    host = env.get("POSTHOG_HOST") or _POSTHOG_DEFAULT_HOST

    if kind == "uptrace":
        if not dsn:
            raise ValueError("TELEMETRY_PROVIDER=uptrace requires UPTRACE_DSN (or OTEL_DSN)")
        return UptraceProvider(dsn)
    if kind == "posthog":
        if not api_key:
            raise ValueError("TELEMETRY_PROVIDER=posthog requires POSTHOG_API_KEY")
        return _posthog_from_api_key(api_key, host)
    if kind:
        raise ValueError(f"unknown TELEMETRY_PROVIDER {kind!r}; expected 'uptrace' or 'posthog'")
    if dsn:
        return UptraceProvider(dsn)
    if api_key:
        return _posthog_from_api_key(api_key, host)
    return None


_PREFIXED_PROVIDERS = ("UPTRACE", "POSTHOG")


def _uptrace_from_dsn(key: str, dsn: str) -> UptraceProvider:
    try:
        parts = urlsplit(dsn)
        _ = parts.port
    except ValueError:
        raise TelemetryConfigError(f"{key} is not a valid URL") from None
    if parts.scheme not in ("http", "https"):
        raise TelemetryConfigError(f"{key} must use the http or https scheme")
    if not parts.hostname:
        raise TelemetryConfigError(f"{key} has no host")
    if not parts.username:
        raise TelemetryConfigError(f"{key} has no token; expected scheme://<token>@host")
    return UptraceProvider(dsn)


def provider_from_prefixed_env(prefix: str = "TELEMETRY_", env: Mapping[str, str] | None = None) -> Provider | None:
    """
    Build a provider from ``<prefix><PROVIDER>_DSN`` variables, strictly.

    Only keys starting with ``prefix`` are considered; empty values count as unset
    (docker-compose ``${VAR}`` yields empty strings). Recognised keys:

    - ``<prefix>UPTRACE_DSN`` — ``https://<token>@api.uptrace.dev?grpc=4317``
    - ``<prefix>POSTHOG_DSN`` — ``https://<phc_api_key>@us.i.posthog.com`` (or ``eu.i.posthog.com``)

    Returns ``None`` when no key is set.

    Raises
    ------
    TelemetryConfigError
        On any other non-empty ``<prefix>*`` key, when both providers are configured,
        or when the DSN is malformed. Messages name keys, never values.
    """
    env = os.environ if env is None else env
    dsns: dict[str, tuple[str, str]] = {}
    for key in sorted(env):
        if not key.startswith(prefix):
            continue
        value = env[key].strip()
        if not value:
            continue
        rest = key[len(prefix) :]
        provider, sep, suffix = rest.rpartition("_")
        if not sep or suffix != "DSN" or provider not in _PREFIXED_PROVIDERS:
            expected = " or ".join(f"{prefix}{p}_DSN" for p in _PREFIXED_PROVIDERS)
            raise TelemetryConfigError(f"unknown telemetry setting {key}; expected {expected}")
        dsns[provider] = (key, value)

    if len(dsns) > 1:
        keys = " and ".join(key for key, _ in dsns.values())
        raise TelemetryConfigError(f"{keys} are both set; configure only one telemetry provider")
    if not dsns:
        return None
    ((provider, (key, dsn)),) = dsns.items()
    if provider == "UPTRACE":
        return _uptrace_from_dsn(key, dsn)
    try:
        return PostHogProvider(dsn)
    except TelemetryConfigError as exc:
        raise TelemetryConfigError(f"{key}: {exc}") from None
