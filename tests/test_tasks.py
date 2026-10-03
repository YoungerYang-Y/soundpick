import asyncio
import time

import pytest

from app.services.errors import MediaError


def test_tasks_are_bounded_private_and_cancelled_input_is_removed(settings):
    from app.services.media import MediaProcessor
    from app.services.network import SafeHTTP
    from app.services.tasks import TaskManager

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            # Reservations themselves are exercised without invoking absent external tools.
            first = manager.reserve("owner", "../名字.mp4", require_media=False)
            assert first.directory.is_dir()
            with pytest.raises(MediaError, match="正在处理"):
                manager.reserve("owner", "two", require_media=False)
            with pytest.raises(MediaError, match="过期"):
                manager.get(first.id, "another-owner")
            (first.directory / "input").write_bytes(b"partial")
            await manager.cancel(first)
            assert first.state == "cancelled"
            assert not first.directory.exists()
            again = manager.reserve("owner", "next", require_media=False)
            await manager.cancel(again)
            first.expires_at = time.time() - 1
            await manager.sweep()
            with pytest.raises(MediaError):
                manager.get(first.id, "owner")
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_restart_only_cleans_owned_task_directories(settings):
    from app.services.media import MediaProcessor
    from app.services.network import SafeHTTP
    from app.services.tasks import TaskManager

    root = settings.data_dir / ".soundpick-tasks"
    root.mkdir(parents=True)
    stale = root / ("a" * 32)
    stale.mkdir()
    (stale / "input").write_bytes(b"partial")
    unrelated = settings.data_dir / "family.txt"
    unrelated.write_text("keep")

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        assert not stale.exists()
        assert unrelated.read_text() == "keep"
        await manager.close()

    asyncio.run(exercise())


def test_storage_failures_are_public_and_cleanup_is_retried(settings, monkeypatch):
    import shutil
    from pathlib import Path

    from app.services.media import MediaProcessor
    from app.services.network import SafeHTTP
    from app.services.tasks import TaskManager

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        original = Path.mkdir

        def broken_mkdir(path, *args, **kwargs):
            if len(path.name) == 32:
                raise OSError("private filesystem failure")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", broken_mkdir)
        try:
            with pytest.raises(MediaError, match="存储") as exc:
                manager.reserve("owner", "sample", require_media=False)
            assert "private" not in str(exc.value)
            assert not manager.tasks
            monkeypatch.setattr(Path, "mkdir", original)
            task = manager.reserve("owner", "sample", require_media=False)
            original_open, original_remove = Path.open, shutil.rmtree

            def broken_open(path, *args, **kwargs):
                if path.name == "input":
                    raise OSError("private filesystem failure")
                return original_open(path, *args, **kwargs)

            def broken_remove(path, *args, **kwargs):
                raise OSError("private cleanup failure")

            async def stream():
                yield b"data"

            monkeypatch.setattr(Path, "open", broken_open)
            monkeypatch.setattr(shutil, "rmtree", broken_remove)
            with pytest.raises(MediaError) as error:
                await manager.receive(task, stream())
            assert error.value.status == 503
            assert task.state == "failed"
            assert task.runner is None
            assert task.cleanup_pending
            assert task.id in manager.tasks
            monkeypatch.setattr(Path, "open", original_open)
            monkeypatch.setattr(shutil, "rmtree", original_remove)
            await manager.sweep()
            assert not task.cleanup_pending
            assert not task.directory.exists()
        finally:
            monkeypatch.setattr(Path, "mkdir", original)
            await manager.close()

    asyncio.run(exercise())


def test_expiry_waits_for_delivery_then_removes_files(settings):
    from app.services.media import MediaProcessor
    from app.services.network import SafeHTTP
    from app.services.tasks import TaskManager

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            task = manager.reserve("owner", "sample", require_media=False)
            (task.directory / "audio.mp3").write_bytes(b"file")
            manager._finish(task, "ready", "done")
            manager.acquire_file(task)
            task.expires_at = time.time() - 1
            await manager.sweep()
            assert task.directory.exists()
            with pytest.raises(MediaError):
                manager.get(task.id, "owner")
            await manager.release_file(task)
            assert not task.directory.exists()
            assert task.id not in manager.tasks
        finally:
            await manager.close()

    asyncio.run(exercise())
