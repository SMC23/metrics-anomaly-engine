"""Shared data contracts (ARCHITECTURE.md section 4).

Every value that crosses an HTTP, Redis or database boundary is one of these models.
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_BATCH_SIZE = 1000

HOST_PATTERN = r"^[a-z0-9-]{1,64}$"

Percent = Annotated[float, Field(ge=0.0, le=100.0)]
NonNegative = Annotated[float, Field(ge=0.0)]

AlertMetric = Literal["cpu_pct", "mem_pct", "latency_ms", "rps", "multivariate"]
MULTIVARIATE: AlertMetric = "multivariate"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertState(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class DetectorName(StrEnum):
    RULES = "rules"
    EWMA = "ewma"
    IFOREST = "iforest"


class _Contract(BaseModel):
    """Base for wire contracts: unknown fields are rejected, NaN and infinity too."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class Metric(_Contract):
    """One sample of the four metrics for one host (section 4.1)."""

    host: str = Field(pattern=HOST_PATTERN)
    ts: AwareDatetime
    cpu_pct: Percent
    mem_pct: Percent
    latency_ms: NonNegative
    rps: NonNegative

    @field_validator("ts")
    @classmethod
    def _ts_not_too_far_in_future(cls, value: datetime) -> datetime:
        value = _to_utc(value)
        limit = datetime.now(UTC) + MAX_FUTURE_SKEW
        if value > limit:
            raise ValueError(f"ts is more than {MAX_FUTURE_SKEW} in the future")
        return value


class MetricBatch(_Contract):
    """Body of `POST /v1/metrics` (section 4.2)."""

    metrics: list[Metric] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


def _as_text(value: str | bytes) -> str:
    # Undecodable bytes become U+FFFD, so the model rejects them as a validation error.
    return value.decode(errors="replace") if isinstance(value, bytes) else value


class StreamMessage(_Contract):
    """One entry of a `metrics:raw:{p}` stream (section 4.3).

    In Redis every field is a string: `payload` holds the Metric as JSON and
    `ingested_at_ms` holds the epoch milliseconds assigned by the gateway.
    """

    payload: Metric
    ingested_at_ms: int = Field(ge=0)

    @field_validator("payload", mode="before")
    @classmethod
    def _parse_payload_json(cls, value: object) -> object:
        # json.loads raises ValueError subclasses (also for invalid UTF-8 bytes),
        # which Pydantic reports as a normal validation error.
        if isinstance(value, str | bytes):
            return json.loads(value)
        return value

    def to_stream_fields(self) -> dict[str, str]:
        return {
            "payload": self.payload.model_dump_json(),
            "ingested_at_ms": str(self.ingested_at_ms),
        }

    @classmethod
    def from_stream_fields(cls, fields: Mapping[str, str] | Mapping[bytes, bytes]) -> Self:
        """Parse the field map returned by XREADGROUP (decoded or raw bytes).

        Raises `pydantic.ValidationError` for any missing, extra or invalid field,
        including bytes that are not valid UTF-8, so callers handle a single error type.
        """
        data: dict[str, object] = {}
        for key, value in fields.items():
            name = _as_text(key)
            data[name] = value if name == "payload" else _as_text(value)
        return cls.model_validate(data)


class Alert(_Contract):
    """An alert as stored in `alerts` and published on `alerts:events` (section 4.4)."""

    alert_id: UUID
    host: str = Field(pattern=HOST_PATTERN)
    metric: AlertMetric
    detector: DetectorName
    severity: Severity
    state: AlertState
    score: float
    message: str = Field(min_length=1)
    started_at: AwareDatetime
    updated_at: AwareDatetime
    resolved_at: AwareDatetime | None = None

    @field_validator("started_at", "updated_at", "resolved_at")
    @classmethod
    def _normalize_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _to_utc(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if (self.detector is DetectorName.IFOREST) != (self.metric == MULTIVARIATE):
            raise ValueError("metric must be 'multivariate' if and only if detector is iforest")
        if self.updated_at < self.started_at:
            raise ValueError("updated_at must not be before started_at")
        if self.state is AlertState.FIRING and self.resolved_at is not None:
            raise ValueError("a firing alert must not have resolved_at")
        if self.state is AlertState.RESOLVED:
            if self.resolved_at is None:
                raise ValueError("a resolved alert must have resolved_at")
            if self.resolved_at < self.started_at:
                raise ValueError("resolved_at must not be before started_at")
        return self
