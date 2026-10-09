"""Run the diagnostic CLI with isolated configuration and Redis data."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "runtime-check:{isolated}"
PASSWORD = "diagnostic-private-password"


def run_check(tmp_path, real_tools, redis_url="", *, missing_client=False, media_missing=False):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "APP_PASSWORD": "runtime-test-password123456",
        "SESSION_SECRET": "runtime-test-secret-" * 4,
        "APP_ENV": "local",
        "COOKIE_SECURE": "false",
        "REDIS_URL": redis_url,
        "REDIS_KEY_PREFIX": PREFIX,
        "REDIS_TIMEOUT_SECONDS": "0.1",
        "FFMPEG_BIN": real_tools.ffmpeg_bin,
        "FFPROBE_BIN": real_tools.ffprobe_bin,
    }
    if media_missing:
        env["FFMPEG_BIN"] = env["FFPROBE_BIN"] = str(tmp_path / "missing-tool")
    command = [sys.executable, "-m", "scripts.check_runtime"]
    if missing_client:
        command = [
            sys.executable,
            "-c",
            "import sys, runpy; sys.modules['redis'] = None; "
            "runpy.run_module('scripts.check_runtime', run_name='__main__')",
        ]
    return subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20
    )


def test_unconfigured_redis_explicitly_reports_memory_mode(tmp_path, real_tools):
    result = run_check(tmp_path, real_tools, missing_client=True)
    assert result.returncode == 0
    assert "Redis 未配置" in result.stdout
    assert "内存" in result.stdout


def test_media_failure_does_not_skip_redis_diagnostic(tmp_path, real_tools):
    result = run_check(tmp_path, real_tools, media_missing=True)
    assert result.returncode == 1
    assert "FFmpeg/FFprobe 未就绪" in result.stdout
    assert "Redis 未配置" in result.stdout


def test_missing_client_is_reported_as_diagnostic_failure_without_leaking_url(tmp_path, real_tools):
    result = run_check(
        tmp_path,
        real_tools,
        f"redis://:{PASSWORD}@127.0.0.1:1/0",
        missing_client=True,
    )
    assert result.returncode == 1
    assert "client_unavailable" in result.stdout
    assert "./scripts/setup.sh" in result.stdout
    assert "内存" in result.stdout
    assert PASSWORD not in result.stdout + result.stderr
    assert "redis://" not in result.stdout + result.stderr


def test_configured_redis_probe_does_not_mutate_quota_or_leave_keys(
    tmp_path, real_tools, redis_server
):
    import redis

    client = redis.Redis.from_url(redis_server)
    try:
        key = f"{PREFIX}:quota"
        client.hset(key, mapping={"completed": "2025-01-01", "active": "pending"})
        before = {key: client.dump(key) for key in client.scan_iter()}
        client.execute_command(
            "ACL", "SETUSER", "probe", "on", f">{PASSWORD}", "-@all", "+eval", "+memory", f"~{key}"
        )
        url = urlsplit(redis_server)
        restricted_url = urlunsplit(url._replace(netloc=f"probe:{PASSWORD}@{url.netloc}"))
        result = run_check(tmp_path, real_tools, restricted_url)
        assert result.returncode == 0
        assert "Redis" in result.stdout
        assert "只读" in result.stdout
        assert "写入权限" in result.stdout
        assert {key: client.dump(key) for key in client.scan_iter()} == before
    finally:
        client.close()


def test_readonly_probe_reports_missing_memory_permission(tmp_path, real_tools, redis_server):
    import redis

    client = redis.Redis.from_url(redis_server)
    try:
        client.execute_command(
            "ACL", "SETUSER", "probe", "on", f">{PASSWORD}", "-@all", "+eval", f"~{PREFIX}:*"
        )
        url = urlsplit(redis_server)
        restricted_url = urlunsplit(url._replace(netloc=f"probe:{PASSWORD}@{url.netloc}"))
        result = run_check(tmp_path, real_tools, restricted_url)
        assert result.returncode == 1
        assert "permission" in result.stdout
        assert "ACL" in result.stdout
        assert PASSWORD not in result.stdout + result.stderr
        assert "redis://" not in result.stdout + result.stderr
        assert not list(client.scan_iter())
    finally:
        client.close()
