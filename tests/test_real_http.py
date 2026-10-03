"""Actual localhost HTTP with installed media tools, separate from ASGI test transport."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.test_auth import csrf_from
from tests.test_media import real_video
from tests.test_mvp_flow import wait_terminal


def test_real_http_cancel_reaps_ffmpeg_and_allows_retry(real_tools, tmp_path):
    source = real_video(tmp_path, real_tools)
    executable = shutil.which(real_tools.ffmpeg_bin)
    marker = tmp_path / "ffmpeg.pid"
    wrapper = tmp_path / "paced-ffmpeg"
    wrapper.write_text(f"""#!{sys.executable}
import os, pathlib, sys
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
args = sys.argv[1:]
args.insert(args.index("-i"), "-re")
os.execv({executable!r}, [{executable!r}, *args])
""")
    wrapper.chmod(0o700)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    environment = {
        **os.environ,
        "APP_ENV": "local",
        "COOKIE_SECURE": "false",
        "APP_PASSWORD": real_tools.app_password.get_secret_value(),
        "SESSION_SECRET": real_tools.session_secret.get_secret_value(),
        "DATA_DIR": str(real_tools.data_dir),
        "FFMPEG_BIN": str(wrapper),
        "FFPROBE_BIN": real_tools.ffprobe_bin,
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3
        ) as client:
            deadline = time.monotonic() + 10
            while True:
                assert server.poll() is None, "localhost application exited during startup"
                try:
                    page = client.get("/login")
                    if page.status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert time.monotonic() < deadline, "localhost application did not start"
                time.sleep(0.02)
            response = client.post(
                "/login",
                data={
                    "password": real_tools.app_password.get_secret_value(),
                    "csrf_token": csrf_from(page),
                },
            )
            assert response.status_code == 303
            headers = {"X-CSRF-Token": csrf_from(client.get("/"))}
            response = client.post(
                "/api/tasks/upload", content=source.read_bytes(), headers=headers
            )
            assert response.status_code == 202
            task_id = response.json()["id"]
            directory = real_tools.data_dir / ".soundpick-tasks" / task_id
            deadline = time.monotonic() + 5
            while not marker.exists() or not (directory / "audio.mp3").exists():
                assert time.monotonic() < deadline, "real FFmpeg did not create partial output"
                time.sleep(0.01)
            pid = int(marker.read_text())
            assert Path(f"/proc/{pid}/exe").resolve() == Path(executable).resolve()
            assert client.get(f"/api/tasks/{task_id}").json()["state"] == "converting"
            assert (
                client.post(
                    "/api/tasks/upload", content=source.read_bytes(), headers=headers
                ).status_code
                == 409
            )
            response = client.post(f"/api/tasks/{task_id}/cancel", headers=headers)
            assert response.status_code == 200
            assert response.json()["state"] == "cancelled"
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            assert not directory.exists()
            assert client.get(f"/files/{task_id}").status_code == 409
            assert (
                client.post(f"/api/tasks/{task_id}/cancel", headers=headers).json()["state"]
                == "cancelled"
            )
            response = client.post(
                "/api/tasks/upload", content=source.read_bytes(), headers=headers
            )
            assert response.status_code == 202
            retried = wait_terminal(client, response.json()["id"])
            assert retried["state"] == "ready", retried["message"]
            assert abs(retried["duration_seconds"] - 4) <= 0.25
            assert client.get(retried["download_url"]).content
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    assert not list((real_tools.data_dir / ".soundpick-tasks").glob("*/input"))
    assert not list((real_tools.data_dir / ".soundpick-tasks").glob("*/audio.mp3"))
