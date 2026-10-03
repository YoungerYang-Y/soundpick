"""Run project checks with configured media tools; never silently skip real media."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class ToolSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--browser", action="store_true")
    args, extra = parser.parse_known_args()
    tools = ToolSettings()
    if not shutil.which(tools.ffmpeg_bin) or not shutil.which(tools.ffprobe_bin):
        print("真实 FFmpeg/FFprobe 未就绪，请先配置工具；本检查不会跳过媒体验收。", file=sys.stderr)
        return 2
    env = {**os.environ, "FFMPEG_BIN": tools.ffmpeg_bin, "FFPROBE_BIN": tools.ffprobe_bin}
    if args.browser:
        node = os.environ.get("SOUNDPICK_NODE_BIN") or shutil.which("node")
        if not node:
            print("浏览器回归需要 Node 22，请在 mise 环境运行。", file=sys.stderr)
            return 2
        return subprocess.call(
            [node, str(ROOT / "tests/browser_flow.cjs"), *extra], cwd=ROOT, env=env
        )
    prefix = [sys.executable]
    if args.coverage:
        (ROOT / "work/coverage").mkdir(parents=True, exist_ok=True)
        prefix += ["-m", "coverage", "run"]
    result = subprocess.call(
        [*prefix, "-m", "pytest", "--require-media-tools", *extra], cwd=ROOT, env=env
    )
    if result or not args.coverage:
        return result
    return subprocess.call([sys.executable, "-m", "coverage", "report"], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
