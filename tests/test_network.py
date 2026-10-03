import asyncio

import httpcore
import httpx
import pytest

from app.services.errors import MediaError


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
def test_connect_failure_tries_next_validated_address(failure):
    from app.services.network import SafeHTTP

    addresses = ["2606:4700:4700::1111", "93.184.216.34"]
    seen = []

    async def dns(host, port):
        return addresses

    def handler(request):
        seen.append(request.url.host)
        assert request.headers["host"] == "media.example"
        assert request.extensions["sni_hostname"] == "media.example"
        if request.url.host == addresses[0]:
            raise failure("unreachable", request=request)
        return httpx.Response(200, content=b"data")

    page = asyncio.run(
        SafeHTTP(transport=httpx.MockTransport(handler), resolve=dns).fetch(
            "https://media.example/video"
        )
    )
    assert page.body == b"data"
    assert seen == addresses


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadError, httpx.ReadTimeout, httpx.WriteError, httpx.WriteTimeout],
)
def test_request_failure_after_connect_is_not_retried(failure):
    from app.services.network import SafeHTTP

    seen = []

    async def dns(host, port):
        return ["93.184.216.34", "1.1.1.1"]

    def handler(request):
        seen.append(request.url.host)
        raise failure("request failed", request=request)

    with pytest.raises(MediaError) as exc:
        asyncio.run(
            SafeHTTP(transport=httpx.MockTransport(handler), resolve=dns).fetch(
                "https://media.example/video"
            )
        )
    assert exc.value.code == "network_error"
    assert seen == ["93.184.216.34"]


def test_failed_http_response_is_not_retried_on_another_address():
    from app.services.network import SafeHTTP

    seen = []

    async def dns(host, port):
        return ["93.184.216.34", "1.1.1.1"]

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(503, content=b"unavailable")

    page = asyncio.run(
        SafeHTTP(transport=httpx.MockTransport(handler), resolve=dns).fetch(
            "https://media.example/video"
        )
    )
    assert page.status == 503
    assert seen == ["93.184.216.34"]


def test_all_connect_failures_return_safe_network_error():
    from app.services.network import SafeHTTP

    addresses = ["93.184.216.34", "1.1.1.1"]
    seen = []

    async def dns(host, port):
        return addresses

    def handler(request):
        seen.append(request.url.host)
        raise httpx.ConnectError("private connection detail", request=request)

    with pytest.raises(MediaError) as exc:
        asyncio.run(
            SafeHTTP(transport=httpx.MockTransport(handler), resolve=dns).fetch(
                "https://media.example/video"
            )
        )
    assert exc.value.code == "network_error"
    assert "private" not in exc.value.message
    assert seen == addresses


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

    def forbidden(request):
        pytest.fail("mixed public/private DNS must be rejected before any connection")

    with pytest.raises(MediaError):
        asyncio.run(
            SafeHTTP(transport=httpx.MockTransport(forbidden), resolve=mixed).fetch(
                "https://media.example/"
            )
        )

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


def test_connect_error_after_stream_started_does_not_replay_download(tmp_path):
    from app.services.network import SafeHTTP

    seen = []

    async def dns(host, port):
        return ["93.184.216.34", "1.1.1.1"]

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(
            200, stream=ChunkStream(b"x" * 65536, httpx.ConnectError("late failure"))
        )

    target = tmp_path / "partial"
    with pytest.raises(MediaError) as exc:
        asyncio.run(
            SafeHTTP(transport=httpx.MockTransport(handler), resolve=dns).download(
                "https://media.example/video", target, 131072
            )
        )
    assert exc.value.code == "network_error"
    assert seen == ["93.184.216.34"]
    assert not target.exists()


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


def test_redirect_to_same_ip_uses_a_new_tls_connection():
    from app.services.network import SafeHTTP

    tls_names = []
    request_tls_names = []
    streams = []

    class Stream(httpcore.AsyncNetworkStream):
        def __init__(self):
            self.pending = b""
            self.tls_name = None
            self.closed = False

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            self.tls_name = server_hostname
            tls_names.append(server_hostname)
            return self

        async def write(self, buffer, timeout=None):
            if buffer.startswith(b"GET "):
                request_tls_names.append(self.tls_name)
                if b"host: media.example\r\n" in buffer.lower():
                    self.pending = (
                        b"HTTP/1.1 302 Found\r\n"
                        b"Location: https://other.example/final\r\nContent-Length: 0\r\n\r\n"
                    )
                else:
                    self.pending = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndone"

        async def read(self, max_bytes, timeout=None):
            result, self.pending = self.pending[:max_bytes], self.pending[max_bytes:]
            return result

        async def aclose(self):
            self.closed = True

    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            assert host == "93.184.216.34"
            assert port == 443
            stream = Stream()
            streams.append(stream)
            return stream

    transport = httpx.AsyncHTTPTransport()
    # Replace only the socket boundary; retain the real HTTPX/httpcore connection pool.
    transport._pool._network_backend = Backend()
    page = asyncio.run(
        SafeHTTP(transport=transport, resolve=public_dns).fetch("https://media.example/a")
    )
    assert page.body == b"done"
    assert request_tls_names == ["media.example", "other.example"]
    assert tls_names == ["media.example", "other.example"]
    assert len(streams) == 2
    assert all(stream.closed for stream in streams)


def test_redirect_does_not_forward_source_cookies_to_another_host_on_the_same_ip():
    from app.services.network import SafeHTTP

    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                302,
                headers={
                    "location": "https://other.example/final",
                    "set-cookie": "private=source-secret; Path=/; Secure; HttpOnly",
                },
            )
        return httpx.Response(200, content=b"done")

    page = asyncio.run(
        SafeHTTP(transport=httpx.MockTransport(handler), resolve=public_dns).fetch(
            "https://media.example/a"
        )
    )
    assert page.body == b"done"
    assert len(seen) == 2
    assert "cookie" not in seen[1].headers


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
