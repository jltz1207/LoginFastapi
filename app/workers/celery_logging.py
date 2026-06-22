"""
Celery signal hooks that inject task_id and task_name into the structlog
context for every worker log line produced during a task's lifetime.

Call setup_celery_logging() once, right after setup_logging(), in your
celery_app.py:

    from app.core.logging import setup_logging
    from app.workers.celery_logging import setup_celery_logging

    setup_logging()
    setup_celery_logging()
"""
import structlog
from celery.signals import task_failure, task_postrun, task_prerun

from app.core.logging import get_logger

logger = get_logger(__name__)


def setup_celery_logging() -> None:
    """Connect all Celery log signals. Call once at worker startup."""
    task_prerun.connect(_on_task_prerun)
    task_postrun.connect(_on_task_postrun)
    task_failure.connect(_on_task_failure)


# --------------------------------------------------------------------------- #
# Signal handlers
# --------------------------------------------------------------------------- #

def _on_task_prerun(task_id: str, task, *args, **kwargs) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        task_id=task_id,
        task_name=task.name,
    )
    logger.info("task.started")


def _on_task_postrun(
    task_id: str, task, retval, state: str, *args, **kwargs
) -> None:
    logger.info("task.finished", state=state)
    structlog.contextvars.clear_contextvars()


def _on_task_failure(
    task_id: str, exception: Exception, traceback, einfo, *args, **kwargs
) -> None:
    logger.error(
        "task.failed",
        exc_info=True,
        error_type=type(exception).__name__,
        error=str(exception),
    )
