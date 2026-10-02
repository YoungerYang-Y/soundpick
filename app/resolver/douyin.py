from app.resolver.base import MediaResolver, ResolvedVideo, ResolverNotConfigured


class DouyinResolver(MediaResolver):
    platform = "douyin"
    display_name = "抖音"

    async def resolve(self, url: str) -> ResolvedVideo:
        raise ResolverNotConfigured("抖音解析服务尚未接入")
