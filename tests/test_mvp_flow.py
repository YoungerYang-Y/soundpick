"""HTTP plumbing uses OS tools; real media tests are separately distinguished."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_media import make_source, make_tools, real_video
from tests.test_media_api import login


def wait_terminal(client, task_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        if response.json()["state"] in {"ready", "failed", "cancelled"}:
            return response.json()
        time.sleep(0.01)
    pytest.fail("task never reached terminal state")


def test_upload_to_private_range_download_and_other_session_isolation(settings, tmp_path):
    configured = make_tools(settings, tmp_path)
    app = create_app(configured)
    source = make_source(tmp_path)
    with TestClient(app) as client:
        token = login(client, configured)
        response = client.post(
            "/api/tasks/upload?filename=测试视频.mp4",
            content=source.read_bytes(),
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 202
        task = wait_terminal(client, response.json()["id"])
        assert task["state"] == "ready"
        directory = app.state.tasks.tasks[task["id"]].directory
        assert not (directory / "input").exists()
        assert (directory / "audio.mp3").stat().st_mode & 0o777 == 0o600
        full = client.get(task["download_url"])
        assert full.headers["content-type"] == "audio/mpeg"
        assert full.content == b"ID3test"
        assert "attachment" in full.headers["content-disposition"]
        assert "filename*=utf-8''" in full.headers["content-disposition"]
        assert ".mp3" in full.headers["content-disposition"]
        ranged = client.get(task["preview_url"], headers={"Range": "bytes=1-3"})
        assert ranged.status_code == 206
        assert ranged.content == full.content[1:4]
        assert client.head(task["preview_url"]).headers["content-length"] == "7"
        assert client.get(task["preview_url"], headers={"Range": "bytes=99-100"}).status_code == 416
        assert app.state.tasks.tasks[task["id"]].deliveries == 0
        client.cookies.clear()
        assert client.get(task["download_url"]).status_code == 401
        login(client, configured)
        assert client.get(task["download_url"]).status_code == 404
        assert client.get(f"/api/tasks/{task['id']}").status_code == 404
    assert not directory.exists()


def test_upload_actual_limit_and_disconnect_release_capacity(settings, tmp_path):
    configured = make_tools(settings, tmp_path).model_copy(update={"max_media_bytes": 1024})
    app = create_app(configured)
    with TestClient(app) as client:
        token = login(client, configured)
        for headers in ({}, {"Content-Length": "1"}):
            response = client.post(
                "/api/tasks/upload",
                content=b"x" * 1025,
                headers={"X-CSRF-Token": token, **headers},
            )
            assert response.status_code == 413
            assert all(
                t.state in {"cancelled", "failed", "ready"} for t in app.state.tasks.tasks.values()
            )
        response = client.post("/api/tasks/upload", content=b"", headers={"X-CSRF-Token": token})
        assert response.status_code == 400
        assert not list(app.state.tasks.root.glob("*/input"))


def test_failed_and_cancelled_tasks_are_bounded_and_cleaned(settings, tmp_path):
    configured = make_tools(settings, tmp_path).model_copy(update={"max_retained_tasks": 2})
    app = create_app(configured)
    with TestClient(app) as client:
        token = login(client, configured)
        for _ in range(2):
            response = client.post(
                "/api/tasks/upload", content=b"not a movie", headers={"X-CSRF-Token": token}
            )
            task = wait_terminal(client, response.json()["id"])
            assert task["state"] == "failed"
            assert not app.state.tasks.tasks[task["id"]].directory.exists()
        response = client.post(
            "/api/tasks/upload", content=b"not a movie", headers={"X-CSRF-Token": token}
        )
        assert response.status_code == 409
        assert len(app.state.tasks.tasks) == 2


def test_real_video_http_flow_preserves_duration_and_expires(real_tools, tmp_path):
    configured = real_tools.model_copy(update={"result_ttl_seconds": 1})
    source = real_video(tmp_path, configured)
    app = create_app(configured)
    with TestClient(app) as client:
        token = login(client, configured)
        response = client.post(
            "/api/tasks/upload?filename=首尾声音.mp4",
            content=source.read_bytes(),
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 202
        task = wait_terminal(client, response.json()["id"])
        assert task["state"] == "ready", task["message"]
        assert abs(task["duration_seconds"] - 4) <= 0.25
        assert client.get(task["download_url"]).content
        assert client.get(task["preview_url"], headers={"Range": "bytes=0-1023"}).status_code == 206
        directory = app.state.tasks.tasks[task["id"]].directory
        while time.time() < task["expires_at"]:
            time.sleep(0.01)
        assert client.get(task["download_url"]).status_code == 404
        client.portal.call(app.state.tasks.sweep)
        assert not directory.exists()


def test_active_conversion_can_be_cancelled_and_retried(settings, tmp_path):
    configured = make_tools(settings, tmp_path, convert_code="import time; time.sleep(30)")
    source = make_source(tmp_path)
    app = create_app(configured)
    with TestClient(app) as client:
        token = login(client, configured)
        response = client.post(
            "/api/tasks/upload", content=source.read_bytes(), headers={"X-CSRF-Token": token}
        )
        task_id = response.json()["id"]
        busy = client.post(
            "/api/tasks/upload", content=source.read_bytes(), headers={"X-CSRF-Token": token}
        )
        assert busy.status_code == 409
        response = client.post(f"/api/tasks/{task_id}/cancel", headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        assert response.json()["state"] == "cancelled"
        assert not app.state.tasks.tasks[task_id].directory.exists()
        assert (
            client.post(f"/api/tasks/{task_id}/cancel", headers={"X-CSRF-Token": token}).json()[
                "state"
            ]
            == "cancelled"
        )
        accepted = client.post(
            "/api/tasks/upload", content=source.read_bytes(), headers={"X-CSRF-Token": token}
        )
        assert accepted.status_code == 202


def test_stream_auth_reads_no_bytes_and_disconnect_removes_partial_input(settings, tmp_path):
    import httpx
    from starlette.requests import ClientDisconnect

    from tests.test_auth import csrf_from

    configured = make_tools(settings, tmp_path)
    app = create_app(configured)

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                reads = 0

                async def anonymous_body():
                    nonlocal reads
                    reads += 1
                    yield b"data"

                response = await client.post("/api/tasks/upload", content=anonymous_body())
                assert response.status_code == 401
                assert reads == 0
                page = await client.get("/login")
                await client.post(
                    "/login",
                    data={
                        "password": configured.app_password.get_secret_value(),
                        "csrf_token": csrf_from(page),
                    },
                )
                token = csrf_from(await client.get("/"))

                async def disconnected_body():
                    yield b"\x00\x00\x00\x18ftypisom"
                    raise ClientDisconnect()

                with pytest.raises(ClientDisconnect):
                    await client.post(
                        "/api/tasks/upload",
                        content=disconnected_body(),
                        headers={"X-CSRF-Token": token},
                    )
                assert not list(app.state.tasks.root.glob("*/input"))
                assert all(t.state == "cancelled" for t in app.state.tasks.tasks.values())

    asyncio.run(exercise())
