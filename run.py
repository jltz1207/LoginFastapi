"""
Dev-server entrypoint used by the VS Code debug launch config.

Windows' default event loop (ProactorEventLoop, which `python -m uvicorn` uses
whenever it isn't running in --reload/multi-worker subprocess mode) cannot run
psycopg in async mode — every checkpointer DB connection attempt fails and the
app eventually dies with psycopg_pool.PoolTimeout. Running uvicorn inside an
explicitly-created SelectorEventLoop avoids that.

`uv run uvicorn app.main:app --reload` is unaffected (reload implies a
subprocess worker, which uvicorn already runs on SelectorEventLoop) and can
still be used as documented in CLAUDE.md.
"""
import asyncio
import sys

import uvicorn


async def _serve() -> None:
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8000)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())
    else:
        asyncio.run(_serve())
