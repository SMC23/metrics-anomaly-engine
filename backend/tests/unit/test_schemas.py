import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from engine.schemas import (
    MAX_BATCH_SIZE,
    Alert,
    AlertState,
    DetectorName,
    Metric,
    MetricBatch,
    Severity,
    StreamMessage,
)

TS = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)


def metric_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "host": "web-01",
        "ts": "2026-11-08T20:00:00.000Z",
        "cpu_pct": 37.2,
        "mem_pct": 61.0,
        "latency_ms": 120.5,
        "rps": 340.0,
    }
    data.update(overrides)
    return data


def alert_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "alert_id": str(uuid4()),
        "host": "web-01",
        "metric": "mem_pct",
        "detector": "rules",
        "severity": "CRITICAL",
        "state": "firing",
        "score": 0.73,
        "message": "mem_pct above 90",
        "started_at": TS,
        "updated_at": TS,
        "resolved_at": None,
    }
    data.update(overrides)
    return data


# --- Metric ---------------------------------------------------------------------------------


def test_architecture_example_is_valid() -> None:
    metric = Metric.model_validate(metric_data())

    assert metric.host == "web-01"
    assert metric.ts == TS
    assert metric.cpu_pct == 37.2


def test_ts_is_normalized_to_utc() -> None:
    metric = Metric.model_validate(metric_data(ts="2026-11-08T15:00:00-05:00"))

    assert metric.ts == TS
    assert metric.ts.tzinfo == UTC


@pytest.mark.parametrize("host", ["a", "web-01", "0", "-", "a" * 64])
def test_valid_hosts(host: str) -> None:
    assert Metric.model_validate(metric_data(host=host)).host == host


@pytest.mark.parametrize("host", ["", "a" * 65, "Web-01", "web_01", "web.01", "web 01", "wéb"])
def test_invalid_hosts(host: str) -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate(metric_data(host=host))


@pytest.mark.parametrize("field", ["cpu_pct", "mem_pct"])
@pytest.mark.parametrize("value", [0.0, 100.0])
def test_percent_bounds_are_inclusive(field: str, value: float) -> None:
    assert getattr(Metric.model_validate(metric_data(**{field: value})), field) == value


