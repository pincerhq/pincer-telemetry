"""
Periodic telemetry example.

Demonstrates all three OTel signal types being emitted on a fixed interval:
  - a span wrapping each work iteration
  - a counter and gauge updated every tick
  - structured log records at each stage

Usage:
    DSN_URL=https://<token>@otlp.uptrace.dev python examples/periodic_telemetry.py

Optional env vars:
    DSN_URL       OTLP endpoint with embedded token (required)
    SERVICE_NAME  service.name resource attribute (default: periodic-example)
    VERSION       service.version resource attribute (default: 0.1.0)
    INTERVAL      seconds between ticks (default: 5)
    TICKS         number of iterations before exit, 0 = run forever (default: 0)
"""

from __future__ import annotations

import logging
import math
import os
import signal
import time

logger = logging.getLogger(__name__)


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def main() -> None:
    dsn_url = os.environ.get("DSN_URL")
    if not dsn_url:
        raise SystemExit("DSN_URL environment variable is required")

    service_name = _env("SERVICE_NAME", "periodic-example")
    version = _env("VERSION", "0.1.0")
    interval = float(_env("INTERVAL", "5"))
    max_ticks = int(_env("TICKS", "0"))

    from pincer_telemetry import init

    shutdown = init(project_name=service_name, version=version, dsn_url=dsn_url)

    # OTel providers are now set globally — obtain tracer and meter from them.
    from opentelemetry import metrics, trace
    from opentelemetry.metrics import Observation

    tracer = trace.get_tracer(service_name)
    meter = metrics.get_meter(service_name)

    tick_counter = meter.create_counter(
        "example.ticks",
        unit="1",
        description="Total number of work iterations completed",
    )
    queue_depth = meter.create_observable_gauge(
        "example.queue_depth",
        callbacks=[lambda _: [Observation(math.sin(time.monotonic()) * 10 + 10)]],
        unit="messages",
        description="Simulated queue depth (sine wave)",
    )
    _ = queue_depth  # registered via callback; reference kept to avoid GC

    stop = False

    def _handle_signal(sig: int, _frame: object) -> None:
        nonlocal stop
        logger.info("received signal %d, stopping after current tick", sig)
        stop = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "starting periodic telemetry loop service=%s version=%s interval=%.1fs", service_name, version, interval
    )

    tick = 0
    while not stop and (max_ticks == 0 or tick < max_ticks):
        tick += 1

        with tracer.start_as_current_span("work.tick") as span:
            span.set_attribute("tick.index", tick)
            span.set_attribute("tick.interval_s", interval)

            logger.info("tick %d started", tick)

            # Simulate two sub-steps so the trace has child spans.
            with tracer.start_as_current_span("work.fetch"):
                time.sleep(interval * 0.3)
                logger.debug("fetch complete tick=%d", tick)

            with tracer.start_as_current_span("work.process"):
                time.sleep(interval * 0.2)
                logger.debug("process complete tick=%d", tick)

            tick_counter.add(1, {"service": service_name})
            logger.info("tick %d complete", tick)

        # Sleep the remainder of the interval before the next tick.
        remaining = interval - interval * 0.5
        if not stop:
            time.sleep(remaining)

    logger.info("loop finished after %d tick(s), flushing exporters", tick)
    shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")
    main()
