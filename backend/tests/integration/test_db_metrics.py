"""Idempotent inserts into `metrics` and the real-time `metrics_1m` aggregate."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from engine.db.metrics import insert_metrics
from engine.schemas import Metric

pytestmark = pytest.mark.integration

# Recent enough to be inside the retention window, aligned to a minute for the aggregate test.
BASE = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=30)


def make_metric(host: str, second: int, cpu: float = 10.0) -> Metric:
    return Metric(
        host=host,
        ts=BASE + timedelta(seconds=second),
        cpu_pct=cpu,
        mem_pct=50.0,
        latency_ms=100.0,
        rps=200.0,
    )


async def count_rows(pool: asyncpg.Pool, host: str) -> int:
    count: int = await pool.fetchval("SELECT count(*) FROM metrics WHERE host = $1", host)
    return count


async def test_insert_returns_new_rows_and_stores_values(
    pool: asyncpg.Pool, test_host: str
) -> None:
    batch = [make_metric(test_host, s, cpu=float(s)) for s in range(3)]

    assert await insert_metrics(pool, batch) == 3

    rows = await pool.fetch(
        "SELECT time, host, cpu_pct, mem_pct, latency_ms, rps FROM metrics "
        "WHERE host = $1 ORDER BY time",
        test_host,
    )
    stored = [
        Metric(
            host=r["host"],
            ts=r["time"],
            cpu_pct=r["cpu_pct"],
            mem_pct=r["mem_pct"],
            latency_ms=r["latency_ms"],
            rps=r["rps"],
        )
        for r in rows
    ]
    assert stored == batch


async def test_redelivered_batch_is_not_duplicated(pool: asyncpg.Pool, test_host: str) -> None:
    batch = [make_metric(test_host, s) for s in range(5)]

    assert await insert_metrics(pool, batch) == 5
    assert await insert_metrics(pool, batch) == 0
    assert await count_rows(pool, test_host) == 5


async def test_conflict_keeps_first_write(pool: asyncpg.Pool, test_host: str) -> None:
    await insert_metrics(pool, [make_metric(test_host, 0, cpu=10.0)])
    await insert_metrics(pool, [make_metric(test_host, 0, cpu=99.0)])

    cpu = await pool.fetchval("SELECT cpu_pct FROM metrics WHERE host = $1", test_host)
    assert cpu == 10.0


async def test_partially_overlapping_batch_inserts_only_new_rows(
    pool: asyncpg.Pool, test_host: str
) -> None:
    await insert_metrics(pool, [make_metric(test_host, s) for s in range(3)])

    inserted = await insert_metrics(pool, [make_metric(test_host, s) for s in range(1, 5)])

    assert inserted == 2
    assert await count_rows(pool, test_host) == 5


async def test_duplicates_inside_one_batch(pool: asyncpg.Pool, test_host: str) -> None:
    metric = make_metric(test_host, 0)

    assert await insert_metrics(pool, [metric, metric, metric]) == 1
    assert await count_rows(pool, test_host) == 1


async def test_same_time_on_different_hosts_is_not_a_conflict(
    pool: asyncpg.Pool, test_host: str
) -> None:
    other = f"{test_host}-b"
    try:
        assert await insert_metrics(pool, [make_metric(test_host, 0), make_metric(other, 0)]) == 2
    finally:
        await pool.execute("DELETE FROM metrics WHERE host = $1", other)


async def test_insert_inside_transaction_is_rolled_back(pool: asyncpg.Pool, test_host: str) -> None:
    async with pool.acquire() as conn:
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await insert_metrics(conn, [make_metric(test_host, 0)])
                raise RuntimeError("simulated crash before commit")

    assert await count_rows(pool, test_host) == 0


async def test_empty_batch_is_a_no_op(pool: asyncpg.Pool) -> None:
    assert await insert_metrics(pool, []) == 0


async def test_real_time_aggregate_sees_rows_before_refresh(
    pool: asyncpg.Pool, test_host: str
) -> None:
    await insert_metrics(
        pool, [make_metric(test_host, 0, cpu=10.0), make_metric(test_host, 30, cpu=30.0)]
    )

    row = await pool.fetchrow(
        "SELECT bucket, cpu_pct_avg, cpu_pct_max FROM metrics_1m WHERE host = $1", test_host
    )

    assert row is not None
    assert row["bucket"] == BASE
    assert row["cpu_pct_avg"] == pytest.approx(20.0)
    assert row["cpu_pct_max"] == 30.0


async def test_alerts_rejects_resolved_without_resolved_at(
    pool: asyncpg.Pool, test_host: str
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO alerts (alert_id, host, metric, detector, severity, state, message,
                                score, started_at, updated_at, resolved_at)
            VALUES ($1, $2, 'cpu_pct', 'rules', 'CRITICAL', 'resolved', 'm', 1.0,
                    now(), now(), NULL)
            """,
            uuid4(),
            test_host,
        )
