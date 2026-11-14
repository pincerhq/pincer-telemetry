"""Exceptions raised by pincer-telemetry."""

from __future__ import annotations


class TelemetryConfigError(ValueError):
    """
    Telemetry configuration is invalid: unknown keys, conflicting providers or a malformed DSN.

    Messages name configuration keys, never their values, because DSNs carry secrets.
    """
