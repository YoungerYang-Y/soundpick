#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ ! -x .venv/bin/python ]]; then
  if ! command -v mise >/dev/null 2>&1; then
    echo "请在 WSL 的 mise 环境中运行；本脚本不会安装全局运行时。" >&2
    exit 1
  fi
  mise exec -- python -m venv .venv
fi

.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 14), "请使用项目指定的 Python 3.14 虚拟环境"'
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.lock
.venv/bin/python -m pip install --disable-pip-version-check --no-deps -e .
echo "项目依赖已安装。接着运行 .venv/bin/python scripts/init_env.py。"
