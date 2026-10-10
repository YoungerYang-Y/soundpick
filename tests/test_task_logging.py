"""Task lifecycle logs use stable events and safe correlation fields."""

import asyncio
import logging

from app.logging import request_context
from app.services.media import MediaProcessor
from app.services.network import SafeHTTP
from app.services.tasks import TaskManager


def test_task_lifecycle_logs_keep_initial_request_id_and_safe_fields(settings, caplog):
    manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())

    async def exercise():
        await manager.open()
        try:
            url = "https://v.douyin.com/private-share/"
            with request_context("a" * 32):
                task = manager.reserve(
                    "private-owner",
                    "private-title",
                    require_media=False,
                    input_kind="link",
                    share_url=url,
                )
            manager.start(task, url)
            await task.runner
            return task
        finally:
            await manager.close()

    with caplog.at_level(logging.INFO, logger="app.services.tasks"):
        task = asyncio.run(exercise())

    records = [record for record in caplog.records if record.name == "app.services.tasks"]
    lifecycle_events = [
        record.msg
        for record in records
        if record.msg in {"task.created", "task.stage", "task.failed"}
    ]
    assert lifecycle_events == ["task.created", "task.stage", "task.failed", "task.stage"]
    assert all(record.request_id == "a" * 32 for record in records)
    stage_record = next(
        record for record in records if record.msg == "task.stage" and record.stage == "resolving"
    )
    assert stage_record.task_id == task.id
    assert stage_record.input_kind == "link"
    assert stage_record.elapsed_ms >= 0
    failed_record = next(record for record in records if record.msg == "task.failed")
    assert failed_record.error_category == "KeyError"
    assert "private-owner" not in caplog.text
    assert "private-title" not in caplog.text
    assert "private message" not in caplog.text
    assert "private-error-category" not in caplog.text


def test_task_quota_release_failure_is_logged_once_with_fixed_event(settings, monkeypatch, caplog):
    manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())

    async def exercise():
        await manager.open()
        try:
            task = manager.reserve("owner", "sample", require_media=False)
            manager.protection.close()
            manager._release_quota(task.id)
            manager.protection.open()
            manager.protection.recover()
            return task
        finally:
            await manager.close()

    with caplog.at_level(logging.WARNING, logger="app.services.tasks"):
        task = asyncio.run(exercise())

    deferred = [record for record in caplog.records if record.msg == "task.quota_release_deferred"]
    assert len(deferred) == 1
    assert deferred[0].task_id == task.id
    assert deferred[0].error_category == "protection_unavailable"


def test_task_cleanup_failure_logs_only_first_defer(settings, monkeypatch, caplog):
    import app.services.tasks as tasks_module

    manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())

    async def exercise():
        await manager.open()
        try:
            task = manager.reserve("owner", "sample", require_media=False)

            def broken_rmtree(path):
                raise OSError("private path")

            monkeypatch.setattr(tasks_module.shutil, "rmtree", broken_rmtree)
            manager._remove(task)
            manager._remove(task)
            return task
        finally:
            await manager.close()

    with caplog.at_level(logging.WARNING, logger="app.services.tasks"):
        task = asyncio.run(exercise())

    deferred = [record for record in caplog.records if record.msg == "task.cleanup_deferred"]
    assert len(deferred) == 1
    assert deferred[0].task_id == task.id
    assert "private path" not in caplog.text
