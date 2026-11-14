"""
FastAPI app using async telemetry in its lifespan.

``init_async`` runs inside the server's event loop, so ``PostHogProvider.client``
is an ``AsyncPosthog`` usable from async handlers on that loop. The routes in
``examples/fastapi_routes.py`` reach telemetry without being handed any objects.

Run from the repository root (FastAPI is not a dependency of this package)::

    TELEMETRY_PROVIDER=posthog POSTHOG_API_KEY=phc_... \\
        uv run --with fastapi --with uvicorn uvicorn examples.fastapi_lifespan:app

Then::

    curl localhost:8000/event   # PostHog event + a span
    curl localhost:8000/boom    # logger.exception -> PostHog error tracking / OTel logs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from examples.fastapi_routes import router
from pincer_telemetry import init_async, provider_from_env

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=logging.INFO)
    provider = provider_from_env()
    if provider is None:
        logging.getLogger(__name__).warning("no telemetry configured; running without it")
        yield
        return

    tel = await init_async(provider, service_name="fastapi-example", version="0.1.0")
    try:
        yield
    finally:
        # Required in async mode: AsyncPosthog cannot be flushed from atexit.
        await tel.shutdown()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
