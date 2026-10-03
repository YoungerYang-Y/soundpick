"""Check media readiness without printing configuration secrets."""

import subprocess
import sys

from app.config import Settings
from app.services.media import MediaProcessor


def main() -> int:
    media = MediaProcessor(Settings())
    if not media.available:
        print("FFmpeg/FFprobe 未就绪，请安装系统工具或配置 FFMPEG_BIN/FFPROBE_BIN。")
        return 1
    for name, executable in (("FFmpeg", media.ffmpeg), ("FFprobe", media.ffprobe)):
        try:
            result = subprocess.run(
                [executable, "-version"], capture_output=True, text=True, timeout=10, check=True
            )
        except (OSError, subprocess.SubprocessError):
            print(f"{name} 无法运行。")
            return 1
        print(result.stdout.splitlines()[0])
    try:
        codecs = subprocess.run(
            [media.ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        print("无法检查 MP3 编码能力。")
        return 1
    if "libmp3lame" not in codecs.stdout:
        print("FFmpeg 缺少 libmp3lame，不能生成 MP3。")
        return 1
    print("媒体运行工具及 MP3 编码器可用；真实视频完整性仍需运行测试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
