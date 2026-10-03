"""Bounded public HTTP, with validated DNS pinned to the connection address."""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.services.errors import MediaError

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


@dataclass(frozen=True)
class HTTPPage:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


async def resolve_addresses(host: str, port: int) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(record[4][0] for record in records))


def public_url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
        if (
            len(value) > 8192
            or url.scheme not in ("http", "https")
            or not url.host
            or url.userinfo
            or url.port not in (None, 80, 443)
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(url.host)
        except ValueError:
            if url.host.lower() == "localhost" or "." not in url.host:
                raise ValueError from None
        else:
            if not address.is_global:
                raise ValueError
        return url.copy_with(fragment=None)
    except (ValueError, httpx.InvalidURL) as exc:
        raise MediaError("链接地址不安全或格式无效", "unsafe_url", 400) from exc


class SafeHTTP:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: Callable[[str, int], Awaitable[list[str]]] = resolve_addresses,
    ) -> None:
        self.transport = transport
        self.resolve = resolve

    @asynccontextmanager
    async def _response(
        self, value: str, headers: dict | None = None, follow_redirects: bool = True
    ) -> AsyncIterator[tuple[str, httpx.Response]]:
        current = public_url(value)
        supplied = headers or {}
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                trust_env=False,
                timeout=httpx.Timeout(30, connect=10),
                follow_redirects=False,
            ) as client:
                for hop in range(6):
                    addresses = await asyncio.wait_for(
                        self.resolve(
                            current.host, current.port or (443 if current.scheme == "https" else 80)
                        ),
                        timeout=10,
                    )
                    if not addresses or any(
                        not ipaddress.ip_address(address).is_global for address in addresses
                    ):
                        raise MediaError("链接指向非公网地址，已拒绝访问", "unsafe_url", 400)
                    request_headers = {
                        "User-Agent": MOBILE_UA,
                        "Accept-Encoding": "identity",
                        **supplied,
                        "Host": current.netloc.decode("ascii"),
                    }
                    pinned = current.copy_with(host=addresses[0])
                    async with client.stream(
                        "GET",
                        pinned,
                        headers=request_headers,
                        extensions={"sni_hostname": current.host},
                    ) as response:
                        if follow_redirects and response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location or hop == 5:
                                raise MediaError("链接跳转次数过多或地址失效", "network_error")
                            current = public_url(str(current.join(location)))
                            supplied = {}  # Never forward credentials to a redirect destination.
                            continue
                        if (
                            response.headers.get("content-encoding", "identity").lower()
                            != "identity"
                        ):
                            raise MediaError(
                                "来源返回不支持的压缩响应，请稍后重试", "network_error"
                            )
                        yield str(current), response
                        return
        except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
            raise MediaError("来源暂时无法访问，请稍后重试或上传本地视频", "network_error") from exc

    @staticmethod
    def _check_length(response: httpx.Response, max_bytes: int) -> None:
        length = response.headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
            except ValueError as exc:
                raise MediaError("来源文件长度无效", "network_error") from exc
            if size < 0 or size > max_bytes:
                raise MediaError("来源文件过大，已停止下载", "size_limit", 413)

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        headers: dict | None = None,
        follow_redirects: bool = True,
    ) -> HTTPPage:
        async with self._response(url, headers, follow_redirects) as (final_url, response):
            self._check_length(response, max_bytes)
            body = bytearray()
            async for chunk in response.aiter_bytes(65536):
                if len(body) + len(chunk) > max_bytes:
                    raise MediaError("来源响应过大，已停止读取", "size_limit", 413)
                body.extend(chunk)
            return HTTPPage(final_url, response.status_code, dict(response.headers), bytes(body))

    async def download(self, url: str, target: Path, max_bytes: int) -> None:
        try:
            async with self._response(url) as (_, response):
                if response.status_code != 200:
                    raise MediaError("视频地址失效或无法下载，请重新分享", "download_failed")
                self._check_length(response, max_bytes)
                count = 0
                with target.open("xb") as output:
                    target.chmod(0o600)
                    async for chunk in response.aiter_bytes(65536):
                        count += len(chunk)
                        if count > max_bytes:
                            raise MediaError("视频文件过大，已停止下载", "size_limit", 413)
                        output.write(chunk)
                if not count:
                    raise MediaError("来源返回空视频", "download_failed")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
