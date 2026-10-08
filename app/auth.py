"""Shared-password sessions and synchronizer tokens for same-origin forms."""

import hashlib
import hmac
import secrets

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
