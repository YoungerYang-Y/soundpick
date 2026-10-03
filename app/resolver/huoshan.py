import re
from urllib.parse import parse_qs, urlsplit

from app.resolver.base import MediaResolver, ResolvedVideo
from app.resolver.provider import configured_provider, json_body, resolved
from app.resolver.registry import platform_of
from app.services.errors import MediaError
from app.services.network import SafeHTTP


class HuoshanResolver(MediaResolver):
    platform = "huoshan"
    display_name = "火山"
    available = True

    def __init__(self, network: SafeHTTP | None = None, endpoint: str = "", token: str = ""):
        self.network = network or SafeHTTP()
        self.endpoint = endpoint
        self.token = token

    @staticmethod
    def video_id(url: str) -> str | None:
        parsed = urlsplit(url)
        match = re.search(r"/share/item/(\d+)", parsed.path)
        if match:
            return match[1]
        candidate = parse_qs(parsed.query).get("item_id", [""])[0]
        return candidate if candidate.isdigit() else None

    async def resolve(self, url: str) -> ResolvedVideo:
        if platform_of(url) != self.platform:
            raise MediaError("请输入火山视频分享链接", "invalid_input", 400)
        if self.endpoint:
            return await configured_provider(self.network, self.endpoint, self.token, url)
        item_id = self.video_id(url)
        if not item_id:
            page = await self.network.fetch(url)
            if platform_of(page.url) != self.platform:
                raise MediaError("火山分享链接已失效，请重新分享或上传本地视频", "resolve_failed")
            item_id = self.video_id(page.url)
        if not item_id:
            raise MediaError("无法识别火山视频，请重新分享或上传本地视频", "resolve_failed")
        page = await self.network.fetch(
            f"https://share.huoshan.com/api/item/info?item_id={item_id}", follow_redirects=False
        )
        if page.status != 200:
            raise MediaError("火山链接暂时无法解析，请重新分享或上传本地视频", "resolve_failed")
        data = json_body(page.body).get("data") or {}
        item = data.get("item_info") or {} if isinstance(data, dict) else {}
        if not isinstance(item, dict):
            raise MediaError("火山解析结果无效", "resolve_failed")
        if str(item.get("item_id") or item.get("id")) != item_id:
            raise MediaError("火山返回的视频与分享链接不匹配，请重新分享", "resolve_failed")
        return resolved(item.get("url"), item.get("title", ""), item.get("author", ""))
