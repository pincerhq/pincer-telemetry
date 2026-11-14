"""Routes that use telemetry through globals only — no objects passed in."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from opentelemetry import trace

from pincer_telemetry import PostHogProvider, get_provider

router = APIRouter()
logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@router.get("/event")
async def event() -> dict[str, str]:
    with tracer.start_as_current_span("example.event"):
        provider = get_provider()
        if isinstance(provider, PostHogProvider):
            # AsyncPosthog here: capture() only queues the event, it is safe on the loop.
            provider.client.capture("example_event", distinct_id="example-user", properties={"source": "fastapi"})
        logger.info("event endpoint hit")
    return {"status": "ok"}


@router.get("/boom")
async def boom() -> dict[str, str]:
    try:
        1 / 0  # noqa: B018
    except ZeroDivisionError:
        # Reaches OTel logs and, with PostHogProvider, PostHog error tracking.
        logger.exception("boom endpoint failed")
    return {"status": "logged"}
