import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def pytest_addoption(parser):
    parser.addoption(
        "--require-redis",
        action="store_true",
        help="Fail instead of skipping Redis integration tests",
    )
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
        redis_url="",  # Tests must never inherit a deployed Redis URL from the environment.
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


@pytest.fixture
def redis_controller(tmp_path, request):
    """An isolated real Redis; never use application credentials or a shared database."""
    import os
    import shutil
    import socket
    import subprocess
    import time
    from types import SimpleNamespace

    binary = os.environ.get("SOUNDPICK_REDIS_SERVER_BIN") or shutil.which("redis-server")
    if not binary:
        if request.config.getoption("--require-redis"):
            pytest.fail("真实 Redis 未提供，严格防刷验收不能跳过")
        pytest.skip("真实 Redis 未提供")
    try:
        import redis
    except ImportError:
        if request.config.getoption("--require-redis"):
            pytest.fail("redis 客户端依赖未提供，严格验收不能跳过")
        pytest.skip("redis 客户端依赖未提供")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"redis://127.0.0.1:{port}/0"
    state = SimpleNamespace(url=url, process=None)

    def start():
        state.process = subprocess.Popen(
            [
                binary,
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                str(tmp_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        probe = redis.Redis.from_url(url, socket_connect_timeout=0.1, socket_timeout=0.1)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    if probe.ping():
                        return
                except redis.RedisError:
                    time.sleep(0.01)
            pytest.fail("临时 Redis 启动失败")
        finally:
            probe.close()

    def stop():
        if state.process is not None:
            state.process.terminate()
            state.process.wait(timeout=5)
            state.process = None

    state.start, state.stop = start, stop
    start()
    try:
        yield state
    finally:
        stop()


@pytest.fixture
def redis_server(redis_controller):
    return redis_controller.url


@pytest.fixture
def redis_settings(settings, redis_server):
    from pydantic import SecretStr

    return settings.model_copy(update={"redis_url": SecretStr(redis_server)})
