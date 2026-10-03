"""One platform catalog for URL selection, adapter construction and public names."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.config import Settings
from app.resolver.base import MediaResolver
from app.services.errors import MediaError
from app.services.network import SafeHTTP, public_url


@dataclass(frozen=True)
class PlatformSpec:
    id: str
    display_name: str
    hosts: frozenset[str]
    resolver_factory: Callable[[SafeHTTP, str, str], MediaResolver]
    parser_setting: str | None = None


def _douyin(network: SafeHTTP, endpoint: str, token: str) -> MediaResolver:
    # Adapters use platform_of; importing them at construction avoids a cycle.
    from app.resolver.douyin import DouyinResolver

    return DouyinResolver(network, endpoint, token)


def _huoshan(network: SafeHTTP, endpoint: str, token: str) -> MediaResolver:
    from app.resolver.huoshan import HuoshanResolver

    return HuoshanResolver(network, endpoint, token)


PLATFORMS = (
    PlatformSpec(
        id="douyin",
        display_name="抖音",
        hosts=frozenset(
            {
                "douyin.com",
                "www.douyin.com",
                "v.douyin.com",
                "iesdouyin.com",
                "www.iesdouyin.com",
            }
        ),
        resolver_factory=_douyin,
        parser_setting="douyin_parser_url",
    ),
    PlatformSpec(
        id="huoshan",
        display_name="火山",
        hosts=frozenset(
            {
                "huoshan.com",
                "www.huoshan.com",
                "share.huoshan.com",
                "reflow.huoshan.com",
            }
        ),
        resolver_factory=_huoshan,
        parser_setting="huoshan_parser_url",
    ),
)


def platform_name(platform_id: str) -> str:
    """Built-in adapter names come from the catalog, rather than a second list."""
    return next(spec.display_name for spec in PLATFORMS if spec.id == platform_id)


def build_resolvers(
    settings: Settings, network: SafeHTTP, *, specs: Sequence[PlatformSpec] | None = None
) -> dict[str, MediaResolver]:
    catalog = PLATFORMS if specs is None else specs
    token = settings.parser_api_token.get_secret_value()
    resolvers = {}
    for spec in catalog:
        endpoint = getattr(settings, spec.parser_setting) if spec.parser_setting else ""
        resolver = spec.resolver_factory(network, endpoint, token)
        if resolver.platform != spec.id or resolver.display_name != spec.display_name:
            raise ValueError("平台注册与解析器标识不一致")
        if spec.id in resolvers:
            raise ValueError("平台注册标识重复")
        resolvers[spec.id] = resolver
    return resolvers


def platform_of(url: str, *, specs: Sequence[PlatformSpec] | None = None) -> str:
    parsed = public_url(url)
    catalog = PLATFORMS if specs is None else specs
    for spec in catalog:
        if parsed.host in spec.hosts:
            return spec.id
    names = [spec.display_name for spec in catalog]
    supported = "、".join(names[:-1]) + "和" + names[-1] if len(names) > 1 else "".join(names)
    message = (
        f"目前支持{supported}分享内容，请选择单个视频"
        if supported
        else "目前未启用分享平台，请上传本地视频"
    )
    raise MediaError(message, "unsupported_platform", 422)


def extract_share_url(text: str, *, specs: Sequence[PlatformSpec] | None = None) -> tuple[str, str]:
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
    return platform_of(urls[0], specs=specs), urls[0]
