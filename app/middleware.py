"""Early request boundaries; only the raw upload route has a streaming budget."""

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.errors import MediaError


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int, upload_max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes or max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        upload = scope["method"] == "POST" and (
            scope["path"] == "/api/tasks/upload"
            or re.fullmatch(r"/api/tasks/[a-f0-9]{32}/upload", scope["path"]) is not None
        )
        limit = self.upload_max_bytes if upload else self.max_bytes
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
                if declared_length < 0:
                    raise ValueError
            except ValueError:
                await JSONResponse({"detail": "请求长度无效"}, status_code=400)(
                    scope, receive, send
                )
                return
            if declared_length > limit:
                await JSONResponse({"detail": "请求内容过大"}, status_code=413)(
                    scope, receive, send
                )
                return

        if upload:
            received = 0

            async def bounded_receive() -> dict:
                nonlocal received
                message = await receive()
                received += len(message.get("body", b""))
                if received > limit:
                    raise MediaError("视频超过文件大小限制", "size_limit", 413)
                return message

            await self.app(scope, bounded_receive, send)
            return

        messages = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                await JSONResponse({"detail": "请求内容过大"}, status_code=413)(
                    scope, receive, send
                )
                return
            messages.append(message)
            if not message.get("more_body", False):
                break
        pending = iter(messages)

        async def replay() -> dict:
            return next(pending, None) or await receive()

        await self.app(scope, replay, send)
