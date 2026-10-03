import re
from urllib.parse import parse_qs, urlsplit

from app.resolver.base import MediaResolver, ResolvedVideo
from app.resolver.provider import configured_provider, json_body, resolved, router_data
from app.resolver.registry import platform_of
from app.services.errors import MediaError
from app.services.network import SafeHTTP


class DouyinResolver(MediaResolver):
    platform = "douyin"
    display_name = "抖音"
    available = True

    def __init__(self, network: SafeHTTP | None = None, endpoint: str = "", token: str = ""):
        self.network = network or SafeHTTP()
        self.endpoint = endpoint
        self.token = token

    @staticmethod
    def video_id(url: str) -> str | None:
        parsed = urlsplit(url)
        match = re.search(r"/(?:share/)?video/(\d+)", parsed.path)
        if match:
            return match[1]
        candidate = parse_qs(parsed.query).get("modal_id", [""])[0]
        return candidate if candidate.isdigit() else None

    @staticmethod
    def from_item(item: dict) -> ResolvedVideo:
        video = item.get("video") or {}
        if not isinstance(video, dict):
            raise MediaError("该分享内容不是可处理的视频", "resolve_failed")
        for field in ("play_addr", "download_addr"):
            address = video.get(field) or {}
            urls = address.get("url_list", []) if isinstance(address, dict) else []
            if urls:
                author = item.get("author") or {}
                return resolved(
                    urls[0],
                    item.get("desc", ""),
                    author.get("nickname", "") if isinstance(author, dict) else "",
                )
        raise MediaError("未取得原视频地址，请改上传本地视频", "resolve_failed")

    async def resolve(self, url: str) -> ResolvedVideo:
        if platform_of(url) != self.platform:
            raise MediaError("请输入抖音视频分享链接", "invalid_input", 400)
        if self.endpoint:
            return await configured_provider(self.network, self.endpoint, self.token, url)
        video_id = self.video_id(url)
        if not video_id:
            page = await self.network.fetch(url)
            if platform_of(page.url) != self.platform:
                raise MediaError("分享链接已失效", "resolve_failed")
            video_id = self.video_id(page.url)
        if not video_id:
            raise MediaError("无法识别抖音视频，请重新分享或上传本地视频", "resolve_failed")
        for host in ("api5-normal-c-hl.amemv.com", "aweme.snssdk.com"):
            try:
                page = await self.network.fetch(
                    f"https://{host}/aweme/v1/feed/?aweme_id={video_id}&aid=1128"
                )
                if page.status != 200:
                    continue
                for item in json_body(page.body).get("aweme_list", []):
                    if isinstance(item, dict) and str(item.get("aweme_id")) == video_id:
                        return self.from_item(item)
            except MediaError:
                continue
        page = await self.network.fetch(f"https://www.iesdouyin.com/share/video/{video_id}/")
        data = router_data(page.body.decode("utf-8", errors="replace"))
        loader = data.get("loaderData") or {}
        if isinstance(loader, dict):
            for content in loader.values():
                info = content.get("videoInfoRes") if isinstance(content, dict) else None
                for item in (info or {}).get("item_list", []) if isinstance(info, dict) else []:
                    if isinstance(item, dict) and str(item.get("aweme_id")) == video_id:
                        return self.from_item(item)
        raise MediaError("抖音链接暂时无法解析，请重新分享或上传本地视频", "resolve_failed")
