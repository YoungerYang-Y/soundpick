"""HTTP task identity and file lifetime regression checks."""

import asyncio
import time

from fastapi.testclient import TestClient
from starlette.responses import FileResponse

from app.main import create_app
from tests.test_media import make_source, make_tools
from tests.test_media_api import login
from tests.test_mvp_flow import wait_terminal


def test_upload_reservation_can_be_replayed_cancelled_and_never_restarted(settings, tmp_path):
    configured = make_tools(settings, tmp_path)
    app = create_app(configured)
    task_id = "a" * 32
    with TestClient(app) as client:
        headers = {"X-CSRF-Token": login(client, configured)}
        reservation = {"task_id": task_id, "filename": "sample.mp4"}
        assert client.post("/api/tasks/reservations", json=reservation).status_code == 403
        first = client.post("/api/tasks/reservations", json=reservation, headers=headers)
        assert first.status_code == 202
        assert first.json()["id"] == task_id
        replay = client.post("/api/tasks/reservations", json=reservation, headers=headers)
        assert replay.json() == first.json()
        cancelled = client.post(f"/api/tasks/{task_id}/cancel", headers=headers)
        assert cancelled.json()["state"] == "cancelled"
        uploaded = client.post(f"/api/tasks/{task_id}/upload", content=b"video", headers=headers)
        assert uploaded.status_code == 409
        assert app.state.tasks.tasks[task_id].runner is None
        assert not app.state.tasks.tasks[task_id].directory.exists()
        client.cookies.clear()
        headers = {"X-CSRF-Token": login(client, configured)}
        assert (
            client.post("/api/tasks/reservations", json=reservation, headers=headers).status_code
            == 404
        )


def test_identified_upload_survives_lost_202_and_can_be_cancelled(settings, tmp_path):
    configured = make_tools(settings, tmp_path, convert_code="import time; time.sleep(30)")
    app = create_app(configured)
    source = make_source(tmp_path).read_bytes()
    task_id = "b" * 32
    with TestClient(app) as client:
        token = login(client, configured)
        headers = {"X-CSRF-Token": token}
        assert (
            client.post(
                "/api/tasks/reservations",
                json={"task_id": task_id, "filename": "sample.mp4"},
                headers=headers,
            ).status_code
            == 202
        )
        cookie = client.cookies.get("soundpick_session")

        async def exercise():
            sent = False
            response_failed = False
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": f"/api/tasks/{task_id}/upload",
                "raw_path": f"/api/tasks/{task_id}/upload".encode(),
                "root_path": "",
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234),
                "headers": [
                    (b"host", b"testserver"),
                    (b"cookie", ("soundpick_session=" + cookie).encode()),
                    (b"x-csrf-token", token.encode()),
                    (b"content-type", b"application/octet-stream"),
                    (b"content-length", str(len(source)).encode()),
                ],
            }

            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": source, "more_body": False}
                await asyncio.Event().wait()

            async def send(message):
                nonlocal response_failed
                if message["type"] == "http.response.start" and message["status"] == 202:
                    response_failed = True
                    raise OSError("response intentionally lost")

            try:
                await app(scope, receive, send)
            except OSError:
                pass
            assert response_failed
            assert (
                app.state.tasks.get(task_id, app.state.tasks.tasks[task_id].owner).runner
                is not None
            )

        client.portal.call(exercise)
        assert client.get(f"/api/tasks/{task_id}").status_code == 200
        assert (
            client.post(f"/api/tasks/{task_id}/cancel", headers=headers).json()["state"]
            == "cancelled"
        )
        assert not app.state.tasks.tasks[task_id].directory.exists()


