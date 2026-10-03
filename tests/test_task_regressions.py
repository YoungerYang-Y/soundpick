"""Regressions for cancellation, reservations, and safe diagnostics."""

import asyncio

import pytest

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
