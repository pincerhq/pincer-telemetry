# pincer-telemetry

Pluggable telemetry for Python services. One call sets up the OpenTelemetry traces, metrics and logs providers and points them at a backend **provider**: [Uptrace](https://uptrace.dev) or [PostHog](https://posthog.com).

## Features

- **Pluggable providers**: `UptraceProvider` (plain OTLP, gRPC or HTTP) and `PostHogProvider` (OTLP/HTTP plus the PostHog SDK client). Choose one per process.
- **Selectable collectors**: traces, metrics and logs are all on by default; pass a subset to turn the others off.
- **Sync and async modes**: `init()` for scripts and workers, `await init_async()` for asyncio apps such as FastAPI.
- **Automatic stdlib logging bridge**: existing `logging.getLogger()` calls become OTel log records.
- **Globals, not plumbing**: use `trace.get_tracer()`, `metrics.get_meter()`, `logging.getLogger()` and `get_provider()` anywhere in the app.

## Requirements

- Python 3.12+
- `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` and `opentelemetry-exporter-otlp-proto-http` >= 1.40.0
- `opentelemetry-instrumentation-logging` >= 0.61b0
- `posthog[async]` >= 7.45.0, < 8

## Installation

```bash
pip install pincer-telemetry
```

For development (test, lint and type-checking tools):

```bash
pip install -e ".[dev]"
```

## Quick start (sync)

```python
import logging

from opentelemetry import trace

from pincer_telemetry import UptraceProvider, init

tel = init(
    UptraceProvider(dsn="https://<token>@api.uptrace.dev?grpc=4317"),
    service_name="my-service",
    version="1.2.3",
)

logging.getLogger(__name__).warning("service started")  # exported as an OTel log record
with trace.get_tracer(__name__).start_as_current_span("work"):
    ...

tel.flush()  # optional
tel.shutdown()  # also registered with atexit in sync mode
```

## Async mode (FastAPI lifespan)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pincer_telemetry import PostHogProvider, init_async


@asynccontextmanager
async def lifespan(app: FastAPI):
    tel = await init_async(
        PostHogProvider(api_key="phc_...", host="https://eu.i.posthog.com"),
        service_name="api",
        version="1.0.0",
    )
    try:
        yield
    finally:
        await tel.shutdown()  # required, see below


app = FastAPI(lifespan=lifespan)
```

> **You must `await tel.shutdown()` in async mode.** `AsyncPosthog` cannot be flushed from `atexit`: by then the event loop is gone. `init_async` registers an `atexit` hook only for the OTel providers, as a safety net. Pending PostHog events are lost if you skip the explicit shutdown.

Code anywhere in the app can reach telemetry without being passed any objects:

```python
import logging

from opentelemetry import trace

from pincer_telemetry import PostHogProvider, get_provider

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


async def handler() -> None:
    with tracer.start_as_current_span("handler"):
        provider = get_provider()  # the Provider passed to init, or None
        if isinstance(provider, PostHogProvider):
            provider.client.capture("signed_up", distinct_id="user-1")
```

See [`examples/fastapi_lifespan.py`](examples/fastapi_lifespan.py) and [`examples/fastapi_routes.py`](examples/fastapi_routes.py) for a runnable app.

### Event-loop caveat

In async mode, `PostHogProvider.client` is an `AsyncPosthog` tied to the loop that ran `init_async`.

- **Safe:** code on that loop, such as FastAPI `async def` handlers and `BackgroundTasks` that run on the loop.
- **Not safe:** code on another loop or thread, such as sync `def` endpoints (FastAPI runs them in a threadpool), other threads, and worker processes. These should run their own sync `init()` in their own process, or skip vendor events. OTel tracers, meters and logging are thread-safe and fine everywhere.

## API reference

### `init(provider, *, service_name, version, collectors=DEFAULT_COLLECTORS, resource_attributes=None) -> Telemetry`

### `await init_async(provider, *, service_name, version, collectors=DEFAULT_COLLECTORS, resource_attributes=None) -> AsyncTelemetry`

| Parameter | Description |
|---|---|
| `provider` | An `UptraceProvider` or `PostHogProvider` (or any object implementing `Provider`) |
| `service_name` / `version` | `service.name` / `service.version` resource attributes |
| `collectors` | Any non-empty iterable of `Collector` members or their names (`"traces"`, `"metrics"`, `"logs"`). Default: all three |
| `resource_attributes` | Extra OTel resource attributes, e.g. `{"deployment.environment": "prod"}`. Keys here override `service.name`/`service.version` |

Both functions:

- build OTel providers only for the selected collectors and install them as the OTel globals;
- attach the stdlib logging bridge to the root logger, only when `logs` is selected;
- start the provider's backend client.

`Telemetry` and `AsyncTelemetry` expose:

- `provider`;
- `collectors`;
- `tracer_provider`, `meter_provider` and `logger_provider`, which are `None` for collectors that weren't selected;
- `flush()` and `shutdown()`. These are sync on `Telemetry` and coroutines on `AsyncTelemetry`. `shutdown()` can be called more than once.

**Raises**

- `ValueError` for an empty or unknown collector.
- `RuntimeError("telemetry already initialised")` on a second init in the same process. OTel's global providers can only be set once per process, so this also applies after `shutdown()`.
- `RuntimeError` when the provider was already used by an earlier init, even if it has since been shut down. Providers are single-use.

A failed init (for example the backend client raising on start) installs nothing global, and you can retry it.

### `get_provider() -> Provider | None`

Returns the provider passed to the successful init. It returns `None` before init, after `shutdown()` begins, or when init failed.

### `Collector`, `DEFAULT_COLLECTORS`

`Collector` is a `StrEnum` with members `TRACES`, `METRICS` and `LOGS`. `DEFAULT_COLLECTORS` is a frozenset of all three.

## Providers

### `UptraceProvider(dsn, *, compression=True)`

- **DSN format:** `https://<token>@api.uptrace.dev?grpc=4317`. The full DSN is sent as the `uptrace-dsn` header.
- **Transport:**
  - With `?grpc=<port>`, the provider uses gRPC to `<host>:<port>`. It falls back to HTTP if the gRPC exporter can't be imported.
  - Without it, the provider uses OTLP/HTTP to `<scheme>://<host>:<port>/v1/{traces,metrics,logs}`.
  - `http://` gives an insecure gRPC channel.
- **Settings Uptrace recommends:**
  - gzip compression;
  - DELTA temporality for `Counter`, `Histogram` and `ObservableCounter`;
  - batch processors with `max_queue_size` and `max_export_batch_size` of 1000;
  - a 60s metric export interval.

```python
UptraceProvider("https://<token>@api.uptrace.dev?grpc=4317")  # Uptrace cloud, gRPC
UptraceProvider("https://<token>@api.uptrace.dev")  # Uptrace cloud, HTTP
UptraceProvider("http://<token>@collector.internal:4318")  # self-hosted, plain HTTP
```

### `PostHogProvider(api_key, *, host="https://us.i.posthog.com", capture_exceptions=True, client_options=None)`

- **OTel export:** OTLP/HTTP with gzip to `{host}/i/v1/{traces,metrics,logs}`, with header `Authorization: Bearer <api_key>`. Use `https://eu.i.posthog.com` for the EU region. Metric temporality stays at the SDK default (cumulative).
- **Client:** `provider.client` is a `posthog.Posthog` after `init()`, or a `posthog.AsyncPosthog` after `init_async()`. Reading it before start raises `RuntimeError`. `client_options` is passed through to the client constructor.
- **Error tracking** (`capture_exceptions=True`):
  - In both modes, a root-logger handler forwards `ERROR`+ records that carry `exc_info`, such as `logger.exception(...)`, to `client.capture_exception`.
  - In sync mode, PostHog's `enable_exception_autocapture` also catches uncaught exceptions through `sys.excepthook` and `threading.excepthook`. `AsyncPosthog` has no autocapture.
- **Shutdown:** `shutdown()` flushes and closes the client and removes the handler.

PostHog's LLM-analytics OTel exporter (`posthog[otel]`, `/i/v0/ai/otel`) isn't supported yet; it may be added later.

## Configuring from the environment

`provider_from_env()` returns a provider built from environment variables, or `None` when nothing is configured:

| Variable | Meaning |
|---|---|
| `TELEMETRY_PROVIDER` | `uptrace` or `posthog`. Optional; when set, the matching credentials are required (`ValueError` otherwise) |
| `UPTRACE_DSN` | Uptrace DSN (`OTEL_DSN` is accepted as a legacy alias) |
| `POSTHOG_API_KEY` | PostHog project token (`phc_...`) |
| `POSTHOG_HOST` | PostHog host (default `https://us.i.posthog.com`) |

When `TELEMETRY_PROVIDER` is unset, Uptrace is chosen if a DSN is set; otherwise PostHog is chosen if `POSTHOG_API_KEY` is set.

```python
from pincer_telemetry import init, provider_from_env

if (provider := provider_from_env()) is not None:
    init(provider, service_name="svc", version="1.0.0")
```

## Migrating from 0.1

| 0.1 | 0.2 |
|---|---|
| `shutdown = init("svc", "1.0", dsn)` | `tel = init(UptraceProvider(dsn), service_name="svc", version="1.0")` |
| `init(project_name="svc", version="1.0")` with `OTEL_DSN` set | `init(provider_from_env(), service_name="svc", version="1.0")`, handling `None` |
| `shutdown()` | `tel.shutdown()` |
| `https://<token>@otlp.uptrace.dev` | `https://<token>@api.uptrace.dev?grpc=4317` |

Other changes:

- Metric temporality for Uptrace is now DELTA for counters and histograms.
- Exports are gzip-compressed.
- The logging bridge now uses `opentelemetry.instrumentation.logging.handler.LoggingHandler`. It replaces `LoggingInstrumentor` and the deprecated SDK handler.
- A second `init` now raises instead of being silently ignored.

## Development

```bash
uv sync --all-extras
uv run pytest --cov=pincer_telemetry
uv run mypy src/
uv run ruff check . && uv run ruff format --check .
uv run bandit -r src/pincer_telemetry -ll
```

CI (GitHub Actions) runs the same checks on every push and pull request.

## License

MIT — see [LICENSE](LICENSE).
