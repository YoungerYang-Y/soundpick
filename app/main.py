"""ASGI application factory; importing modules does not require credentials."""

from pathlib import Path

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app.auth import (
    LoginLimiter,
    csrf_token,
    is_authenticated,
    password_matches,
    session_fingerprint,
    valid_csrf,
)
from app.config import Settings
from app.middleware import BodyLimitMiddleware
from app.resolver.douyin import DouyinResolver
from app.resolver.huoshan import HuoshanResolver

APP_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie="soundpick_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=APP_DIR / "templates")
    limiter = LoginLimiter()
    resolvers = (DouyinResolver(), HuoshanResolver())

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
            return render_login(request, "页面已过期，请重新输入口令。", 403)
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            response = render_login(request, "尝试次数过多，请一分钟后重试。", 429)
            response.headers["Retry-After"] = "60"
            return response
        password = form.get("password")
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            return render_login(request, "口令格式不正确。", 400)
        if not password_matches(password, settings):
            return render_login(request, "口令不正确，请重试。", 401)
        request.session.clear()
        request.session["authenticated"] = session_fingerprint(settings)
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
            return JSONResponse({"detail": "页面已过期，请刷新后再试"}, status_code=403)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/api/capabilities")
    async def capabilities(request: Request) -> Response:
        if not is_authenticated(request, settings):
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        return JSONResponse(
            {
                "extraction_available": False,
                "planned_inputs": ["share_text", "video_upload"],
                "platforms": [
                    {"id": r.platform, "name": r.display_name, "available": r.available}
                    for r in resolvers
                ],
            }
        )

    return app
