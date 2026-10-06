import json
import logging
import logging.config
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

THIRD_PARTY_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "faststream")
STANDARD_RECORD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None)).keys() | {"message", "asctime", "color_message"}
)

_log_context: ContextVar[Mapping[str, Any]] = ContextVar("log_context", default=MappingProxyType({}))


@contextmanager
def bind_log_context(**fields: Any) -> Iterator[None]:
    token = _log_context.set(MappingProxyType({**_log_context.get(), **fields}))
    try:
        yield
    finally:
        _log_context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_log_context.get(),
            **{key: value for key, value in vars(record).items() if key not in STANDARD_RECORD_ATTRIBUTES},
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: LogLevel) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "stdout": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"},
            },
            "loggers": {name: {"handlers": [], "propagate": True} for name in THIRD_PARTY_LOGGERS},
            "root": {"level": level, "handlers": ["stdout"]},
        }
    )
