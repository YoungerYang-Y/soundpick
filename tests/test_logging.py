"""Console logging must work with the actual default Uvicorn configuration."""

import asyncio
import json
import logging
import re
import subprocess
import sys
import textwrap

import pytest
from starlette.requests import ClientDisconnect
from starlette.responses import Response

from app.logging import JsonFormatter, get_request_id, request_context
from app.main import create_app
from app.middleware import RequestLogMiddleware


def test_default_uvicorn_factory_emits_structured_info_without_private_values():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import logging
                import logging.config
                from uvicorn.config import LOGGING_CONFIG
                from app.config import Settings
                from app.main import create_app

                logging.config.dictConfig(LOGGING_CONFIG)
                create_app(Settings(
                    app_password='test-password-123456', session_secret='test-key-' * 8,
                    redis_url='', _env_file=None,
                ))
                logging.getLogger('app.services.tasks').info('task.stage', extra={
                    'task_id': 'a' * 32, 'stage': 'converting',
                    'share_url': 'https://private.example/secret-share',
                    'password': 'private-password',
                })
                """
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1
    assert records[0]["event"] == "task.stage"
    assert records[0]["level"] == "INFO"
    assert records[0]["stage"] == "converting"
    assert records[0]["task_id"] == "a" * 32
    assert "private" not in result.stdout + result.stderr


def test_existing_root_handler_does_not_drop_fields_or_duplicate_application_logs():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
            import logging, sys
            from app.config import Settings
            from app.main import create_app
            logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='HOST %(message)s')
            settings = Settings(app_password='test-password-123456',
                session_secret='test-key-' * 8, redis_url='', _env_file=None)
            create_app(settings)
            create_app(settings)
            logging.getLogger('app.services.tasks').info('task.stage', extra={
                'task_id': 'a' * 32, 'stage': 'converting', 'request_id': 'b' * 32,
            })
        """),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.startswith('{"timestamp":')
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1
    assert records[0]["task_id"] == "a" * 32
    assert records[0]["request_id"] == "b" * 32


def test_request_event_covers_early_body_rejection_and_ignores_client_request_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        response = client.post(
            "/logout?token=private-query",
            content=b"private-body" * 1600,
            headers={"X-Request-ID": "private-client-id", "Cookie": "private-cookie"},
        )
    assert response.status_code == 413
    records = [record for record in caplog.records if record.msg == "request.completed"]
    assert len(records) == 1
    assert records[0].route == "/logout"
    assert records[0].status_code == 413
    assert records[0].error_category == "size_limit"
    assert records[0].elapsed_ms >= 0
    assert re.fullmatch(r"[a-f0-9]{32}", response.headers["X-Request-ID"])
    assert records[0].request_id == response.headers["X-Request-ID"]
    assert "private" not in caplog.text


def test_rate_rejection_logs_are_sampled_while_each_response_keeps_its_request_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        responses = [client.post("/login", data={}) for _ in range(14)]
    rejections = [response for response in responses if response.status_code == 429]
    assert len(rejections) > 1
    records = [
        record
        for record in caplog.records
        if record.msg == "request.completed" and record.status_code == 429
    ]
    assert len(records) == 1
    assert records[0].error_category == "rate_limit"
    assert records[0].retry_after_seconds > 0
    assert len({response.headers["X-Request-ID"] for response in responses}) == len(responses)


def test_formatter_drops_log_arguments_tracebacks_and_unknown_fields():
    try:
        raise ValueError("https://private-media.example/token?password=private-password")
    except ValueError:
        record = logging.LogRecord(
            "app.services.tasks",
            logging.ERROR,
            "private-file.py",
            12,
            "private-message %s",
            ("private-cookie",),
            sys.exc_info(),
        )
    record.share_url = "https://private-share.example/"
    record.title = "private-title"
    record.route = "/files/private-path"
    record.error_category = "private-error: contains input"
    record.elapsed_ms = float("inf")
    data = json.loads(JsonFormatter().format(record))
    assert data["event"] == "application.log"
    assert data["exception_type"] == "ValueError"
    assert not {"share_url", "title", "route", "error_category", "elapsed_ms"} & data.keys()
    assert "private" not in json.dumps(data)


