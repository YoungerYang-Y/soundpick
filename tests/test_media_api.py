import asyncio

import httpx
import pytest

from tests.test_auth import csrf_from


def login(client, settings):
    page = client.get("/login")
    client.post(
        "/login",
        data={"password": settings.app_password.get_secret_value(), "csrf_token": csrf_from(page)},
    )
    return csrf_from(client.get("/"))


def test_media_routes_require_login_and_csrf_before_receiving_body(client, settings):
    assert client.post("/api/tasks/upload", content=b"not-video").status_code == 401
    token = login(client, settings)
    assert client.post("/api/tasks/upload", content=b"not-video").status_code == 403
    assert (
        client.post(
            "/api/tasks/link", json={"share_text": "no link"}, headers={"X-CSRF-Token": token}
        ).status_code
        == 400
    )
    assert client.get("/files/" + "a" * 32).status_code == 404


@pytest.mark.parametrize("task_id", ["a" * 32, "foo"])
@pytest.mark.parametrize("declared_oversized", [False, True])
@pytest.mark.parametrize(
    ("authentication", "expected_status"),
    [("anonymous", 401), ("missing_csrf", 403), ("invalid_csrf", 403), ("unknown_task", 404)],
)
def test_identified_upload_rejects_invalid_requests_before_receiving_body(
    client, settings, task_id, declared_oversized, authentication, expected_status
):
    headers = {}
    if authentication != "anonymous":
        token = login(client, settings)
        if authentication == "invalid_csrf":
            headers["X-CSRF-Token"] = "invalid"
        elif authentication == "unknown_task":
            headers["X-CSRF-Token"] = token
    if declared_oversized:
        headers["Content-Length"] = str(settings.max_media_bytes + 1)

    async def exercise():
        reads = 0

        async def stream():
            nonlocal reads
            reads += 1
            yield b"untrusted-media"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver",
            cookies=client.cookies,
        ) as browser:
            response = await browser.post(
                f"/api/tasks/{task_id}/upload", headers=headers, content=stream()
            )
        assert response.status_code == expected_status
        assert reads == 0, "rejected upload must not consume the request body"
        assert not client.app.state.tasks.tasks

    asyncio.run(exercise())


def test_capabilities_report_real_tool_state_and_limits(client, settings):
    login(client, settings)
    value = client.get("/api/capabilities").json()
    assert value["limits"]["max_media_bytes"] == settings.max_media_bytes
    assert value["extraction_available"] == value["media_available"]
    assert value["planned_inputs"] == ["share_text", "video_upload"]
