"""Observable production quotas, request admission and ownership boundaries."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.services.errors import MediaError
from app.services.media import MediaProcessor
from app.services.network import SafeHTTP
from app.services.tasks import TaskManager
from tests.test_auth import csrf_from
from tests.test_media import make_tools


def login(browser, settings):
    page = browser.get("/login")
    response = browser.post(
        "/login",
        data={
            "password": settings.app_password.get_secret_value(),
            "csrf_token": csrf_from(page),
        },
    )
    assert response.status_code == 303
    return {"X-CSRF-Token": csrf_from(browser.get("/"))}


def test_daily_successes_survive_task_cleanup_restart_and_new_browser(redis_settings, tmp_path):
    settings = redis_settings
    settings = make_tools(settings, tmp_path)

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            for i in range(10):
                task = manager.reserve("owner", str(i), require_media=False)
                manager._finish(task, "ready", "done")
                manager._finish(task, "ready", "done")  # Idempotent accounting.
            with pytest.raises(MediaError) as error:
                manager.reserve("another-browser", "eleventh", require_media=False)
            assert error.value.status == 429
            assert error.value.code == "daily_quota"
            assert error.value.retry_after > 0
        finally:
            await manager.close()

    asyncio.run(exercise())
    app = create_app(settings)
    with TestClient(app, follow_redirects=False) as browser:
        headers = login(browser, settings)
        response = browser.post(
            "/api/tasks/reservations", json={"task_id": "f" * 32}, headers=headers
        )
        assert response.status_code == 429
        assert response.json()["code"] == "daily_quota"
        assert int(response.headers["Retry-After"]) == response.json()["retry_after"]


def test_failed_cancelled_and_expired_reservations_release_quota(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            for _ in range(11):
                task = manager.reserve("owner", "fail", require_media=False)
                manager._finish(task, "failed", "fail")
                await manager.cancel(task)
            task = manager.reserve("owner", "cancel", require_media=False)
            await manager.cancel(task)
            await manager.cancel(task)
            task = manager.reserve("owner", "expired", require_media=False)
            task.receive_deadline = 0
            await manager.sweep()
            assert task.state == "failed"
            for _ in range(10):
                task = manager.reserve("owner", "success", require_media=False)
                manager._finish(task, "ready", "done")
            with pytest.raises(MediaError) as error:
                manager.reserve("owner", "eleventh", require_media=False)
            assert error.value.code == "daily_quota"
        finally:
            await manager.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("method", "path", "limit"),
    [
        ("POST", "/api/tasks/link", 6),
        ("POST", "/api/tasks/reservations", 6),
        ("POST", "/api/tasks/" + "a" * 32 + "/upload", 6),
        ("POST", "/api/tasks/upload", 6),
        ("GET", "/api/tasks/" + "a" * 32, 120),
        ("GET", "/api/capabilities", 120),
        ("POST", "/api/tasks/" + "a" * 32 + "/cancel", 120),
        ("GET", "/files/" + "a" * 32, 30),
        ("HEAD", "/files/" + "a" * 32 + "?kind=video", 30),
    ],
)
def test_each_endpoint_limits_even_unauthorized_attempts(client, method, path, limit):
    for _ in range(limit):
        response = client.request(method, path)
        assert response.status_code == 401
    response = client.request(method, path, headers={"X-Forwarded-For": "198.51.100.20"})
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    if method != "HEAD":
        assert response.json()["code"] == "rate_limit"
        assert response.json()["retry_after"] == int(response.headers["Retry-After"])


def test_login_budget_includes_invalid_csrf_and_survives_restart(redis_settings):
    settings = redis_settings
    with TestClient(create_app(settings)) as browser:
        for _ in range(10):
            assert browser.post("/login", data={"password": "wrong"}).status_code == 403
    with TestClient(create_app(settings)) as browser:
        response = browser.post(
            "/login", data={"password": "wrong"}, headers={"X-Forwarded-For": "198.51.100.21"}
        )
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
        assert "后重试" in response.text


def test_rate_limit_rejects_upload_without_consuming_body(settings):
    app = create_app(settings)
    with TestClient(app) as browser:
        for _ in range(6):
            assert browser.post("/api/tasks/upload").status_code == 401

        async def exercise():
            reads = 0
            messages = []

            async def receive():
                nonlocal reads
                reads += 1
                raise AssertionError("rate-limited upload body must not be read")

            async def send(message):
                messages.append(message)

            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/api/tasks/upload",
                    "raw_path": b"/api/tasks/upload",
                    "query_string": b"",
                    "headers": [],
                    "client": ("testclient", 1234),
                    "server": ("testserver", 80),
                },
                receive,
                send,
            )
            assert reads == 0
            assert messages[0]["status"] == 429

        asyncio.run(exercise())


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Length": "1025"},
        {"Content-Length": "1"},
        {},
    ],
)
def test_oversized_reserved_upload_releases_slot_and_quota(settings, tmp_path, headers):
    configured = make_tools(settings.model_copy(update={"max_media_bytes": 1024}), tmp_path)
    app = create_app(configured)
    with TestClient(app, follow_redirects=False) as browser:
        csrf = login(browser, configured)
        task_id = "b" * 32
        assert (
            browser.post(
                "/api/tasks/reservations", json={"task_id": task_id}, headers=csrf
            ).status_code
            == 202
        )
        response = browser.post(
            f"/api/tasks/{task_id}/upload", content=b"x" * 1025, headers={**csrf, **headers}
        )
        assert response.status_code == 413
        task = app.state.tasks.tasks[task_id]
        assert task.state == "cancelled"
        assert not task.directory.exists()
        # A remaining pending charge would reject this ten-slot sequence.
        for index in range(10):
            replacement = app.state.tasks.reserve("another-owner", str(index), require_media=False)
            app.state.tasks._finish(replacement, "ready", "done")


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", ""),
        ("POST", "/cancel"),
        ("POST", "/upload"),
        ("GET", "audio"),
        ("HEAD", "audio"),
        ("GET", "video"),
        ("HEAD", "video"),
    ],
)
def test_other_browser_cannot_access_or_stop_owned_task(settings, tmp_path, method, suffix):
    app = create_app(make_tools(settings, tmp_path))
    with TestClient(app, follow_redirects=False) as browser:
        first_csrf = login(browser, settings)
        task_id = "c" * 32
        assert (
            browser.post(
                "/api/tasks/reservations", json={"task_id": task_id}, headers=first_csrf
            ).status_code
            == 202
        )
        task = app.state.tasks.tasks[task_id]
        browser.cookies.clear()
        second_csrf = login(browser, settings)
        path = (
            f"/files/{task_id}?kind={suffix}"
            if suffix in {"audio", "video"}
            else f"/api/tasks/{task_id}{suffix}"
        )
        response = browser.request(
            method,
            path,
            content=b"oversized" if suffix == "/upload" else b"",
            headers={**second_csrf, "Content-Length": "999999999", "Range": "bytes=0-2"}
            if suffix == "/upload"
            else {**second_csrf, "Range": "bytes=0-2"},
        )
        assert response.status_code == 404
        assert task.state == "receiving"
        assert task.directory.exists()


def test_reused_success_id_cannot_start_new_task_after_restart(redis_settings):
    settings = redis_settings

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        task = manager.reserve("owner", "first", require_media=False, task_id="d" * 32)
        manager._finish(task, "ready", "done")
        await manager.close()
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            with pytest.raises(MediaError) as error:
                manager.reserve("new-owner", "again", require_media=False, task_id=task.id)
            assert error.value.code == "input_conflict"
            assert not manager.tasks
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_direct_upload_shares_extract_hour_budget_across_new_browsers(settings):
    now = [1_750_000_000.0]
    app = create_app(settings)
    app.state.tasks.protection.clock = lambda: now[0]
    with TestClient(app) as browser:
        for _batch in range(5):
            for _ in range(6):
                assert browser.post("/api/tasks/upload").status_code == 401
            now[0] += 60
            browser.cookies.clear()
        response = browser.post("/api/tasks/upload")
        assert response.status_code == 429
        assert response.json()["retry_after"] == 3300


def test_small_body_receive_timeout_is_bounded_and_does_not_enter_handler():
    from app.middleware import BodyLimitMiddleware

    async def exercise():
        entered = False
        messages = []

        async def handler(scope, receive, send):
            nonlocal entered
            entered = True

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        middleware = BodyLimitMiddleware(handler, max_bytes=1024, body_timeout_seconds=0.01)
        await middleware(
            {"type": "http", "method": "POST", "path": "/login", "headers": []}, receive, send
        )
        assert not entered
        assert messages[0]["status"] == 408

    asyncio.run(exercise())


def test_startup_discards_orphan_uploads_and_releases_all_pending_quota(redis_settings):
    from app.services.protection import ProtectionStore

    settings = redis_settings
    settings.data_dir.mkdir()
    store = ProtectionStore(
        redis_url=settings.redis_url.get_secret_value(), key_prefix=settings.redis_key_prefix
    )
    store.open()
    for index in range(10):
        task_id = f"{index:032x}"
        store.reserve(task_id)
        stale = settings.data_dir / ".soundpick-tasks" / task_id
        stale.mkdir(parents=True)
        (stale / "input").write_bytes(b"partial")
    store.close()

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            assert not any(path.is_dir() for path in manager.root.iterdir())
            for index in range(10):
                task = manager.reserve("new-owner", str(index), require_media=False)
                manager._finish(task, "ready", "done")
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_redis_unavailable_falls_back_to_memory_for_login_limits_and_download(settings, tmp_path):
    configured = make_tools(
        settings.model_copy(update={"redis_url": SecretStr("redis://127.0.0.1:1")}), tmp_path
    )
    app = create_app(configured)
    with TestClient(app, follow_redirects=False) as browser:
        login_page = browser.get("/login")
        csrf = csrf_from(login_page)
        response = browser.post(
            "/login",
            data={
                "password": configured.app_password.get_secret_value(),
                "csrf_token": csrf,
            },
        )
        assert response.status_code == 303
        session_csrf = csrf_from(browser.get("/"))

        task_id = "e" * 32
        reservation = browser.post(
            "/api/tasks/reservations",
            json={"task_id": task_id},
            headers={"X-CSRF-Token": session_csrf},
        )
        assert reservation.status_code == 202
        task = app.state.tasks.tasks[task_id]
        (task.directory / "audio.mp3").write_bytes(b"ID3redis-down")
        app.state.tasks._finish(task, "ready", "done")

        failed_login = {
            "password": "wrong",
            "csrf_token": session_csrf,
        }
        # The successful login and nine failures consume the ten-request budget.
        for _ in range(9):
            assert browser.post("/login", data=failed_login).status_code == 401
        limited = browser.post("/login", data=failed_login)
        assert limited.status_code == 429
        assert "后重试" in limited.text

        artifact = browser.get(f"/files/{task_id}")
        assert artifact.status_code == 200
        assert artifact.content == b"ID3redis-down"


def test_directory_failure_releases_quota_immediately(settings, monkeypatch):
    from pathlib import Path

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        original_mkdir = Path.mkdir

        def unavailable_directory(path, *args, **kwargs):
            if len(path.name) == 32:
                raise OSError("private storage failure")
            return original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", unavailable_directory)
        try:
            with pytest.raises(MediaError) as error:
                manager.reserve("owner", "sample", require_media=False)
            assert error.value.status == 503
            assert not manager.tasks
            monkeypatch.setattr(Path, "mkdir", original_mkdir)
            for index in range(10):
                task = manager.reserve("owner", str(index), require_media=False)
                manager._finish(task, "ready", "done")
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_shutdown_releases_process_lock_even_if_cleaner_already_failed(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        manager.cleaner.cancel()
        try:
            await manager.cleaner
        except asyncio.CancelledError:
            pass

        async def failed_cleaner():
            raise MediaError("protection unavailable", "protection_unavailable", 503)

        manager.cleaner = asyncio.create_task(failed_cleaner())
        await asyncio.sleep(0)
        await manager.close()
        replacement = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await replacement.open()
        await replacement.close()

    asyncio.run(exercise())


def test_private_mode_uses_memory_without_creating_a_database(settings):
    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            task = manager.reserve("owner", "sample", require_media=False)
            manager._finish(task, "ready", "done")
            assert not list(settings.data_dir.glob("*.sqlite3"))
            assert not (settings.data_dir / ".soundpick-protection.sqlite3").exists()
        finally:
            await manager.close()

    asyncio.run(exercise())
