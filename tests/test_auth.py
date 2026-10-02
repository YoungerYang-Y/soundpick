import re

from fastapi.testclient import TestClient

from app.main import create_app


def csrf_from(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def test_shared_password_grants_access_and_logout_revokes_browser_session(client, settings):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"password": settings.app_password.get_secret_value(), "csrf_token": csrf_from(page)},
    )
    assert response.status_code == 303
    page = client.get("/")
    assert page.status_code == 200
    assert "音频提取功能正在准备中，暂不可用" in page.text
    response = client.post("/logout", data={"csrf_token": csrf_from(page)})
    assert response.status_code == 303
    assert client.get("/api/capabilities").status_code == 401


def test_wrong_password_is_rejected_without_echoing_it(client):
    page = client.get("/login")
    response = client.post(
        "/login", data={"password": "wrong-password", "csrf_token": csrf_from(page)}
    )
    assert response.status_code == 401
    assert "wrong-password" not in response.text
    assert client.get("/api/capabilities").status_code == 401


def test_login_and_logout_require_valid_csrf_tokens(client, settings):
    client.get("/login")
    response = client.post("/login", data={"password": settings.app_password.get_secret_value()})
    assert response.status_code == 403
    assert client.post("/logout", data={"csrf_token": "invalid"}).status_code == 403


def test_production_session_cookie_is_secure_and_http_only(settings):
    secure_settings = settings.model_copy(update={"app_env": "production", "cookie_secure": True})
    with TestClient(create_app(secure_settings), base_url="https://testserver") as client:
        cookie = client.get("/login").headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie


def test_tampered_session_cookie_is_rejected(client, settings):
    page = client.get("/login")
    client.post(
        "/login",
        data={"password": settings.app_password.get_secret_value(), "csrf_token": csrf_from(page)},
    )
    cookie = client.cookies.get("soundpick_session")
    client.cookies.clear()
    client.cookies.set("soundpick_session", cookie + "tampered")
    assert client.get("/api/capabilities").status_code == 401


def test_capabilities_do_not_claim_unimplemented_extraction_is_available(client, settings):
    page = client.get("/login")
    client.post(
        "/login",
        data={"password": settings.app_password.get_secret_value(), "csrf_token": csrf_from(page)},
    )
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json()["extraction_available"] is False
    assert all(not platform["available"] for platform in response.json()["platforms"])


def test_repeated_login_attempts_are_limited(client):
    page = client.get("/login")
    form = {"password": "wrong-password", "csrf_token": csrf_from(page)}
    for _ in range(10):
        assert client.post("/login", data=form).status_code == 401
    response = client.post("/login", data=form)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
