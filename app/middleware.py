"""Early request boundaries; only the raw upload route has a streaming budget."""

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time

from starlette.requests import ClientDisconnect
from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.logging import request_context
from app.services.errors import MediaError
from app.services.protection import ProtectionStore

logger = logging.getLogger(__name__)


class RequestLogMiddleware:
    """Correlate safe request metadata, including rejections outside the router."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Route templates are a fixed, bounded set; no client identities or paths
        # accumulate here. Keep logging useful when an endpoint is being flooded.
        self._rate_logs: dict[str, tuple[float, int]] = {}

    @staticmethod
    def route(scope: Scope) -> str:
        path = scope["path"]
        if path in {
            "/",
            "/healthz",
            "/login",
            "/logout",
            "/api/capabilities",
            "/api/tasks/link",
            "/api/tasks/upload",
            "/api/tasks/reservations",
        }:
            return path
        if path.startswith("/static/"):
            return "/static/{path}"
        for pattern, template in (
            (r"/api/tasks/[^/]+/upload", "/api/tasks/{task_id}/upload"),
            (r"/api/tasks/[^/]+/cancel", "/api/tasks/{task_id}/cancel"),
            (r"/api/tasks/[^/]+", "/api/tasks/{task_id}"),
            (r"/files/[^/]+", "/files/{task_id}"),
        ):
            if re.fullmatch(pattern, path):
                return template
        return "unmatched"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(16)
        scope["soundpick.request_id"] = request_id
        started = time.monotonic()
        status = 500
        response_started = False
        response_completed = False
        disconnected = False
        retry_after = None
        exception_type = None

        async def correlated_send(message: dict) -> None:
            nonlocal status, retry_after, response_started, response_completed, disconnected
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
                raw_wait = dict(headers).get(b"retry-after", b"")
                if raw_wait.isdigit():
                    retry_after = int(raw_wait)
            try:
                await send(message)
            except OSError:
                disconnected = True
                raise
            if message["type"] == "http.response.start":
                response_started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_completed = True

        async def tracked_receive() -> dict:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        with request_context(request_id):
            try:
                await self.app(scope, tracked_receive, correlated_send)
            except BaseException as exc:
                exception_type = type(exc).__name__
                if disconnected or isinstance(exc, (asyncio.CancelledError, ClientDisconnect)):
                    disconnected = True
                    if not response_started:
                        status = 499
                    scope["soundpick.error_category"] = "client_disconnect"
                else:
                    if not response_started:
                        status = 500
                    scope["soundpick.error_category"] = "internal_error"
                raise
            finally:
                route = self.route(scope)
                if disconnected and not response_completed:
                    scope["soundpick.error_category"] = "client_disconnect"
                    if not response_started:
                        status = 499
                finished = time.monotonic()
                suppressed = 0
                quiet = (
                    exception_type is None
                    and status < 400
                    and response_completed
                    and (
                        route in {"/healthz", "/static/{path}"}
                        or (scope["method"] == "GET" and route == "/api/tasks/{task_id}")
                    )
                )
                if status == 429 and exception_type is None:
                    previous = self._rate_logs.get(route)
                    if previous is not None and finished - previous[0] < 30:
                        self._rate_logs[route] = (previous[0], previous[1] + 1)
                        quiet = True
                    else:
                        suppressed = previous[1] if previous else 0
                        self._rate_logs[route] = (finished, 0)
                if not quiet:
                    level = (
                        logging.ERROR
                        if status >= 500 or (exception_type is not None and not disconnected)
                        else logging.WARNING
                        if status >= 400 or (disconnected and not response_completed)
                        else logging.INFO
                    )
                    logger.log(
                        level,
                        "request.completed",
                        extra={
                            "request_id": request_id,
                            "method": scope["method"],
                            "route": route,
                            "status_code": status,
                            "response_completed": response_completed,
                            "elapsed_ms": (finished - started) * 1000,
                            "suppressed_count": suppressed,
                            "error_category": scope.get("soundpick.error_category"),
                            "exception_type": exception_type,
                            "retry_after_seconds": retry_after,
                        },
                    )


class RateLimitMiddleware:
    """Admit requests before receiving bodies; never interpret client-supplied proxy headers."""

    def __init__(self, app: ASGIApp, protection: ProtectionStore, identity_secret: str) -> None:
        self.app = app
        self.protection = protection
        self.identity_secret = identity_secret.encode()

    @staticmethod
    def groups(method: str, path: str) -> tuple[str, ...]:
        if method == "POST" and path == "/login":
            return ("login",)
        if method == "POST" and path in {"/api/tasks/link", "/api/tasks/reservations"}:
            return ("extract",)
        if method == "POST" and path == "/api/tasks/upload":
            return ("extract", "upload")
        if method == "POST" and re.fullmatch(r"/api/tasks/[^/]+/upload", path):
            return ("upload",)
        poll_path = path == "/api/capabilities" or path.startswith("/api/tasks/")
        if (method == "GET" and poll_path) or (
            method == "POST" and re.fullmatch(r"/api/tasks/[^/]+/cancel", path)
        ):
            return ("poll",)
        if method in {"GET", "HEAD"} and path.startswith("/files/"):
            return ("download",)
        return ()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            groups = self.groups(scope["method"], scope["path"])
            if groups:
                address = scope.get("client")
                identity = hmac.new(
                    self.identity_secret,
                    (address[0] if address else "unknown").encode(),
                    hashlib.sha256,
                ).hexdigest()
                try:
                    self.protection.check_rate(groups, identity)
                except MediaError as exc:
                    scope["soundpick.error_category"] = exc.code
                    content = {"detail": exc.message, "code": exc.code}
                    headers = {}
                    if exc.retry_after is not None:
                        content["retry_after"] = exc.retry_after
                        headers["Retry-After"] = str(exc.retry_after)
                    if scope["path"] == "/login":
                        response = HTMLResponse(
                            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                            f"<title>请稍后重试</title><p>{exc.message}</p>"
                            '<a href="/login">返回登录</a></html>',
                            status_code=exc.status,
                            headers=headers,
                        )
                    else:
                        response = JSONResponse(content, status_code=exc.status, headers=headers)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        upload_max_bytes: int | None = None,
        body_timeout_seconds: int = 15,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes or max_bytes
        self.body_timeout_seconds = body_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        # Match the route shape; invalid task IDs must be rejected without receiving bytes.
        upload = scope["method"] == "POST" and (
            scope["path"] == "/api/tasks/upload"
            or re.fullmatch(r"/api/tasks/[^/]+/upload", scope["path"]) is not None
        )
        limit = self.upload_max_bytes if upload else self.max_bytes
        raw_length = headers.get(b"content-length")
        declared_error = None
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
                if declared_length < 0:
                    raise ValueError
            except ValueError:
                declared_error = MediaError("请求长度无效", "invalid_input", 400)
            else:
                if declared_length > limit:
                    declared_error = MediaError("请求内容过大", "size_limit", 413)
            # An existing upload reservation must pass ownership checks, then fail
            # through receive() so its slot and quota are released without reading bytes.
            if declared_error and not (upload and scope["path"] != "/api/tasks/upload"):
                scope["soundpick.error_category"] = declared_error.code
                await JSONResponse(
                    {"detail": declared_error.message, "code": declared_error.code},
                    status_code=declared_error.status,
                )(scope, receive, send)
                return

        if upload:
            received = 0

            async def bounded_receive() -> dict:
                nonlocal received
                if declared_error is not None:
                    raise declared_error
                message = await receive()
                received += len(message.get("body", b""))
                if received > limit:
                    raise MediaError("视频超过文件大小限制", "size_limit", 413)
                return message

            await self.app(scope, bounded_receive, send)
            return

        messages = []
        total = 0
        try:
            async with asyncio.timeout(self.body_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    total += len(message.get("body", b""))
                    if total > self.max_bytes:
                        scope["soundpick.error_category"] = "size_limit"
                        await JSONResponse({"detail": "请求内容过大"}, status_code=413)(
                            scope, receive, send
                        )
                        return
                    messages.append(message)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            scope["soundpick.error_category"] = "body_timeout"
            await JSONResponse({"detail": "请求接收超时", "code": "body_timeout"}, status_code=408)(
                scope, receive, send
            )
            return
        pending = iter(messages)

        async def replay() -> dict:
            return next(pending, None) or await receive()

        await self.app(scope, replay, send)
