"""asyncpg connection pool."""

import asyncpg

from engine.config import Settings


async def create_pool(settings: Settings, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Open a pool to `DATABASE_URL`. The caller closes it with `await pool.close()`."""
    pool: asyncpg.Pool = await asyncpg.create_pool(
        dsn=str(settings.database_url),
        min_size=min_size,
        max_size=max_size,
    )
    return pool


async def check_connection(pool: asyncpg.Pool) -> None:
    """Run a trivial query; raises if the database is unreachable (used by /readyz)."""
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
