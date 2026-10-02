def test_oversized_declared_body_is_rejected(client, settings):
    response = client.post("/login", content=b"x" * (settings.max_body_bytes + 1))
    assert response.status_code == 413


def test_body_limit_counts_actual_bytes_even_with_a_false_length(client, settings):
    response = client.post(
        "/login",
        content=b"x" * (settings.max_body_bytes + 1),
        headers={"Content-Length": "1"},
    )
    assert response.status_code == 413


def test_chunked_body_without_content_length_is_limited(client, settings):
    def chunks():
        yield b"x" * settings.max_body_bytes
        yield b"x"

    response = client.post("/login", content=chunks())
    assert response.status_code == 413


def test_health_endpoint_has_no_credentials_and_disables_caching(client):
    response = client.get("/healthz")
    assert response.json() == {"status": "ok", "service": "soundpick"}
    assert response.headers["cache-control"] == "no-store"
