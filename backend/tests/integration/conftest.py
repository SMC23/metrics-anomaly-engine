"""Fixtures for integration tests against the real TimescaleDB from `make up`."""

from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest

from engine.config import Settings
from engine.db.pool import create_pool


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    db_pool = await create_pool(Settings(), max_size=2)
    try:
        yield db_pool
    finally:
        await db_pool.close()


@pytest.fixture
async def test_host(pool: asyncpg.Pool) -> AsyncIterator[str]:
    """A host name unique to the test; its rows are deleted afterwards."""
    host = f"it-{uuid4().hex[:12]}"
    try:
        yield host
    finally:
        await pool.execute("DELETE FROM metrics WHERE host = $1", host)
        await pool.execute("DELETE FROM alerts WHERE host = $1", host)
