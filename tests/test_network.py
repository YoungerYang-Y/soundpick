import asyncio

import httpx
import pytest

from app.services.errors import MediaError


def test_public_dns_is_pinned_while_host_and_tls_name_are_preserved():
    from app.services.network import SafeHTTP

    async def dns(host, port):
        return ["93.184.216.34"]

    async def handler(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "media.example"
        assert request.extensions["sni_hostname"] == "media.example"
        return httpx.Response(200, content=b"data")

    network = SafeHTTP(transport=httpx.MockTransport(handler), resolve=dns)
    page = asyncio.run(network.fetch("https://media.example/video"))
    assert page.body == b"data"


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/a", "file:///etc/passwd", "https://user:pass@host/a", "http://x:8080"]
)
def test_unsafe_urls_never_reach_transport(url):
    from app.services.network import SafeHTTP

    async def handler(request):
        pytest.fail("unsafe request reached transport")

    with pytest.raises(MediaError):
        asyncio.run(SafeHTTP(transport=httpx.MockTransport(handler)).fetch(url))


def test_mixed_public_private_dns_and_private_redirect_are_rejected():
    from app.services.network import SafeHTTP

    async def mixed(host, port):
        return ["93.184.216.34", "10.0.0.1"]

    with pytest.raises(MediaError):
        asyncio.run(SafeHTTP(resolve=mixed).fetch("https://media.example/"))

    async def public(host, port):
        return ["93.184.216.34"]

    def redirect(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    with pytest.raises(MediaError):
        asyncio.run(
            SafeHTTP(transport=httpx.MockTransport(redirect), resolve=public).fetch(
                "https://media.example/"
            )
        )


def test_download_limit_removes_partial_file(tmp_path):
    from app.services.network import SafeHTTP

    async def public(host, port):
        return ["93.184.216.34"]

    def response(request):
        return httpx.Response(200, content=b"x" * 1025)

    target = tmp_path / "input"
    with pytest.raises(MediaError, match="过大"):
        asyncio.run(
            SafeHTTP(transport=httpx.MockTransport(response), resolve=public).download(
                "https://media.example/", target, 1024
            )
        )
    assert not target.exists()


async def public_dns(host, port):
    return ["93.184.216.34"]


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


@pytest.mark.parametrize("action", ["fetch", "download"])
def test_actual_stream_limit_without_declared_length_removes_partial_file(tmp_path, action):
    from app.services.network import SafeHTTP

    def handler(request):
        return httpx.Response(200, stream=ChunkStream(b"x" * 65536, b"y"))

    network = SafeHTTP(transport=httpx.MockTransport(handler), resolve=public_dns)
    target = tmp_path / "partial"
    with pytest.raises(MediaError) as exc:
        if action == "download":
            asyncio.run(network.download("https://media.example/a", target, 65536))
        else:
            asyncio.run(network.fetch("https://media.example/a", max_bytes=65536))
    assert exc.value.code == "size_limit"
    assert not target.exists()


def test_redirect_strips_credentials_and_revalidates_destination():
    from app.services.network import SafeHTTP

    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "https://other.example/final"})
        assert "authorization" not in request.headers
        assert request.headers["host"] == "other.example"
        assert request.extensions["sni_hostname"] == "other.example"
        return httpx.Response(200, content=b"done")

    network = SafeHTTP(transport=httpx.MockTransport(handler), resolve=public_dns)
    page = asyncio.run(
        network.fetch("https://media.example/a", headers={"Authorization": "Bearer private-test"})
    )
    assert page.body == b"done"
    assert page.url == "https://other.example/final"
    assert len(seen) == 2


@pytest.mark.parametrize(
    "headers", [{"content-length": "bad"}, {"content-length": "-1"}, {"content-encoding": "gzip"}]
)
def test_invalid_response_boundaries_are_rejected(headers):
    from app.services.network import SafeHTTP

    network = SafeHTTP(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, stream=ChunkStream(b"body"))
        ),
        resolve=public_dns,
    )
    with pytest.raises(MediaError):
        asyncio.run(network.fetch("https://media.example/a"))


@pytest.mark.parametrize("status,body", [(403, b"error"), (200, b"")])
def test_invalid_download_response_is_cleaned(tmp_path, status, body):
    from app.services.network import SafeHTTP

    target = tmp_path / "input"
    network = SafeHTTP(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, stream=ChunkStream(body))
        ),
        resolve=public_dns,
    )
    with pytest.raises(MediaError) as exc:
        asyncio.run(network.download("https://media.example/a", target, 1024))
    assert exc.value.code == "download_failed"
    assert not target.exists()


def test_redirect_loop_and_dns_failure_have_safe_errors():
    from app.services.network import SafeHTTP

    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "/again"})

    network = SafeHTTP(transport=httpx.MockTransport(redirect), resolve=public_dns)
    with pytest.raises(MediaError):
        asyncio.run(network.fetch("https://media.example/a"))
    assert len(calls) == 6

    async def bad_dns(host, port):
        raise OSError("private DNS detail")

    with pytest.raises(MediaError) as exc:
        asyncio.run(SafeHTTP(resolve=bad_dns).fetch("https://media.example/private"))
    assert exc.value.code == "network_error"
    assert "private" not in exc.value.message


def test_cancelled_stream_removes_partial_download(tmp_path):
    from app.services.network import SafeHTTP

    target = tmp_path / "input"
    network = SafeHTTP(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=ChunkStream(b"data", asyncio.CancelledError())
            )
        ),
        resolve=public_dns,
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(network.download("https://media.example/a", target, 1024))
    assert not target.exists()


def test_download_failure_does_not_delete_a_preexisting_target(tmp_path):
    from app.services.network import SafeHTTP

    target = tmp_path / "input"
    target.write_bytes(b"keep original")
    network = SafeHTTP(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"replacement")),
        resolve=public_dns,
    )
    with pytest.raises(MediaError):
        asyncio.run(network.download("https://media.example/a", target, 1024))
    assert target.read_bytes() == b"keep original"
