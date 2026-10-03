"""Explicit platform registration keeps URL selection separate from adapters."""

import re

from app.services.errors import MediaError
from app.services.network import public_url

PLATFORM_HOSTS = {
    "douyin": {
        "douyin.com",
        "www.douyin.com",
        "v.douyin.com",
        "iesdouyin.com",
        "www.iesdouyin.com",
    },
    "huoshan": {"huoshan.com", "www.huoshan.com", "share.huoshan.com", "reflow.huoshan.com"},
}


def platform_of(url: str) -> str:
    parsed = public_url(url)
    for platform, hosts in PLATFORM_HOSTS.items():
        if parsed.host in hosts:
            return platform
    raise MediaError("目前支持抖音和火山分享内容，请选择单个视频", "unsupported_platform", 422)


def extract_share_url(text: str) -> tuple[str, str]:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4096:
        raise MediaError("请粘贴分享链接或分享文字（最多4096字）", "invalid_input", 400)
    urls = list(
        dict.fromkeys(
            match.rstrip(".,;!?，。；！？”’）)]}")
            for match in re.findall(r'https?://[^\s<>"\'，。；！？“”]+', text)
        )
    )
    if len(urls) != 1:
        raise MediaError("请提供一个视频分享链接", "invalid_input", 400)
    return platform_of(urls[0]), urls[0]
