import structlog
import logging
import os
from logging.handlers import RotatingFileHandler
from config import get_settings

settings = get_settings()

os.makedirs(settings.log_dir, exist_ok=True)

_log_handler = None


def _get_handler():
    global _log_handler
    if _log_handler is None:
        _log_handler = RotatingFileHandler(
            os.path.join(settings.log_dir, "backend.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        _log_handler.setFormatter(logging.Formatter("%(message)s"))
    return _log_handler


def setup_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer() if settings.log_level == "DEBUG"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root_logger.addHandler(_get_handler())


def get_logger(name: str = __name__):
    return structlog.get_logger(name)
