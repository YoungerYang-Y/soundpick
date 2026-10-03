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
