"""The init scripts in db/init/ produce the schema of ARCHITECTURE.md section 6."""

from datetime import timedelta
from typing import Any

import asyncpg
import pytest

pytestmark = pytest.mark.integration


async def test_timescaledb_extension_is_installed(pool: asyncpg.Pool) -> None:
    version = await pool.fetchval(
        "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
    )
    assert version is not None
    assert version.startswith("2.")


async def test_metrics_is_a_hypertable_with_daily_chunks(pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow(
        """
        SELECT column_name, time_interval
        FROM timescaledb_information.dimensions
        WHERE hypertable_schema = 'public' AND hypertable_name = 'metrics'
        """
    )
    assert row is not None
    assert row["column_name"] == "time"
    assert row["time_interval"] == timedelta(days=1)


async def test_metrics_columns(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'metrics'
        """
    )
    columns = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in rows}
    assert columns == {
        "time": ("timestamp with time zone", "NO"),
        "host": ("text", "NO"),
        "cpu_pct": ("double precision", "NO"),
        "mem_pct": ("double precision", "NO"),
        "latency_ms": ("double precision", "NO"),
        "rps": ("double precision", "NO"),
    }


async def _primary_key(pool: asyncpg.Pool, table: str) -> list[str]:
    rows = await pool.fetch(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, pos) ON true
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
        WHERE i.indrelid = $1::regclass AND i.indisprimary
        ORDER BY k.pos
        """,
        table,
    )
    return [r["attname"] for r in rows]


async def test_metrics_primary_key_is_host_time(pool: asyncpg.Pool) -> None:
    assert await _primary_key(pool, "public.metrics") == ["host", "time"]


async def test_alerts_primary_key_is_alert_id(pool: asyncpg.Pool) -> None:
    assert await _primary_key(pool, "public.alerts") == ["alert_id"]


async def test_alerts_indexes(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'alerts'"
    )
    definitions = {r["indexdef"].split(" USING btree ")[1] for r in rows}
    assert "(state, updated_at DESC)" in definitions
    assert "(host, started_at DESC)" in definitions


async def test_continuous_aggregate_is_real_time(pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow(
        """
        SELECT materialized_only
        FROM timescaledb_information.continuous_aggregates
        WHERE view_schema = 'public' AND view_name = 'metrics_1m'
        """
    )
    assert row is not None
    assert row["materialized_only"] is False


async def _jobs(pool: asyncpg.Pool, proc_name: str) -> dict[str, Any]:
    """Map 'metrics' / 'metrics_1m' to the job config of `proc_name` on it."""
    rows = await pool.fetch(
        """
        SELECT coalesce(ca.view_name, j.hypertable_name) AS target, j.config::text AS config
        FROM timescaledb_information.jobs j
        LEFT JOIN timescaledb_information.continuous_aggregates ca
               ON ca.materialization_hypertable_name = j.hypertable_name
        WHERE j.proc_name = $1
        """,
        proc_name,
    )
    return {r["target"]: r["config"] for r in rows}


async def test_retention_policies(pool: asyncpg.Pool) -> None:
    jobs = await _jobs(pool, "policy_retention")
    assert set(jobs) == {"metrics", "metrics_1m"}
    assert '"drop_after": "7 days"' in jobs["metrics"]
    assert '"drop_after": "90 days"' in jobs["metrics_1m"]


async def test_continuous_aggregate_refresh_policy(pool: asyncpg.Pool) -> None:
    jobs = await _jobs(pool, "policy_refresh_continuous_aggregate")
    assert set(jobs) == {"metrics_1m"}
    assert '"start_offset": "02:00:00"' in jobs["metrics_1m"]
    assert '"end_offset": "00:01:00"' in jobs["metrics_1m"]
