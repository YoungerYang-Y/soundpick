"""ASGI application factory; importing modules does not require credentials."""

import logging
import secrets
from collections.abc import Sequence
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import TypeVar

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app.auth import (
    csrf_token,
    is_authenticated,
    password_matches,
    session_fingerprint,
    valid_csrf,
)
from app.config import Settings
from app.logging import setup_logging
from app.middleware import BodyLimitMiddleware, RateLimitMiddleware, RequestLogMiddleware
from app.resolver.registry import PlatformSpec, build_resolvers, extract_share_url, platform_of
from app.services.artifacts import ARTIFACTS, ArtifactKind, download_filename
from app.services.errors import MediaError
from app.services.media import MediaProcessor
from app.services.network import SafeHTTP
from app.services.protection import DAILY_SUCCESS_LIMIT
from app.services.tasks import Task, TaskManager

APP_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
InputModel = TypeVar("InputModel", bound=BaseModel)


class UploadReservation(BaseModel):
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    filename: str = Field(default="本地视频", max_length=200)


class LinkSubmission(BaseModel):
    share_text: str = Field(min_length=1, max_length=4096)
    task_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def create_app(
    settings: Settings | None = None, *, platforms: Sequence[PlatformSpec] | None = None
) -> FastAPI:
    setup_logging()
    settings = settings or Settings()
    setup_logging(settings.log_level)
    network = SafeHTTP()
    resolvers = build_resolvers(settings, network, specs=platforms)
    media = MediaProcessor(settings)
    tasks = TaskManager(
        settings, media, resolvers, network, select_platform=partial(platform_of, specs=platforms)
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            await media.check_readiness()
            await tasks.open()
        except Exception as exc:
            logger.error("application.startup_failed", extra={"exception_type": type(exc).__name__})
            raise
        logger.info(
            "application.started",
            extra={
                "app_env": settings.app_env,
                "backend": tasks.protection.mode,
                "media_available": media.available,
            },
        )
        try:
            yield
        finally:
            await tasks.close()
            logger.info("application.stopped")

    app = FastAPI(
        title=settings.app_name, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.tasks = tasks
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        upload_max_bytes=settings.max_media_bytes,
        body_timeout_seconds=settings.request_body_timeout_seconds,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie="soundpick_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.add_middleware(
        RateLimitMiddleware,
        protection=tasks.protection,
        identity_secret=settings.session_secret.get_secret_value(),
    )
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=APP_DIR / "templates")

    def authorized(request: Request, *, write: bool = False) -> str:
        if not is_authenticated(request, settings) or not request.session.get("owner"):
            raise MediaError("请先登录", "unauthorized", 401)
        if write and not valid_csrf(request, request.headers.get("x-csrf-token")):
            raise MediaError("页面已过期，请刷新后再试", "csrf", 403)
        return request.session["owner"]

    @app.exception_handler(MediaError)
    async def media_error(request: Request, exc: MediaError) -> Response:
        request.scope["soundpick.error_category"] = exc.code
        content = {"detail": exc.message, "code": exc.code}
        headers = {}
        if exc.retry_after is not None:
            content["retry_after"] = exc.retry_after
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(content, status_code=exc.status, headers=headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> Response:
        # Starlette's outer error handler runs after our request context unwinds.
        # Retain only the server-generated ID; the middleware already logged the type.
        headers = {}
        if request_id := request.scope.get("soundpick.request_id"):
            headers["X-Request-ID"] = request_id
        return JSONResponse(
            {"detail": "服务暂时无法完成请求，请稍后重试", "code": "internal_error"},
            status_code=500,
            headers=headers,
        )

    def render_login(request: Request, error: str | None = None, status: int = 200) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "app_name": settings.app_name,
                "csrf_token": csrf_token(request),
                "error": error,
            },
            status_code=status,
        )

    @app.middleware("http")
    async def response_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
        )
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def health() -> dict:
        return {"status": "ok", "service": "soundpick"}

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        if is_authenticated(request, settings):
            return RedirectResponse("/", status_code=303)
        return render_login(request)

    @app.post("/login")
    async def login(request: Request) -> Response:
        form = await request.form()
        if not valid_csrf(request, form.get("csrf_token")):
            request.scope["soundpick.error_category"] = "csrf"
            return render_login(request, "页面已过期，请重新输入口令。", 403)
        password = form.get("password")
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            request.scope["soundpick.error_category"] = "invalid_input"
            return render_login(request, "口令格式不正确。", 400)
        if not password_matches(password, settings):
            request.scope["soundpick.error_category"] = "unauthorized"
            return render_login(request, "口令不正确，请重试。", 401)
        request.session.clear()
        request.session["authenticated"] = session_fingerprint(settings)
        request.session["owner"] = secrets.token_hex(16)
        csrf_token(request)
        return RedirectResponse("/", status_code=303)

    @app.get("/")
    async def home(request: Request) -> Response:
        if not is_authenticated(request, settings):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"app_name": settings.app_name, "csrf_token": csrf_token(request)},
        )

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        form = await request.form()
        if not valid_csrf(request, form.get("csrf_token")):
            request.scope["soundpick.error_category"] = "csrf"
            return JSONResponse({"detail": "页面已过期，请刷新后再试"}, status_code=403)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/api/capabilities")
    async def capabilities(request: Request) -> Response:
        authorized(request)
        return JSONResponse(
            {
                "extraction_available": media.available,
                "media_available": media.available,
                "planned_inputs": ["share_text", "video_upload"],
                "platforms": [
                    {
                        "id": r.platform,
                        "name": r.display_name,
                        "available": r.available,
                        "configured": r.available,
                    }
                    for r in resolvers.values()
                ],
                "limits": {
                    "daily_success_limit": DAILY_SUCCESS_LIMIT,
                    "max_media_bytes": settings.max_media_bytes,
                    "max_duration_seconds": settings.max_duration_seconds,
                    "result_ttl_seconds": settings.result_ttl_seconds,
                    "upload_timeout_seconds": settings.upload_timeout_seconds,
                },
            }
        )

    async def json_input(request: Request, model: type[InputModel]) -> InputModel:
        try:
            return model.model_validate(await request.json())
        except ValueError:
            raise MediaError(
                "请求格式无效，请检查分享内容或任务标识", "invalid_input", 400
            ) from None

    async def receive_upload(task: Task, request: Request) -> dict:
        if task.input_kind != "upload":
            raise MediaError("此任务不接收本地视频，请重新创建任务", "input_conflict", 409)
        try:
            await tasks.receive(task, request.stream())
        except TimeoutError:
            raise MediaError("上传超时，请重新选择视频", "upload_timeout", 408) from None
        tasks.start(task)
        return tasks.view(task)

    @app.post("/api/tasks/reservations", status_code=202)
    async def reserve_upload(request: Request):
        owner = authorized(request, write=True)
        value = await json_input(request, UploadReservation)
        title = Path(value.filename.replace("\\", "/")).stem or "本地视频"
        task = tasks.reserve(owner, title, task_id=value.task_id, input_kind="upload")
        return tasks.view(task)

    @app.post("/api/tasks/{task_id}/upload", status_code=202)
    async def identified_upload(task_id: str, request: Request):
        task = tasks.get(task_id, authorized(request, write=True))
        return await receive_upload(task, request)

    @app.post("/api/tasks/upload", status_code=202)
    async def upload(request: Request, filename: str = Query(default="本地视频", max_length=200)):
        owner = authorized(request, write=True)
        title = Path(filename.replace("\\", "/")).stem or "本地视频"
        return await receive_upload(tasks.reserve(owner, title, input_kind="upload"), request)

    @app.post("/api/tasks/link", status_code=202)
    async def share_link(request: Request):
        owner = authorized(request, write=True)
        value = await json_input(request, LinkSubmission)
        _, url = extract_share_url(value.share_text, specs=platforms)
        task = tasks.reserve(
            owner, "音拾音频", task_id=value.task_id, input_kind="link", share_url=url
        )
        if task.state == "receiving" and task.runner is None:
            tasks.start(task, url)
        return tasks.view(task)

    @app.get("/api/tasks/{task_id}")
    async def task_status(task_id: str, request: Request):
        return tasks.view(tasks.get(task_id, authorized(request)))

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, request: Request):
        task = tasks.get(task_id, authorized(request, write=True))
        await tasks.cancel(task)
        return tasks.view(task)

    class ProtectedFileResponse(FileResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                await tasks.release_file(self.media_task)

    @app.api_route("/files/{task_id}", methods=["GET", "HEAD"])
    async def audio_file(
        task_id: str,
        request: Request,
        download: bool = False,
        kind: ArtifactKind = "audio",
    ):
        task = tasks.get(task_id, authorized(request))
        path = tasks.acquire_file(task, kind)
        try:
            response = ProtectedFileResponse(
                path,
                media_type=ARTIFACTS[kind].media_type,
                filename=download_filename(task.title, kind),
                content_disposition_type="attachment"
                if download or ARTIFACTS[kind].force_attachment
                else "inline",
            )
        except BaseException:
            await tasks.release_file(task)
            raise
        response.media_task = task
        return response

    app.add_middleware(RequestLogMiddleware)
    return app
