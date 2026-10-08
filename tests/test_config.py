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


def test_media_limits_are_bounded_and_provider_keys_remain_secret(settings):
    assert settings.max_media_bytes == 200 * 1024 * 1024
    assert settings.max_duration_seconds == 1800
    assert settings.result_ttl_seconds == 3600
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "max_media_bytes": 0}, _env_file=None)
    configured = Settings(
        **{**settings.model_dump(), "parser_api_token": "private-parser-key"}, _env_file=None
    )
    assert "private-parser-key" not in repr(configured)


@pytest.mark.parametrize(
    "url",
    [
        "redis://:private-redis-secret@localhost:6379/2",
        "rediss://user:private-redis-secret@localhost:6379?db=2",
    ],
)
def test_redis_credentials_stay_secret_and_safe_urls_are_accepted(settings, url):
    configured = Settings(**{**settings.model_dump(), "redis_url": url}, _env_file=None)
    assert configured.redis_url.get_secret_value() == url
    assert "private-redis-secret" not in repr(configured)
    assert configured.redis_timeout_seconds == 0.1
    assert configured.redis_retry_seconds == 30


@pytest.mark.parametrize(
    "url",
    [
        "https://:private-redis-secret@localhost/0",
        "redis://:private-redis-secret@localhost/0?socket_timeout=300",
        "rediss://:private-redis-secret@localhost/0?ssl_cert_reqs=none",
        "redis://:private-redis-secret@localhost:invalid/0",
        "redis://:private-redis-secret@localhost/not-a-db",
        "redis://:private-redis-secret@localhost/0?db=0&db=1",
        "redis://:private-redis-secret@localhost/0#fragment",
    ],
)
def test_redis_url_cannot_override_timeouts_or_tls(settings, url):
    with pytest.raises(ValidationError) as exc:
        Settings(**{**settings.model_dump(), "redis_url": url}, _env_file=None)
    assert "private-redis-secret" not in str(exc.value)
