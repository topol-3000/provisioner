"""structlog configuration.

Per docs/architecture.md §Observability: JSON output in non-dev environments,
correlation IDs propagated via context vars, fields like `envelope_id`,
`subscription_id`, `instance_id` attached when in scope.

Call `configure_logging(settings)` once at app startup.
"""

import logging
import sys
from typing import TYPE_CHECKING

import structlog
from structlog.contextvars import merge_contextvars
from structlog.processors import (
    CallsiteParameter,
    CallsiteParameterAdder,
    JSONRenderer,
    TimeStamper,
    add_log_level,
    format_exc_info,
)
from structlog.stdlib import (
    BoundLogger,
    ProcessorFormatter,
    add_logger_name,
)

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Wire up structlog + stdlib logging. Idempotent.

    In dev, uses a colorized ConsoleRenderer for human-friendly output.
    In staging/prod, uses JSONRenderer so log aggregators can index fields.

    Args:
        settings: Application settings providing `environment` and `log_level`.
    """
    use_json = settings.environment != "dev"

    shared_processors: list = [
        merge_contextvars,
        add_log_level,
        add_logger_name,
        TimeStamper(fmt="iso", utc=True),
        CallsiteParameterAdder(
            parameters=[
                CallsiteParameter.MODULE,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            ],
        ),
        format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    renderer = JSONRenderer() if use_json else structlog.dev.ConsoleRenderer(colors=True)

    formatter = ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Drop any pre-existing handlers so our handler is the sole output.
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    # NOTE: No Granian logger silencing — this worker uses no Granian.


def get_logger(name: str | None = None) -> BoundLogger:
    """Convenience wrapper. Always call after `configure_logging`.

    Args:
        name: Logger name (typically `__name__`).

    Returns:
        A structlog BoundLogger.
    """
    return structlog.get_logger(name)
