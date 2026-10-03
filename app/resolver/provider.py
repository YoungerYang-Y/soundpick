"""Small helpers for public-page data and explicitly configured JSON providers."""

import json
import re

import httpx

from app.resolver.base import ResolvedVideo
from app.services.errors import MediaError
from app.services.network import SafeHTTP, public_url


def router_data(html: str) -> dict:
    match = re.search(r"(?:window\.)?_ROUTER_DATA\s*=\s*", html)
    if not match:
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(html[match.end() :])
    except (ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def text_field(value: object, limit: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    # JSON may contain unpaired UTF-16 surrogates, which cannot be sent as UTF-8.
    normalized = value.encode("utf-16-le", errors="surrogatepass").decode(
        "utf-16-le", errors="replace"
    )
    return normalized[:limit]


def resolved(url, title="", author="") -> ResolvedVideo:
    if not isinstance(url, str) or not url:
        raise MediaError("未取得视频地址，请改上传本地视频", "resolve_failed")
    public_url(url)
    return ResolvedVideo(url, text_field(title, 1000), text_field(author))


def json_body(body: bytes) -> dict:
    try:
        value = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise MediaError(
            "平台返回了验证页面或无效内容，请改上传本地视频", "resolve_failed"
        ) from exc
    if not isinstance(value, dict):
        raise MediaError("平台解析结果无效", "resolve_failed")
    return value


async def configured_provider(
    network: SafeHTTP, endpoint: str, token: str, url: str
) -> ResolvedVideo:
    request_url = str(httpx.URL(endpoint).copy_merge_params({"url": url}))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    page = await network.fetch(request_url, headers=headers, follow_redirects=False)
    if page.status != 200:
        raise MediaError("解析服务不可用，请稍后再试或上传本地视频", "resolve_failed")
    value = json_body(page.body)
    author = value.get("author", "")
    if isinstance(author, dict):
        author = author.get("name", "")
    # Explicit normalized contract. music_url is intentionally never read.
    return resolved(value.get("video_url"), value.get("title", ""), author)
