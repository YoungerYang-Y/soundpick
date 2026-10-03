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


def test_capabilities_report_real_tool_state_and_limits(client, settings):
    login(client, settings)
    value = client.get("/api/capabilities").json()
    assert value["limits"]["max_media_bytes"] == settings.max_media_bytes
    assert value["extraction_available"] == value["media_available"]
    assert value["planned_inputs"] == ["share_text", "video_upload"]
