import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings():
    return Settings(
        app_password="家庭共享测试口令123456",
        session_secret="test-key-" * 8,
        app_env="local",
        cookie_secure=False,
        _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), follow_redirects=False) as client:
        yield client
