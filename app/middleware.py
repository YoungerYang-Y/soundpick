"""Small-body boundary for the bootstrap routes; uploads are not enabled yet."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
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
            if declared_length > self.max_bytes:
                await JSONResponse({"detail": "请求内容过大"}, status_code=413)(
                    scope, receive, send
                )
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
