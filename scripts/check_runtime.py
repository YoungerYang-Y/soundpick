"""Check media readiness without printing configuration secrets."""

import asyncio
import sys

from app.config import Settings
from app.services.media import MediaProcessor


def main() -> int:
    media = MediaProcessor(Settings())
    if not asyncio.run(media.check_readiness()):
        print(media.readiness_error)
        return 1
    for version in media.versions.values():
        print(version)
    print("媒体运行工具及 MP3 编码器可用；真实视频完整性仍需运行测试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
