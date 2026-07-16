from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import settings

_saver: AsyncPostgresSaver | None = None
_pool: AsyncConnectionPool | None = None

def get_checkpointer():
    if _saver is None:
        raise RuntimeError("Checkpointer not initialized — call setup_checkpointer() at startup")
    return _saver


@asynccontextmanager
async def checkpointer_lifespan():
    global _saver, _pool
    clean_url = settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    # A pooled connection is required here (rather than the single AsyncConnection
    # from_conn_string opens) because that single connection is held for the whole
    # app lifetime — if the DB/proxy drops it while idle, every request after that
    # fails with "server closed the connection unexpectedly". check=check_connection
    # validates the connection on every checkout and transparently swaps in a fresh
    # one if it's gone stale.
    pool = AsyncConnectionPool(
        conninfo=clean_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(conn=pool)
        await saver.setup()
        _saver = saver
        _pool = pool
        yield
    finally:
        _saver = None
        _pool = None
        await pool.close()
