# pincer-telemetry

A lightweight Python library that initializes all three OpenTelemetry signal types — traces, metrics, and logs — with a single function call. Uses gRPC export by default, falling back to HTTP/1.1 automatically.

## Features

- **One-call setup** — a single `init()` configures TracerProvider, MeterProvider, and LoggerProvider globally
- **gRPC-first with HTTP fallback** — prefers the gRPC exporter; silently falls back to HTTP when the gRPC package is unavailable
- **Automatic stdlib logging bridge** — existing `logging.getLogger()` calls emit OTel log records without any code changes
- **DSN-style configuration** — embed your auth token directly in the endpoint URL
- **Graceful shutdown** — returns a callable and registers an `atexit` hook so exporters always flush

## Requirements

- Python 3.12+
- `opentelemetry-sdk >= 1.25.0`
- `opentelemetry-exporter-otlp-proto-grpc >= 1.25.0` (optional, for gRPC)
- `opentelemetry-exporter-otlp-proto-http >= 1.25.0` (fallback)
- `opentelemetry-instrumentation-logging >= 0.46b0`

## Installation

```bash
pip install pincer-telemetry
```

For development (includes test, lint, and type-checking tools):

```bash
pip install "pincer-telemetry[dev]"
```

## Quick start

```python
from pincer_telemetry import init

shutdown = init(
    project_name="my-service",
    version="1.2.3",
    dsn_url="https://<token>@otlp.uptrace.dev",
)

# TracerProvider, MeterProvider, and LoggerProvider are now set globally.
# Standard library logging is automatically bridged to OTel.

import logging
logger = logging.getLogger(__name__)
logger.info("service started")   # emitted as an OTel log record

# Flush and shut down all exporters explicitly when needed.
# This also happens automatically at process exit.
shutdown()
```

## API reference

### `init(project_name, version, dsn_url) → Callable[[], None]`

Initialises the three OpenTelemetry global providers and bridges stdlib logging.

| Parameter | Type | Description |
|---|---|---|
| `project_name` | `str` | Value for the `service.name` OTel resource attribute |
| `version` | `str` | Value for the `service.version` OTel resource attribute |
| `dsn_url` | `str` | Full OTLP endpoint URL. Embed the auth token as URL credentials (e.g. `https://mytoken@host`); the full URL is forwarded as an `uptrace-dsn` header to the exporters |

**Returns** a `Callable[[], None]` that shuts down all three providers and flushes pending data. An `atexit` hook calling this function is registered automatically.

**Raises** `ImportError` if the required OpenTelemetry packages are not installed.

## DSN URL format

```
https://<token>@<host>[:<port>]
```

- Use `http://` for an unencrypted connection (sets gRPC insecure mode).
- Credentials in the URL are stripped from the HTTP base URL and sent as the `uptrace-dsn` header.
- The gRPC exporter uses port `4317` by default (OTLP standard); the HTTP exporter appends `/v1/traces`, `/v1/metrics`, and `/v1/logs` paths automatically.
- Standard ports (`80`, `443`) are omitted from the HTTP base URL.

### Examples

```python
# Uptrace (managed backend)
init("svc", "1.0.0", "https://mytoken@otlp.uptrace.dev")

# Self-hosted collector on a custom port
init("svc", "1.0.0", "https://collector.internal:4318")

# Insecure local collector
init("svc", "1.0.0", "http://localhost")
```

## Transport selection

```
gRPC available?
  yes → OTLPSpan/Metric/LogExporter (grpc)
  no  → OTLPSpan/Metric/LogExporter (http/protobuf)
```

Install only the HTTP exporter to force HTTP mode:

```bash
pip install opentelemetry-exporter-otlp-proto-http
# (omit opentelemetry-exporter-otlp-proto-grpc)
```

## What gets configured

| OTel component | Implementation |
|---|---|
| TracerProvider | `opentelemetry.sdk.trace.TracerProvider` with `BatchSpanProcessor` |
| MeterProvider | `opentelemetry.sdk.metrics.MeterProvider` with `PeriodicExportingMetricReader` |
| LoggerProvider | `opentelemetry.sdk._logs.LoggerProvider` with `BatchLogRecordProcessor` |
| Logging bridge | `LoggingInstrumentor` (or `LoggingHandler` fallback) attached to `logging.root` |
| Resource | `service.name` and `service.version` attributes |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type check
mypy src

# Lint / format
ruff check src tests
ruff format src tests

# Security scan
bandit -r src
```

### CI

GitHub Actions runs on every push and pull request:

1. `pytest` with coverage
2. `mypy --strict`
3. `ruff check` + `ruff format --check`
4. `bandit`

## License

MIT — see [LICENSE](LICENSE).
