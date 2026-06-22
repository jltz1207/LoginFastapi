"""
FastAPI middleware that:
  1. Clears structlog context vars at the start of every request
     (prevents context leaking across async tasks that reuse workers).
  2. Injects a unique request_id — taken from the incoming X-Request-ID
     header when present, otherwise a fresh UUID4.
  3. Logs request start / finish with method, path, status code, and
     wall-clock duration in milliseconds.
  4. Echoes the request_id back in the X-Request-ID response header so
     clients can correlate logs with their own traces.
"""
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Always start with a clean slate — crucial for async worker reuse.
        structlog.contextvars.clear_contextvars()

        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        logger.info(
            "request.started",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else None,
        )

        try:
            response: Response = await call_next(request)
        except Exception:
            logger.exception(
                "request.unhandled_error",
                method=request.method,
                path=request.url.path,
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request.finished",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response
