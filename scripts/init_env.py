"""Create a private local .env without exposing or overwriting credentials."""

import os
import secrets
from pathlib import Path


def initialize_env(destination: Path) -> bool:
    content = (
        "# Local development. Use HTTPS + production/true before external access.\n"
        "APP_ENV=local\n"
        f"APP_PASSWORD={secrets.token_urlsafe(18)}\n"
        f"SESSION_SECRET={secrets.token_hex(32)}\n"
        "COOKIE_SECURE=false\n"
        "SESSION_MAX_AGE=604800\n"
        "MAX_BODY_BYTES=16384\n"
        "DATA_DIR=data\n"
        "APP_PORT=8000\n"
    )
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
    return True


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / ".env"
    if initialize_env(target):
        print("已创建权限为 600 的本地 .env。共享口令请在该文件中查看，不会输出到终端。")
    else:
        print("保留现有 .env，未覆盖其中的配置或口令。")