def test_file_response_constructor_failure_returns_delivery_reference(
    settings, tmp_path, monkeypatch
):
    configured = make_tools(settings, tmp_path)
    app = create_app(configured)
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = {"X-CSRF-Token": login(client, configured)}
        response = client.post(
            "/api/tasks/upload", content=make_source(tmp_path).read_bytes(), headers=headers
        )
        value = wait_terminal(client, response.json()["id"])
        task = app.state.tasks.tasks[value["id"]]

        def broken_constructor(*args, **kwargs):
            raise OSError("isolated response construction failure")

        monkeypatch.setattr(FileResponse, "__init__", broken_constructor)
        assert client.get(value["download_url"]).status_code == 500
        assert task.deliveries == 0
        task.expires_at = time.time() - 1
        client.portal.call(app.state.tasks.sweep)
        assert not task.directory.exists()
        assert task.id not in app.state.tasks.tasks


def test_identified_upload_counts_actual_bytes_with_false_declared_length(settings, tmp_path):
    configured = make_tools(settings, tmp_path).model_copy(update={"max_media_bytes": 1024})
    app = create_app(configured)
    task_id = "c" * 32
    with TestClient(app) as client:
        headers = {"X-CSRF-Token": login(client, configured)}
        assert (
            client.post(
                "/api/tasks/reservations", json={"task_id": task_id}, headers=headers
            ).status_code
            == 202
        )
        response = client.post(
            f"/api/tasks/{task_id}/upload",
            content=b"x" * 1025,
            headers={**headers, "Content-Length": "1"},
        )
        assert response.status_code == 413
        assert app.state.tasks.tasks[task_id].state == "cancelled"
        assert not app.state.tasks.tasks[task_id].directory.exists()


def test_damaged_video_task_delivers_mp3_and_rejects_mp4(real_tools, tmp_path):
    from tests.test_video import make_video, probe

    source = make_video(tmp_path, real_tools)
    info = probe(real_tools, source, packets=True)
    indices = {s["index"] for s in info["streams"] if s["codec_type"] == "video"}
    damaged = bytearray(source.read_bytes())
    for packet in info["packets"]:
        if packet["stream_index"] in indices:
            start, size = int(packet["pos"]), int(packet["size"])
            damaged[start : start + size] = bytes(size)
    app = create_app(real_tools)
    with TestClient(app) as client:
        headers = {"X-CSRF-Token": login(client, real_tools)}
        response = client.post("/api/tasks/upload", content=bytes(damaged), headers=headers)
        task = wait_terminal(client, response.json()["id"])
        assert task["state"] == "ready"
        assert task["video_download_url"] is None
        assert task["video_message"]
        assert client.get(task["download_url"]).status_code == 200
        assert client.get(f"/files/{task['id']}?kind=video").status_code == 409
        directory = app.state.tasks.tasks[task["id"]].directory
        assert sorted(path.name for path in directory.iterdir()) == ["audio.mp3"]


def test_third_platform_catalog_drives_actual_task_route(real_tools, tmp_path):
    import httpx

    from app.resolver.base import MediaResolver, ResolvedVideo
    from app.resolver.registry import PLATFORMS, PlatformSpec
    from app.services.network import SafeHTTP
    from tests.test_video import make_video

    source = make_video(tmp_path, real_tools).read_bytes()

    class ThirdResolver(MediaResolver):
        platform = "third"
        display_name = "第三平台"
        available = True

        def __init__(self, network, endpoint, token):
            pass

        async def resolve(self, url):
            return ResolvedVideo("https://third.example/source.mp4", title="第三平台")

    spec = PlatformSpec("third", "第三平台", frozenset({"third.example"}), ThirdResolver)
    app = create_app(real_tools, platforms=(*PLATFORMS, spec))

    async def dns(host, port):
        return ["93.184.216.34"]

    app.state.tasks.network = SafeHTTP(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=source)),
        resolve=dns,
    )
    with TestClient(app) as client:
        headers = {"X-CSRF-Token": login(client, real_tools)}
        assert client.get("/api/capabilities").json()["platforms"][-1]["id"] == "third"
        body = {"share_text": "https://third.example/123", "task_id": "d" * 32}
        response = client.post("/api/tasks/link", json=body, headers=headers)
        assert response.status_code == 202
        task = wait_terminal(client, response.json()["id"])
        assert task["state"] == "ready"
        replay = client.post("/api/tasks/link", json=body, headers=headers)
        assert replay.json() == task
        assert client.get(task["video_download_url"]).content == source
