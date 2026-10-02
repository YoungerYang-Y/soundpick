# 音拾 · soundpick

音拾是供本人和家人使用的手机优先网页，用于从抖音、火山分享内容或本地视频中提取完整原音轨并保存为 MP3。当前项目处于初始化阶段：提供登录和页面骨架，尚不能提取或下载音频。

## 本地运行

需要 Python `>=3.12,<3.15` 和 mise；项目配置使用 Python 3.14.5。

```bash
./scripts/setup.sh
.venv/bin/python scripts/init_env.py
.venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

首次运行 `init_env.py` 会在项目根目录安全创建 `.env`，其中包含随机共享密码和会话密钥。登录密码可自行在本机读取 `.env` 中的 `APP_PASSWORD`；不要将密码粘贴到聊天或日志。初始化不会覆盖已有 `.env`。

启动后访问 `http://127.0.0.1:8000`。也可以使用 `make dev` 启动。默认仅监听本机回环地址，当前配置用于本地开发。

运行依赖的精确版本保存在 `requirements.lock`，开发依赖在 `requirements-dev.lock`。调整 `pyproject.toml` 后需要重新解析并更新锁文件，不要仅修改声明后沿用旧锁。

## 当前能力

- `GET /healthz` 提供最小存活检查。
- 共享密码登录、退出与受保护的页面。
- 能力探测接口明确报告当前未接入提取服务。
- 页面提供输入交互骨架，提取按钮保持禁用。

链接解析、视频上传、媒体任务、原音轨校验、MP3 转换、试听与下载、临时文件清理仍待实现。产品范围与未决限制见 [`docs/requirements.md`](docs/requirements.md)，初始化边界见 [`docs/design-docs/initialization.md`](docs/design-docs/initialization.md)。

## 本地检查

```bash
make test
make lint
```

2026-10-02，在本项目的 mise Python 3.14.5 虚拟环境中：依赖安装脚本执行成功，15 个针对性测试通过，Ruff 静态检查与格式检查通过，Node 22 完成页面脚本语法检查。实际启动临时 Uvicorn 服务后，健康检查、登录、受保护页面、静态资源、能力探测和退出的 HTTP 流程通过；验证完成后已关闭该进程。

测试客户端当前有一条 Starlette 提示迁移到 `httpx2` 的弃用警告，测试结果仍通过。上述结果是本地检查，不代表 CI、浏览器视觉检查或手机真机验收；内置浏览器连接因运行环境路径错误未能建立。

## 容器运行

```bash
docker compose up --build
```

Compose 读取 `.env`，默认绑定 `127.0.0.1:8000`，使用命名数据卷、非 root 用户、单应用进程和日志轮转。镜像声明安装 FFmpeg/FFprobe，为后续媒体处理预留；这不代表提取功能已实现。对外部署前需要 HTTPS 入口，并设置 `APP_ENV=production`、`COOKIE_SECURE=true`。

当前 WSL 没有可用的 Docker 集成或 FFmpeg，容器构建与运行尚未验证。Git 提交按用户单独授权执行；尚未配置远端、推送或部署。
