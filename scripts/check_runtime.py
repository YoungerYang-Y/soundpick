"""Check media tools and optional Redis without writing data or printing secrets."""

import asyncio
import sys
from contextlib import suppress

from app.config import Settings
from app.services.media import MediaProcessor
from app.services.redis_diagnostics import FAILURE_MESSAGES, redis_failure_reason
from app.services.redis_protection import RedisBackend


def check_redis(settings: Settings) -> bool:
    url = settings.redis_url.get_secret_value()
    if not url:
        print("Redis 未配置；防刷使用进程内保护，重启后内存额度与限频状态会丢失。")
        return True
    backend = RedisBackend(
        url,
        key_prefix=settings.redis_key_prefix,
        timeout_seconds=settings.redis_timeout_seconds,
    )
    try:
        backend.probe()
    except Exception as error:
        reason = redis_failure_reason(error)
        print(f"Redis 检查未通过（reason={reason}）：{FAILURE_MESSAGES[reason]}。")
        print("Redis 不可用时服务仍会降级到内存保护；本检查返回失败。")
        return False
    finally:
        with suppress(Exception):
            backend.close()
    print("Redis 连接及只读 Lua/MEMORY 检查通过；未验证写入权限及 Redis 持久化配置。")
    return True


def main() -> int:
    settings = Settings()
    media = MediaProcessor(settings)
    media_ready = asyncio.run(media.check_readiness())
    if not media_ready:
        print(media.readiness_error)
    else:
        for version in media.versions.values():
            print(version)
        print("媒体运行工具及 MP3 编码器可用；真实视频完整性仍需运行测试。")
    redis_ready = check_redis(settings)
    return 0 if media_ready and redis_ready else 1


if __name__ == "__main__":
    sys.exit(main())
