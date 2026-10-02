"""Shared-password sessions and synchronizer tokens for same-origin forms."""

import hashlib
import hmac
import secrets
import time
from collections import OrderedDict

from starlette.requests import Request

from app.config import Settings


def session_fingerprint(settings: Settings) -> str:
    return hmac.new(
        settings.session_secret.get_secret_value().encode(),
        settings.app_password.get_secret_value().encode(),
        hashlib.sha256,
    ).hexdigest()


def is_authenticated(request: Request, settings: Settings) -> bool:
    marker = request.session.get("authenticated")
    return isinstance(marker, str) and secrets.compare_digest(marker, session_fingerprint(settings))


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return token


def valid_csrf(request: Request, submitted: object) -> bool:
    expected = request.session.get("csrf_token")
    return (
        isinstance(expected, str)
        and isinstance(submitted, str)
        and secrets.compare_digest(expected.encode(), submitted.encode())
    )


def password_matches(candidate: str, settings: Settings) -> bool:
    expected = settings.app_password.get_secret_value()
    return secrets.compare_digest(
        hashlib.sha256(candidate.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


class LoginLimiter:
    """Bounded, process-local guard; deployment must keep a single app process."""

    def __init__(self, limit: int = 10, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.attempts: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.attempts.pop(client, []) if now - t < self.window_seconds]
        allowed = len(recent) < self.limit
        if allowed:
            recent.append(now)
        self.attempts[client] = recent
        while len(self.attempts) > 256:
            self.attempts.popitem(last=False)
        return allowed
