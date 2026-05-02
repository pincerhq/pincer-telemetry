"""
OpenTelemetry traces, metrics, and logs — shareable across Pincer workspace apps.

Requires the ``telemetry`` optional extra or direct installation::

    pip install pincer-telemetry

Quick start::

    from pincer_telemetry import init

    shutdown = init(
        project_name="my-agent",
        version="1.0.0",
        dsn_url="https://<token>@otlp.uptrace.dev",
    )

All three OTel signal types (traces, metrics, logs) are configured in a single
call.  The stdlib ``logging`` root logger is bridged into OTel logs so existing
``logging.getLogger(__name__)`` calls appear in your telemetry backend without
any code changes.
"""

from pincer_telemetry._core import init

__all__ = ["init"]
