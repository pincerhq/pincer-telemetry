"""init(): collectors, resource, init-once guard, get_provider, atexit and provider_from_env."""

from __future__ import annotations

import logging

import pytest
from conftest import FakeProvider, OtelGlobals
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

from pincer_telemetry import (
    DEFAULT_COLLECTORS,
    Collector,
    PostHogProvider,
    Telemetry,
    TelemetryConfigError,
    UptraceProvider,
    get_provider,
    init,
    provider_from_env,
    provider_from_prefixed_env,
)


def _otel_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]


def _init(provider: FakeProvider, **kw: object) -> Telemetry:
    return init(provider, service_name="svc", version="1.0", **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def test_default_collectors_build_all_three(fake_provider: FakeProvider, otel_globals: OtelGlobals) -> None:
    tel = _init(fake_provider)

    assert tel.collectors == DEFAULT_COLLECTORS
    assert fake_provider.requested == DEFAULT_COLLECTORS
    assert tel.tracer_provider is not None
    assert tel.meter_provider is not None
    assert tel.logger_provider is not None
    otel_globals.set_tracer.assert_called_once_with(tel.tracer_provider)
    otel_globals.set_meter.assert_called_once_with(tel.meter_provider)
    otel_globals.set_logger.assert_called_once_with(tel.logger_provider)
    assert len(_otel_handlers()) == 1


def test_subset_builds_only_selected(fake_provider: FakeProvider, otel_globals: OtelGlobals) -> None:
    tel = _init(fake_provider, collectors={Collector.TRACES, "metrics"})

    assert tel.collectors == frozenset({Collector.TRACES, Collector.METRICS})
    assert tel.logger_provider is None
    otel_globals.set_logger.assert_not_called()
    assert _otel_handlers() == []


def test_logs_only(fake_provider: FakeProvider, otel_globals: OtelGlobals) -> None:
    tel = _init(fake_provider, collectors=[Collector.LOGS])
    assert tel.tracer_provider is None
    assert tel.meter_provider is None
    otel_globals.set_tracer.assert_not_called()
    otel_globals.set_meter.assert_not_called()
    assert len(_otel_handlers()) == 1


def test_empty_collectors_raise(fake_provider: FakeProvider) -> None:
    with pytest.raises(ValueError, match="empty"):
        _init(fake_provider, collectors=set())
    assert fake_provider.calls == []


def test_unknown_collector_raises(fake_provider: FakeProvider) -> None:
    with pytest.raises(ValueError):
        _init(fake_provider, collectors={"profiles"})


def test_provider_missing_exporter_raises(otel_globals: OtelGlobals) -> None:
    from pincer_telemetry import SignalExporters

    class NoLogs(FakeProvider):
        def exporters(self, collectors: frozenset[Collector]) -> SignalExporters:
            return SignalExporters()

    p = NoLogs()
    with pytest.raises(ValueError, match="logs"):
        _init(p, collectors={Collector.LOGS})
    assert get_provider() is None
    # Claim was released: a corrected init can still succeed.
    _init(FakeProvider())


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


def test_resource_attributes(fake_provider: FakeProvider) -> None:
    tel = init(
        fake_provider,  # type: ignore[arg-type]
        service_name="my-agent",
        version="3.1.4",
        resource_attributes={"deployment.environment": "prod"},
    )
    assert tel.tracer_provider is not None
    attrs = tel.tracer_provider.resource.attributes
    assert attrs["service.name"] == "my-agent"
    assert attrs["service.version"] == "3.1.4"
    assert attrs["deployment.environment"] == "prod"


def test_resource_attributes_override_service_fields(fake_provider: FakeProvider) -> None:
    tel = init(
        fake_provider,  # type: ignore[arg-type]
        service_name="my-agent",
        version="3.1.4",
        resource_attributes={"service.name": "override"},
    )
    assert tel.tracer_provider is not None
    assert tel.tracer_provider.resource.attributes["service.name"] == "override"


# ---------------------------------------------------------------------------
# Init-once guard and single-use providers
# ---------------------------------------------------------------------------


def test_second_init_raises(fake_provider: FakeProvider) -> None:
    _init(fake_provider)
    with pytest.raises(RuntimeError, match="already initialised"):
        _init(FakeProvider())


def test_init_after_shutdown_still_raises(fake_provider: FakeProvider) -> None:
    # OTel globals can only be set once per process; a re-init would be silently ignored.
    _init(fake_provider).shutdown()
    with pytest.raises(RuntimeError, match="already initialised"):
        _init(FakeProvider())


def test_second_init_checks_guard_before_collectors(fake_provider: FakeProvider) -> None:
    _init(fake_provider)
    with pytest.raises(RuntimeError, match="already initialised"):
        _init(FakeProvider(), collectors=set())


def test_invalid_collectors_release_guard(fake_provider: FakeProvider) -> None:
    with pytest.raises(ValueError):
        _init(fake_provider, collectors=set())
    _init(fake_provider)  # neither the guard nor the provider was consumed


def test_stopped_provider_cannot_be_reused(fake_provider: FakeProvider) -> None:
    from pincer_telemetry._api import _reset_for_tests

    _init(fake_provider).shutdown()
    assert not fake_provider.started
    _reset_for_tests()  # clear the process-wide guard so only the single-use check applies
    with pytest.raises(RuntimeError, match="single-use"):
        _init(fake_provider)


def test_started_provider_rejected() -> None:
    p = FakeProvider()
    p.started = True
    with pytest.raises(RuntimeError, match="single-use"):
        _init(p)
    # The guard did not get claimed by the rejected call.
    _init(FakeProvider())


# ---------------------------------------------------------------------------
# get_provider()
# ---------------------------------------------------------------------------


def test_get_provider_lifecycle(fake_provider: FakeProvider) -> None:
    assert get_provider() is None
    tel = _init(fake_provider)
    assert get_provider() is fake_provider
    tel.shutdown()
    assert get_provider() is None


def test_get_provider_none_when_start_fails(otel_globals: OtelGlobals) -> None:
    p = FakeProvider(fail_start=True)
    with pytest.raises(RuntimeError, match="boom"):
        _init(p)
    assert get_provider() is None
    otel_globals.set_tracer.assert_not_called()
    assert _otel_handlers() == []
    # Nothing global was installed, so a retry is allowed.
    _init(FakeProvider())


def test_get_provider_cleared_before_teardown(fake_provider: FakeProvider) -> None:
    seen: list[object] = []
    fake_provider.stop_sync = lambda: seen.append(get_provider())  # type: ignore[method-assign]
    _init(fake_provider).shutdown()
    assert seen == [None]


# ---------------------------------------------------------------------------
# Shutdown, flush, atexit
# ---------------------------------------------------------------------------


def test_atexit_registered(fake_provider: FakeProvider, otel_globals: OtelGlobals) -> None:
    tel = _init(fake_provider)
    otel_globals.atexit.register.assert_called_once_with(tel.shutdown)


def test_shutdown_is_idempotent(fake_provider: FakeProvider, otel_globals: OtelGlobals) -> None:
    tel = _init(fake_provider)
    tel.shutdown()
    tel.shutdown()
    assert fake_provider.calls.count("stop_sync") == 1
    otel_globals.atexit.unregister.assert_called_once_with(tel.shutdown)
    assert _otel_handlers() == []


def test_flush_flushes_otel_and_provider(fake_provider: FakeProvider) -> None:
    tel = _init(fake_provider)
    tel.flush()
    assert "flush_sync" in fake_provider.calls
    tel.shutdown()
    fake_provider.calls.clear()
    tel.flush()  # no-op after shutdown
    assert fake_provider.calls == []


def test_logging_bridge_emits_records(fake_provider: FakeProvider) -> None:
    tel = _init(fake_provider, collectors={Collector.LOGS})
    logging.getLogger("tests.bridge").warning("hello otel")
    tel.flush()
    assert fake_provider.built is not None
    exporter = fake_provider.built.log
    assert isinstance(exporter, InMemoryLogRecordExporter)
    bodies = [r.log_record.body for r in exporter.get_finished_logs()]
    assert "hello otel" in bodies


# ---------------------------------------------------------------------------
# provider_from_env()
# ---------------------------------------------------------------------------


def test_env_nothing_configured() -> None:
    assert provider_from_env({}) is None


@pytest.mark.parametrize("var", ["UPTRACE_DSN", "OTEL_DSN"])
def test_env_implicit_uptrace(var: str) -> None:
    p = provider_from_env({var: "https://t@api.uptrace.dev", "POSTHOG_API_KEY": "phc_x"})
    assert isinstance(p, UptraceProvider)
    assert p.dsn == "https://t@api.uptrace.dev"


def test_env_uptrace_dsn_wins_over_otel_dsn() -> None:
    p = provider_from_env({"UPTRACE_DSN": "https://a@h", "OTEL_DSN": "https://b@h"})
    assert isinstance(p, UptraceProvider)
    assert p.dsn == "https://a@h"


def test_env_implicit_posthog_with_host() -> None:
    p = provider_from_env({"POSTHOG_API_KEY": "phc_x", "POSTHOG_HOST": "https://eu.i.posthog.com"})
    assert isinstance(p, PostHogProvider)
    assert p.api_key == "phc_x"
    assert p.host == "https://eu.i.posthog.com"


def test_env_posthog_default_host() -> None:
    p = provider_from_env({"POSTHOG_API_KEY": "phc_x"})
    assert isinstance(p, PostHogProvider)
    assert p.host == "https://us.i.posthog.com"


def test_env_explicit_posthog_overrides_dsn() -> None:
    p = provider_from_env({"TELEMETRY_PROVIDER": "PostHog", "OTEL_DSN": "https://t@h", "POSTHOG_API_KEY": "phc_x"})
    assert isinstance(p, PostHogProvider)


def test_env_explicit_uptrace() -> None:
    p = provider_from_env({"TELEMETRY_PROVIDER": "uptrace", "UPTRACE_DSN": "https://t@h"})
    assert isinstance(p, UptraceProvider)


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"TELEMETRY_PROVIDER": "uptrace"}, "UPTRACE_DSN"),
        ({"TELEMETRY_PROVIDER": "posthog"}, "POSTHOG_API_KEY"),
        ({"TELEMETRY_PROVIDER": "datadog"}, "unknown"),
    ],
)
def test_env_explicit_misconfigured(env: dict[str, str], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        provider_from_env(env)


def test_env_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("TELEMETRY_PROVIDER", "UPTRACE_DSN", "OTEL_DSN", "POSTHOG_API_KEY", "POSTHOG_HOST"):
        monkeypatch.delenv(var, raising=False)
    assert provider_from_env() is None
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_env")
    assert isinstance(provider_from_env(), PostHogProvider)


def test_provider_tuning_applied() -> None:
    from unittest.mock import patch

    from pincer_telemetry import OtelTuning

    class Tuned(FakeProvider):
        def tuning(self) -> OtelTuning:
            return OtelTuning(max_queue_size=10, max_export_batch_size=5, export_interval_millis=1234)

    with (
        patch("opentelemetry.sdk.trace.export.BatchSpanProcessor") as bsp,
        patch("opentelemetry.sdk._logs.export.BatchLogRecordProcessor") as blp,
        patch("opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader") as reader,
        patch("opentelemetry.sdk.metrics.MeterProvider"),
        patch("opentelemetry.sdk.trace.TracerProvider"),
        patch("opentelemetry.sdk._logs.LoggerProvider"),
    ):
        _init(Tuned())

    assert bsp.call_args.kwargs == {"max_queue_size": 10, "max_export_batch_size": 5}
    assert blp.call_args.kwargs == {"max_queue_size": 10, "max_export_batch_size": 5}
    assert reader.call_args.kwargs == {"export_interval_millis": 1234}


# ---------------------------------------------------------------------------
# provider_from_prefixed_env()
# ---------------------------------------------------------------------------

_UPTRACE = "https://tok@api.uptrace.dev?grpc=4317"
_POSTHOG = "https://phc_secret@eu.i.posthog.com"


def test_prefixed_nothing_configured() -> None:
    assert provider_from_prefixed_env("TELEMETRY_", {}) is None


def test_prefixed_ignores_other_prefixes() -> None:
    env = {"OTEL_DSN": _UPTRACE, "PINCER_TELEMETRY_DSN": _UPTRACE, "MY_TELEMETRY_X": "1"}
    assert provider_from_prefixed_env("TELEMETRY_", env) is None


def test_prefixed_empty_values_are_unset() -> None:
    env = {"TELEMETRY_UPTRACE_DSN": "", "TELEMETRY_POSTHOG_DSN": "  ", "TELEMETRY_PROVIDER": ""}
    assert provider_from_prefixed_env("TELEMETRY_", env) is None


def test_prefixed_uptrace() -> None:
    p = provider_from_prefixed_env("TELEMETRY_", {"TELEMETRY_UPTRACE_DSN": _UPTRACE})
    assert isinstance(p, UptraceProvider)
    assert p.dsn == _UPTRACE


def test_prefixed_posthog() -> None:
    p = provider_from_prefixed_env("PINCER_TELEMETRY_", {"PINCER_TELEMETRY_POSTHOG_DSN": _POSTHOG})
    assert isinstance(p, PostHogProvider)
    assert p.api_key == "phc_secret"
    assert p.host == "https://eu.i.posthog.com"


def test_prefixed_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_UPTRACE_DSN", _UPTRACE)
    assert isinstance(provider_from_prefixed_env(), UptraceProvider)


def test_prefixed_both_providers_raise() -> None:
    env = {"TELEMETRY_UPTRACE_DSN": _UPTRACE, "TELEMETRY_POSTHOG_DSN": _POSTHOG}
    with pytest.raises(TelemetryConfigError) as exc:
        provider_from_prefixed_env("TELEMETRY_", env)
    msg = str(exc.value)
    assert "TELEMETRY_UPTRACE_DSN" in msg
    assert "TELEMETRY_POSTHOG_DSN" in msg
    assert "tok" not in msg
    assert "phc_secret" not in msg


@pytest.mark.parametrize("key", ["TELEMETRY_PROVIDER", "TELEMETRY_DSN", "TELEMETRY_UPTRACE", "TELEMETRY_DATADOG_DSN"])
def test_prefixed_unknown_key_raises(key: str) -> None:
    with pytest.raises(TelemetryConfigError, match=key) as exc:
        provider_from_prefixed_env("TELEMETRY_", {key: "secret-value"})
    assert "secret-value" not in str(exc.value)


def test_prefixed_is_a_value_error() -> None:
    assert issubclass(TelemetryConfigError, ValueError)


@pytest.mark.parametrize(
    "dsn",
    ["https://us.i.posthog.com", "ftp://phc_secret@h", "https://phc_secret@h/path", "https://phc_secret@h?x=1"],
)
def test_prefixed_malformed_posthog_dsn_raises(dsn: str) -> None:
    with pytest.raises(TelemetryConfigError, match="TELEMETRY_POSTHOG_DSN") as exc:
        provider_from_prefixed_env("TELEMETRY_", {"TELEMETRY_POSTHOG_DSN": dsn})
    assert "phc_secret" not in str(exc.value)


@pytest.mark.parametrize(
    "dsn",
    ["https://api.uptrace.dev", "api.uptrace.dev", "grpc://s3cr3t@h", "https://s3cr3t@:4317", "https://s3cr3t@h:x"],
)
def test_prefixed_malformed_uptrace_dsn_raises(dsn: str) -> None:
    with pytest.raises(TelemetryConfigError, match="TELEMETRY_UPTRACE_DSN") as exc:
        provider_from_prefixed_env("TELEMETRY_", {"TELEMETRY_UPTRACE_DSN": dsn})
    assert "s3cr3t" not in str(exc.value)
