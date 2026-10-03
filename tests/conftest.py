import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def pytest_addoption(parser):
    parser.addoption(
        "--require-media-tools",
        action="store_true",
        help="Fail instead of skipping real media tests when tools are missing",
    )


@pytest.fixture
def settings(tmp_path):
    return Settings(
        app_password="家庭共享测试口令123456",
        session_secret="test-key-" * 8,
        app_env="local",
        cookie_secure=False,
        data_dir=tmp_path / "data",
        _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), follow_redirects=False) as client:
        yield client


@pytest.fixture
def real_tools(settings, request):
    import os
    import shutil

    configured = settings.model_copy(
        update={
            "ffmpeg_bin": os.environ.get("FFMPEG_BIN", settings.ffmpeg_bin),
            "ffprobe_bin": os.environ.get("FFPROBE_BIN", settings.ffprobe_bin),
        }
    )
    if not shutil.which(configured.ffmpeg_bin) or not shutil.which(configured.ffprobe_bin):
        if request.config.getoption("--require-media-tools"):
            pytest.fail("真实FFmpeg/FFprobe尚未提供，严格验收不能跳过媒体测试")
        pytest.skip("真实FFmpeg/FFprobe尚未提供，不能证明转码验收")
    return configured
