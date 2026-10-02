from app.resolver.base import MediaResolver, ResolvedVideo, ResolverNotConfigured


class HuoshanResolver(MediaResolver):
    platform = "huoshan"
    display_name = "火山"

    async def resolve(self, url: str) -> ResolvedVideo:
        raise ResolverNotConfigured("火山解析服务尚未接入")
