import asyncio
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_saver: AsyncPostgresSaver | None = None
_pool: AsyncConnectionPool | None = None

_SETUP_ATTEMPTS = 5
_SETUP_RETRY_DELAY_SECONDS = 5

def get_checkpointer():
    if _saver is None:
        raise RuntimeError("Checkpointer not initialized — call setup_checkpointer() at startup")
    return _saver


@asynccontextmanager
async def checkpointer_lifespan():
    global _saver, _pool
    clean_url = settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")

    def _new_pool() -> AsyncConnectionPool:
        # A pooled connection is required here (rather than the single AsyncConnection
        # from_conn_string opens) because that single connection is held for the whole
        # app lifetime — if the DB/proxy drops it while idle, every request after that
        # fails with "server closed the connection unexpectedly". check=check_connection
        # validates the connection on every checkout and transparently swaps in a fresh
        # one if it's gone stale.
        return AsyncConnectionPool(
            conninfo=clean_url,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            check=AsyncConnectionPool.check_connection,
        )

    # open(wait=False) is the default and returns immediately without confirming
    # Postgres is actually reachable — on a fresh boot the local Postgres service
    # can still be starting up, so a plain open() lets the app "start" successfully
    # and only surfaces the failure ~30s later, as a confusing PoolTimeout, on the
    # first request that touches the checkpointer. wait=True + a retry loop instead
    # blocks startup until the DB is truly ready, or fails loudly right here. A failed
    # pool cannot be reopened, so each retry gets a fresh AsyncConnectionPool.
    for attempt in range(1, _SETUP_ATTEMPTS + 1):
        pool = _new_pool()
        try:
            await pool.open(wait=True, timeout=_SETUP_RETRY_DELAY_SECONDS * 2)
            saver = AsyncPostgresSaver(conn=pool)
            await saver.setup()
            break
        except PoolTimeout:
            await pool.close()
            if attempt == _SETUP_ATTEMPTS:
                raise
            logger.warning(
                "checkpointer_db_not_ready",
                attempt=attempt,
                max_attempts=_SETUP_ATTEMPTS,
                retry_in_seconds=_SETUP_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(_SETUP_RETRY_DELAY_SECONDS)

    try:
        _saver = saver
        _pool = pool
        yield
    finally:
        _saver = None
        _pool = None
        await pool.close()