def test_request_context_is_reset_on_error():
    assert get_request_id() is None
    try:
        with request_context("a" * 32):
            assert get_request_id() == "a" * 32
            raise RuntimeError
    except RuntimeError:
        pass
    assert get_request_id() is None


def test_unexpected_http_error_has_safe_500_and_correlated_event(settings, caplog):
    from fastapi.testclient import TestClient

    app = create_app(settings)

    @app.get("/unexpected-error")
    async def crash():
        raise RuntimeError("private-cookie-and-upstream-url")

    with caplog.at_level(logging.INFO, logger="app.middleware"):
        with TestClient(app, raise_server_exceptions=False) as browser:
            response = browser.get("/unexpected-error?token=private-query")
    assert response.status_code == 500
    records = [record for record in caplog.records if record.msg == "request.completed"]
    assert len(records) == 1
    assert records[0].status_code == 500
    assert records[0].request_id == response.headers.get("X-Request-ID")
    assert records[0].exception_type == "RuntimeError"
    assert "private" not in response.text + caplog.text


@pytest.mark.parametrize("failure", ["receive", "send"])
def test_client_disconnect_is_classified_without_becoming_a_server_failure(caplog, failure):
    async def endpoint(scope, receive, send):
        if failure == "receive":
            raise ClientDisconnect
        await Response("test")(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        raise BrokenPipeError("private-client-address")

    middleware = RequestLogMiddleware(endpoint)
    scope = {"type": "http", "method": "POST", "path": "/api/tasks/upload", "headers": []}
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        with pytest.raises((ClientDisconnect, BrokenPipeError)):
            asyncio.run(middleware(scope, receive, send))
    record = next(record for record in caplog.records if record.msg == "request.completed")
    assert record.status_code == 499
    assert record.error_category == "client_disconnect"
    assert record.levelno == logging.WARNING
    assert record.response_completed is False
    assert "private" not in caplog.text
    assert get_request_id() is None


def test_429_log_summarizes_suppressed_requests_after_next_window(monkeypatch, caplog):
    from app import middleware as middleware_module

    now = [0.0]
    monkeypatch.setattr(middleware_module.time, "monotonic", lambda: now[0])

    async def endpoint(scope, receive, send):
        await Response(status_code=429, headers={"Retry-After": "10"})(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = RequestLogMiddleware(endpoint)
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        for moment in (0.0, 5.0, 10.0, 30.0):
            now[0] = moment
            asyncio.run(
                middleware(
                    {"type": "http", "method": "POST", "path": "/login", "headers": []},
                    receive,
                    send,
                )
            )
    records = [record for record in caplog.records if record.msg == "request.completed"]
    assert len(records) == 2
    assert records[0].suppressed_count == 0
    assert records[1].suppressed_count == 2


def test_download_interruption_preserves_started_status_and_records_incomplete_response(caplog):
    async def endpoint(scope, receive, send):
        await Response("private-file-content")(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            raise ConnectionResetError("private-client-address")

    middleware = RequestLogMiddleware(endpoint)
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        with pytest.raises(ConnectionResetError):
            asyncio.run(
                middleware(
                    {"type": "http", "method": "GET", "path": "/files/" + "a" * 32, "headers": []},
                    receive,
                    send,
                )
            )
    record = next(record for record in caplog.records if record.msg == "request.completed")
    assert record.status_code == 200
    assert record.levelno == logging.WARNING
    assert record.error_category == "client_disconnect"
    assert record.response_completed is False
    assert "private" not in caplog.text


def test_request_cancellation_is_rethrown_and_resets_context(caplog):
    async def endpoint(scope, receive, send):
        raise asyncio.CancelledError

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    with caplog.at_level(logging.INFO, logger="app.middleware"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                RequestLogMiddleware(endpoint)(
                    {"type": "http", "method": "POST", "path": "/api/tasks/upload", "headers": []},
                    receive,
                    send,
                )
            )
    record = next(record for record in caplog.records if record.msg == "request.completed")
    assert record.status_code == 499
    assert record.error_category == "client_disconnect"
    assert get_request_id() is None
