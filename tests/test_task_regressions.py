"""Regressions for cancellation, reservations, and safe diagnostics."""

import asyncio
import logging
import time

import pytest

from app.services.errors import MediaError
from app.services.media import MediaProcessor
from app.services.network import SafeHTTP
from app.services.tasks import TaskManager


def test_repeated_cancel_shares_cleanup_and_survives_cancelled_waiter(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        entered = asyncio.Event()
        cleanup = asyncio.Event()
        finish = asyncio.Event()
        repeated = []
        task = manager.reserve("owner", "sample", require_media=False)

        async def external_work():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup.set()
                try:
                    await finish.wait()
                except asyncio.CancelledError:
                    repeated.append(True)
                    raise

        task.runner = asyncio.create_task(external_work())
        await entered.wait()
        first = asyncio.create_task(manager.cancel(task))
        await cleanup.wait()
        second = asyncio.create_task(manager.cancel(task))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        finish.set()
        await second
        try:
            assert not repeated, "a second caller cancelled resource cleanup again"
            assert task.state == "cancelled"
            assert not task.directory.exists()
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_reservation_replay_is_private_idempotent_and_expires(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            task = manager.reserve("owner", "sample", task_id="a" * 32, require_media=False)
            assert manager.reserve("owner", "other", task_id=task.id, require_media=False) is task
            with pytest.raises(MediaError) as exc:
                manager.reserve("other", "sample", task_id=task.id, require_media=False)
            assert exc.value.status == 404
            task.receive_deadline = time.time() - 1
            await manager.sweep()
            assert task.state == "failed"
            assert not task.directory.exists()
            next_task = manager.reserve("owner", "next", require_media=False)
            await manager.cancel(next_task)
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_unexpected_failure_logs_stage_and_type_without_private_values(settings, caplog):
    async def exercise():
        async def dns(host, port):
            raise RuntimeError("secret-cookie-and-private-url")

        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP(resolve=dns))
        await manager.open()
        try:
            url = "https://v.douyin.com/private-share/"
            task = manager.reserve(
                "owner", "private-title", require_media=False, input_kind="link", share_url=url
            )
            manager.start(task, url)
            # Missing adapter is an internal programming failure, not public platform content.
            await task.runner
            assert task.state == "failed"
            assert "resolving" in caplog.text
            assert "KeyError" in caplog.text
            assert "private-share" not in caplog.text
            assert "private-title" not in caplog.text
            assert "secret-cookie" not in caplog.text
        finally:
            await manager.close()

    with caplog.at_level(logging.WARNING, logger="app.services.tasks"):
        asyncio.run(exercise())


def test_expired_reservation_cannot_receive_before_periodic_cleanup(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        task = manager.reserve("owner", "sample", require_media=False)
        task.receive_deadline = time.time() - 1
        consumed = []

        async def stream():
            consumed.append(True)
            yield b"late upload"

        try:
            with pytest.raises(MediaError) as exc:
                await manager.receive(task, stream())
            assert exc.value.status == 409
            assert not consumed
            assert not task.directory.exists()
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_close_keeps_live_deliveries_until_last_release(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        task = manager.reserve("owner", "sample", require_media=False)
        (task.directory / "audio.mp3").write_bytes(b"audio")
        (task.directory / "video.mp4").write_bytes(b"video")
        task.video_ready = True
        manager._finish(task, "ready", "done")
        manager.acquire_file(task)
        manager.acquire_file(task, "video")
        await manager.close()
        assert task.directory.exists()
        with pytest.raises(MediaError):
            manager.get(task.id, "owner")
        await manager.release_file(task)
        assert task.directory.exists()
        await manager.release_file(task)
        assert not task.directory.exists()
        assert task.id not in manager.tasks

    asyncio.run(exercise())
