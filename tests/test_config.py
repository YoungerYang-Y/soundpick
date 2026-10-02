import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_secure_cookie():
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(
            app_password="test-password-for-family",
            session_secret="s" * 48,
            app_env="production",
            cookie_secure=False,
            _env_file=None,
        )


def test_credentials_are_required_and_validation_errors_hide_values():
    with pytest.raises(ValidationError) as exc:
        Settings(app_password="short-secret", session_secret="tiny", _env_file=None)
    assert "short-secret" not in str(exc.value)
    assert "tiny" not in str(exc.value)
