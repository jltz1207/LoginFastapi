from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import settings

_saver: AsyncPostgresSaver | None = None

def get_checkpointer():
    if _saver is None:
        raise RuntimeError("Checkpointer not initialized — call setup_checkpointer() at startup")
    return _saver


@asynccontextmanager
async def checkpointer_lifespan():
    global _saver
    clean_url = settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(clean_url) as saver:
        await saver.setup()
        _saver = saver
        yield
        _saver = None