@pytest.mark.parametrize("field", ["cpu_pct", "mem_pct"])
@pytest.mark.parametrize("value", [-0.001, 100.001])
def test_percent_out_of_range(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate(metric_data(**{field: value}))


@pytest.mark.parametrize("field", ["latency_ms", "rps"])
def test_non_negative_fields_accept_zero(field: str) -> None:
    assert getattr(Metric.model_validate(metric_data(**{field: 0.0})), field) == 0.0


@pytest.mark.parametrize("field", ["latency_ms", "rps"])
def test_non_negative_fields_reject_negative(field: str) -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate(metric_data(**{field: -0.001}))


@pytest.mark.parametrize("field", ["cpu_pct", "mem_pct", "latency_ms", "rps"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nan_and_infinity_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate(metric_data(**{field: value}))


def test_naive_ts_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate(metric_data(ts="2026-11-08T20:00:00"))


def test_ts_slightly_in_future_is_accepted() -> None:
    ts = datetime.now(UTC) + timedelta(minutes=4, seconds=50)
    assert Metric.model_validate(metric_data(ts=ts)).ts == ts


def test_ts_more_than_five_minutes_in_future_is_rejected() -> None:
    ts = datetime.now(UTC) + timedelta(minutes=5, seconds=10)
    with pytest.raises(ValidationError, match="in the future"):
        Metric.model_validate(metric_data(ts=ts))


def test_future_check_uses_absolute_time_not_wall_clock_of_offset() -> None:
    # 4 minutes ahead expressed in UTC-05:00 is still 4 minutes ahead.
    ts = (datetime.now(UTC) + timedelta(minutes=4)).astimezone(timezone(timedelta(hours=-5)))
    assert Metric.model_validate(metric_data(ts=ts)).ts == ts


@pytest.mark.parametrize("field", ["host", "ts", "cpu_pct", "mem_pct", "latency_ms", "rps"])
def test_missing_field_is_rejected(field: str) -> None:
    data = metric_data()
    del data[field]
    with pytest.raises(ValidationError):
        Metric.model_validate(data)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Metric.model_validate(metric_data(disk_pct=10.0))


def test_metric_is_immutable() -> None:
    metric = Metric.model_validate(metric_data())
    with pytest.raises(ValidationError):
        metric.cpu_pct = 1.0  # type: ignore[misc]


def test_json_round_trip() -> None:
    metric = Metric.model_validate(metric_data())
    assert Metric.model_validate_json(metric.model_dump_json()) == metric


# --- MetricBatch ----------------------------------------------------------------------------


@pytest.mark.parametrize("size", [1, MAX_BATCH_SIZE])
def test_batch_size_bounds_are_inclusive(size: int) -> None:
    batch = MetricBatch.model_validate({"metrics": [metric_data()] * size})
    assert len(batch.metrics) == size


@pytest.mark.parametrize("size", [0, MAX_BATCH_SIZE + 1])
def test_batch_size_out_of_range(size: int) -> None:
    with pytest.raises(ValidationError):
        MetricBatch.model_validate({"metrics": [metric_data()] * size})


def test_batch_rejects_if_any_item_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MetricBatch.model_validate({"metrics": [metric_data(), metric_data(cpu_pct=101)]})
    assert exc_info.value.errors()[0]["loc"] == ("metrics", 1, "cpu_pct")


# --- StreamMessage --------------------------------------------------------------------------


def test_stream_fields_round_trip() -> None:
    message = StreamMessage(payload=Metric.model_validate(metric_data()), ingested_at_ms=1234)

    fields = message.to_stream_fields()

    assert set(fields) == {"payload", "ingested_at_ms"}
    assert fields["ingested_at_ms"] == "1234"
    assert json.loads(fields["payload"])["host"] == "web-01"
    assert StreamMessage.from_stream_fields(fields) == message


def test_stream_fields_accept_raw_bytes() -> None:
    message = StreamMessage(payload=Metric.model_validate(metric_data()), ingested_at_ms=1234)
    raw = {k.encode(): v.encode() for k, v in message.to_stream_fields().items()}

    assert StreamMessage.from_stream_fields(raw) == message


@pytest.mark.parametrize(
    "fields",
    [
        {"ingested_at_ms": "1"},
        {"payload": json.dumps(metric_data())},
        {"payload": "not json", "ingested_at_ms": "1"},
        {"payload": json.dumps(metric_data(cpu_pct=150)), "ingested_at_ms": "1"},
        {"payload": json.dumps(metric_data()), "ingested_at_ms": "abc"},
        {"payload": json.dumps(metric_data()), "ingested_at_ms": "-1"},
        {"payload": json.dumps(metric_data()), "ingested_at_ms": "1", "extra": "x"},
    ],
    ids=["no-payload", "no-ingested", "bad-json", "bad-metric", "bad-int", "negative", "extra"],
)
def test_invalid_stream_fields(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        StreamMessage.from_stream_fields(fields)


@pytest.mark.parametrize(
    "fields",
    [
        {b"payload": b"\xff\xfe", b"ingested_at_ms": b"1"},
        {b"payload": json.dumps(metric_data()).encode(), b"ingested_at_ms": b"\xff"},
        {b"\xff": b"1", b"payload": json.dumps(metric_data()).encode()},
    ],
    ids=["payload", "ingested", "key"],
)
def test_invalid_utf8_is_a_validation_error(fields: dict[bytes, bytes]) -> None:
    with pytest.raises(ValidationError):
        StreamMessage.from_stream_fields(fields)


# --- Alert ----------------------------------------------------------------------------------


def test_valid_firing_alert() -> None:
    alert = Alert.model_validate(alert_data())

    assert alert.detector is DetectorName.RULES
    assert alert.severity is Severity.CRITICAL
    assert alert.state is AlertState.FIRING


def test_valid_resolved_alert() -> None:
    alert = Alert.model_validate(
        alert_data(state="resolved", updated_at=TS + timedelta(minutes=1), resolved_at=TS)
    )
    assert alert.resolved_at == TS


def test_iforest_alert_uses_multivariate() -> None:
    alert = Alert.model_validate(alert_data(detector="iforest", metric="multivariate"))
    assert alert.metric == "multivariate"


def test_iforest_alert_with_single_metric_is_rejected() -> None:
    with pytest.raises(ValidationError, match="multivariate"):
        Alert.model_validate(alert_data(detector="iforest", metric="cpu_pct"))


@pytest.mark.parametrize("detector", ["rules", "ewma"])
def test_univariate_detector_with_multivariate_is_rejected(detector: str) -> None:
    with pytest.raises(ValidationError, match="multivariate"):
        Alert.model_validate(alert_data(detector=detector, metric="multivariate"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("detector", "zscore"),
        ("severity", "warning"),
        ("state", "FIRING"),
        ("metric", "disk_pct"),
        ("alert_id", "not-a-uuid"),
        ("message", ""),
        ("score", float("nan")),
        ("host", "Web-01"),
    ],
)
def test_invalid_alert_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Alert.model_validate(alert_data(**{field: value}))


def test_firing_alert_with_resolved_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="firing"):
        Alert.model_validate(alert_data(resolved_at=TS))


def test_resolved_alert_without_resolved_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="resolved_at"):
        Alert.model_validate(alert_data(state="resolved"))


def test_updated_before_started_is_rejected() -> None:
    with pytest.raises(ValidationError, match="updated_at"):
        Alert.model_validate(alert_data(updated_at=TS - timedelta(seconds=1)))


def test_resolved_before_started_is_rejected() -> None:
    with pytest.raises(ValidationError, match="resolved_at"):
        Alert.model_validate(alert_data(state="resolved", resolved_at=TS - timedelta(seconds=1)))


def test_naive_alert_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Alert.model_validate(alert_data(started_at=datetime(2026, 11, 8, 20, 0)))


def test_alert_json_round_trip() -> None:
    alert = Alert.model_validate(alert_data())
    assert Alert.model_validate_json(alert.model_dump_json()) == alert
