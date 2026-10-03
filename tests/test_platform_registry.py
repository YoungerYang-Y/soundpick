import asyncio

import pytest

from app.resolver import registry
from app.resolver.base import MediaResolver, ResolvedVideo
from app.services.errors import MediaError
from app.services.network import SafeHTTP


def test_one_platform_catalog_drives_selection_factory_and_supported_names(settings):
    assert hasattr(registry, "PlatformSpec"), "平台注册缺少统一描述"
    assert hasattr(registry, "build_resolvers"), "平台注册缺少统一装配入口"
    seen = []

    class ThirdResolver(MediaResolver):
        platform = "third"
        display_name = "第三平台"
        available = True

        def __init__(self, network, endpoint, token):
            seen.append((network, endpoint, token))

        async def resolve(self, url):
            return ResolvedVideo("https://video.example/third.mp4", title="第三平台视频")

    third = registry.PlatformSpec(
        id="third",
        display_name="第三平台",
        hosts=frozenset({"third.example"}),
        resolver_factory=ThirdResolver,
    )
    specs = (*registry.PLATFORMS, third)
    network = SafeHTTP()
    resolvers = registry.build_resolvers(settings, network, specs=specs)
    assert list(resolvers) == [spec.id for spec in specs]
    assert [resolver.display_name for resolver in resolvers.values()] == [
        spec.display_name for spec in specs
    ]
    assert seen == [(network, "", settings.parser_api_token.get_secret_value())]
    assert registry.platform_of("https://third.example/video/123", specs=specs) == "third"
    assert registry.extract_share_url("第三个 https://third.example/video/123", specs=specs) == (
        "third",
        "https://third.example/video/123",
    )
    video = asyncio.run(resolvers["third"].resolve("https://third.example/video/123"))
    assert video.video_url == "https://video.example/third.mp4"
    with pytest.raises(MediaError) as error:
        registry.platform_of("https://unknown.example/video/123", specs=specs)
    assert all(spec.display_name in error.value.message for spec in specs)


def test_builtin_factories_receive_existing_provider_configuration(settings):
    assert hasattr(registry, "build_resolvers"), "平台注册缺少统一装配入口"
    configured = settings.model_copy(
        update={
            "douyin_parser_url": "https://provider.example/douyin",
            "huoshan_parser_url": "https://provider.example/huoshan",
        }
    )
    network = SafeHTTP()
    resolvers = registry.build_resolvers(configured, network)
    for spec in registry.PLATFORMS:
        assert resolvers[spec.id].network is network
        assert resolvers[spec.id].endpoint == getattr(configured, spec.parser_setting)
        assert resolvers[spec.id].platform == spec.id
