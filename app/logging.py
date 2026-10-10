"""Bounded console events: never serialize input, log arguments or tracebacks."""

import contextlib
import contextvars
import json
import logging
import math
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_EVENTS = {
    "application.started",
    "application.stopped",
    "application.startup_failed",
    "request.completed",
    "task.created",
    "task.stage",
    "task.failed",
    "task.quota_release_deferred",
    "task.cleanup_deferred",
    "task.cleaned",
    "protection.memory",
    "protection.redis_connected",
    "protection.redis_degraded",
}
_SERVER_EVENTS = {
    "Started server process": "server.started",
    "Waiting for application startup": "server.starting",
    "Application startup complete": "server.ready",
    "Uvicorn running on": "server.listening",
    "Shutting down": "server.stopping",
    "Waiting for application shutdown": "server.shutdown",
    "Application shutdown complete": "server.stopped",
    "Finished server process": "server.finished",
    "Exception in ASGI application": "server.request_failed",
    "Application startup failed": "server.startup_failed",
}
_ROUTES = {
    "/",
    "/healthz",
    "/login",
    "/logout",
    "/static/{path}",
    "/api/capabilities",
    "/api/tasks/link",
    "/api/tasks/reservations",
    "/api/tasks/upload",
    "/api/tasks/{task_id}",
    "/api/tasks/{task_id}/upload",
    "/api/tasks/{task_id}/cancel",
    "/files/{task_id}",
    "unmatched",
}
_ENUM_FIELDS = {
    "method": {"GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"},
    "route": _ROUTES,
    "stage": {
        "receiving",
        "resolving",
        "downloading",
        "converting",
        "ready",
        "failed",
        "cancelled",
    },
    "backend": {"redis", "memory"},
    "previous_backend": {"redis", "memory"},
    "input_kind": {"upload", "link"},
    "app_env": {"local", "production"},
}


def get_request_id() -> str | None:
    return _request_id.get()


@contextlib.contextmanager
def request_context(request_id: str) -> Iterator[None]:
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Do not call getMessage(), formatException() or formatStack(): each can
        # include URLs, cookies, private filenames and original upstream errors.
        event = "application.log"
        if isinstance(record.msg, str) and record.msg in _EVENTS:
            event = record.msg
        elif record.name.startswith("uvicorn"):
            event = "server.log"
            if isinstance(record.msg, str):
                for prefix, value in _SERVER_EVENTS.items():
                    if record.msg.startswith(prefix):
                        event = value
                        break
        data = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
        }
        for key, allowed in _ENUM_FIELDS.items():
            value = record.__dict__.get(key)
            if isinstance(value, str) and value in allowed:
                data[key] = value
        for key in ("request_id", "task_id"):
            value = record.__dict__.get(key, get_request_id() if key == "request_id" else None)
            if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value):
                data[key] = value
        for key in ("error_category", "exception_type", "reason"):
            value = record.__dict__.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
                data[key] = value
        if record.exc_info and record.exc_info[0]:
            data["exception_type"] = record.exc_info[0].__name__
        for key in ("status_code", "elapsed_ms", "retry_after_seconds", "suppressed_count"):
            value = record.__dict__.get(key)
            if type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value):
                data[key] = round(value, 2)
        for key in ("media_available", "response_completed"):
            value = record.__dict__.get(key)
            if isinstance(value, bool):
                data[key] = value
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class _ConsoleHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        # Resolve stdout at emission, including reload and embedded/test hosts.
        self.stream = sys.stdout
        super().emit(record)


def setup_logging(level: str = "INFO") -> None:
    """Own the app console format while leaving unrelated host/root loggers alone."""
    application = logging.getLogger("app")
    application.setLevel(level)
    application.disabled = False
    application.propagate = False
    for handler in list(application.handlers):
        if isinstance(handler, _ConsoleHandler):
            application.removeHandler(handler)
            handler.close()
    handler = _ConsoleHandler()
    handler.setFormatter(JsonFormatter())
    application.addHandler(handler)
    # Replace the raw access log: its path/query and client address are private.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        server = logging.getLogger(name)
        server.setLevel(level)
        for handler in server.handlers:
            handler.setFormatter(JsonFormatter())
