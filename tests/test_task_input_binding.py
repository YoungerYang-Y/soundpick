"""Task IDs bind an input kind and a single share URL."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.resolver.base import MediaResolver
from app.services.errors import MediaError
from app.services.media import MediaProcessor
from app.services.network import SafeHTTP
from app.services.tasks import TaskManager
from tests.test_media import make_tools
from tests.test_media_api import login


def test_link_route_cannot_take_over_an_upload_reservation(settings, tmp_path):
    configured = make_tools(settings, tmp_path)
    app = create_app(configured)
    with TestClient(app) as client:
        headers = {"X-CSRF-Token": login(client, configured)}
        task_id = "a" * 32
        first = client.post("/api/tasks/reservations", json={"task_id": task_id}, headers=headers)
        assert first.status_code == 202
        replacement = client.post(
            "/api/tasks/link",
            json={"task_id": task_id, "share_text": "https://v.douyin.com/replacement/"},
            headers=headers,
        )
        assert replacement.status_code == 409
        assert client.get(f"/api/tasks/{task_id}").json() == first.json()
        assert app.state.tasks.tasks[task_id].runner is None


def test_link_replay_binds_url_and_rejects_upload_routes(settings, tmp_path):
    class WaitingResolver(MediaResolver):
        async def resolve(self, url):
            await asyncio.Event().wait()

    configured = make_tools(settings, tmp_path)
    app = create_app(configured)
    app.state.tasks.resolvers["douyin"] = WaitingResolver()
    with TestClient(app) as client:
        headers = {"X-CSRF-Token": login(client, configured)}
        task_id = "b" * 32
        body = {"task_id": task_id, "share_text": "https://v.douyin.com/original/"}
        first = client.post("/api/tasks/link", json=body, headers=headers)
        assert first.status_code == 202
        replay = client.post(
            "/api/tasks/link",
            json={**body, "share_text": "分享 https://V.DOUYIN.COM:443/original/#fragment"},
            headers=headers,
        )
        assert replay.json() == first.json()
        task = app.state.tasks.tasks[task_id]
        runner = task.runner
        for url in ("https://v.douyin.com/replacement/", "https://v.douyin.com/original/?id=2"):
            response = client.post(
                "/api/tasks/link", json={**body, "share_text": url}, headers=headers
            )
            assert response.status_code == 409
        assert (
            client.post(
                "/api/tasks/reservations", json={"task_id": task_id}, headers=headers
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/tasks/{task_id}/upload", content=b"unrelated", headers=headers
            ).status_code
            == 409
        )
        assert task.runner is runner
        assert task.state == "resolving"
        assert list(task.directory.iterdir()) == []
        assert "input_kind" not in first.json()
        assert "input_fingerprint" not in first.json()
        assert task.input_fingerprint != body["share_text"]
        client.cookies.clear()
        headers = {"X-CSRF-Token": login(client, configured)}
        assert (
            client.post(
                "/api/tasks/reservations", json={"task_id": task_id}, headers=headers
            ).status_code
            == 404
        )


@pytest.mark.parametrize("first_kind,second_kind", [("upload", "link"), ("link", "upload")])
def test_reservation_rejects_cross_kind_replay_without_changing_task(
    settings, first_kind, second_kind
):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            task = manager.reserve(
                "owner",
                "original",
                require_media=False,
                task_id="a" * 32,
                input_kind=first_kind,
                share_url="https://v.douyin.com/original/" if first_kind == "link" else None,
            )
            original = manager.view(task)
            with pytest.raises(MediaError) as exc:
                manager.reserve(
                    "owner",
                    "replacement",
                    require_media=False,
                    task_id=task.id,
                    input_kind=second_kind,
                    share_url="https://v.douyin.com/other/" if second_kind == "link" else None,
                )
            assert exc.value.status == 409
            assert manager.view(task) == original
            assert task.runner is None
            assert list(task.directory.iterdir()) == []
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_link_reservation_rejects_stream_without_consuming_it(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            task = manager.reserve(
                "owner",
                "original",
                require_media=False,
                input_kind="link",
                share_url="https://v.douyin.com/original/",
            )
            original = manager.view(task)
            consumed = []

            async def stream():
                consumed.append(True)
                yield b"unrelated video"

            with pytest.raises(MediaError) as exc:
                await manager.receive(task, stream())
            assert exc.value.status == 409
            assert not consumed
            assert manager.view(task) == original
            assert task.runner is None
            assert list(task.directory.iterdir()) == []
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_conflicting_replay_cannot_interrupt_an_upload_in_progress(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        entered, release = asyncio.Event(), asyncio.Event()
        task = manager.reserve("owner", "original", require_media=False)

        async def stream():
            yield b"first-"
            entered.set()
            await release.wait()
            yield b"last"

        receiver = asyncio.create_task(manager.receive(task, stream()))
        try:
            await entered.wait()
            with pytest.raises(MediaError) as exc:
                manager.reserve(
                    "owner",
                    "replacement",
                    task_id=task.id,
                    require_media=False,
                    input_kind="link",
                    share_url="https://v.douyin.com/replacement/",
                )
            assert exc.value.status == 409
            assert task.runner is receiver
            assert not receiver.done()
            release.set()
            await receiver
            assert (task.directory / "input").read_bytes() == b"first-last"
            assert task.title == "original"
        finally:
            release.set()
            await manager.close()

    asyncio.run(exercise())
