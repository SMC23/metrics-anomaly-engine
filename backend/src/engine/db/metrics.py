"""Queries on the `metrics` hypertable."""

from collections.abc import Sequence

import asyncpg

from engine.schemas import Metric

# unnest() turns five parallel arrays into rows, so a whole batch is a single statement and a
# single round trip. ON CONFLICT DO NOTHING makes redelivered messages harmless (at-least-once
# delivery + idempotent write), including duplicates inside the same batch.
_INSERT_METRICS = """
INSERT INTO metrics (time, host, cpu_pct, mem_pct, latency_ms, rps)
SELECT * FROM unnest(
    $1::timestamptz[], $2::text[],
    $3::double precision[], $4::double precision[], $5::double precision[], $6::double precision[]
)
ON CONFLICT (host, time) DO NOTHING
"""


async def insert_metrics(conn: asyncpg.Pool | asyncpg.Connection, metrics: Sequence[Metric]) -> int:
    """Insert a batch idempotently and return how many rows were new."""
    if not metrics:
        return 0
    status: str = await conn.execute(
        _INSERT_METRICS,
        [m.ts for m in metrics],
        [m.host for m in metrics],
        [m.cpu_pct for m in metrics],
        [m.mem_pct for m in metrics],
        [m.latency_ms for m in metrics],
        [m.rps for m in metrics],
    )
    # Command tag for INSERT is "INSERT <oid> <rows>".
    return int(status.rsplit(" ", 1)[1])
