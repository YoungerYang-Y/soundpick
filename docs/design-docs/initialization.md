---
id: initialization-boundaries
status: stale
owner: project
tags: []
created: 2026-10-02
verified: 2026-10-02
---

# 初始化实现边界与架构候选

本文保留 `57ba420` 初始化提交时的骨架快照。当前 MVP 已继续实施，以 [MVP 设计](../active/0.1.0/audio-extraction/design.md) 及实际代码为准；不要将以下“当前实现范围”当作最新功能范围。

## 背景与动机

项目初始化包含登录、页面、配置、容器和开发环境，但尚未连通媒体处理。本文区分当前已实现的应用骨架、已决定的实现边界和仍待验证的单实例候选，避免将脚手架误认为可用的提取服务。

## 已确定实现边界

- Python `>=3.12,<3.15`，开发环境使用项目 `.mise.toml` 与 `.python-version` 指定的 Python 3.14.5。
- FastAPI 与 Uvicorn 提供 Web 服务，Jinja2 服务端渲染，页面使用原生 JavaScript；不需要 Node 构建。
- `pyproject.toml` 管理依赖；初始化使用运行和开发两份锁文件、项目内 `.venv` 与 `scripts/setup.sh`。
- `scripts/init_env.py` 只在 `.env` 不存在时创建配置，权限设为仅当前用户可读写，生成随机共享密码和 Session 签名密钥，不向终端输出秘密。
- 配置经 `pydantic-settings` 从环境与 `.env` 加载。环境支持 `local`、`production`；生产环境 Cookie 必须启用 Secure。
- 本地初始化配置使用 `APP_ENV=local`、`COOKIE_SECURE=false` 以支持回环地址上的 HTTP 开发；生产部署必须切换到 `APP_ENV=production`、`COOKIE_SECURE=true` 并使用 HTTPS。
- 登录使用共享密码、签名的 HttpOnly / Secure / SameSite=Lax Session Cookie，并校验表单 CSRF token。受保护页面未登录时重定向到登录页，受保护 API 返回 401。
- 基础请求体上限为 16 KiB；该限制不构成视频上传能力，上传端点和专门的文件大小边界仍待实现。
- Docker 目标使用 `python:3.14-slim`，镜像安装 FFmpeg 与 FFprobe，最终以非 root 用户运行。
- 本地 Compose 服务仅绑定 `127.0.0.1`；公网部署不能直接沿用本地 HTTP/Cookie 配置。

## 当前实现范围

- `GET /healthz` 提供不要求登录的最小存活检查。
- `GET /login`、`POST /login`、`GET /`、`POST /logout` 提供登录与受保护页面流程。
- `GET /api/capabilities` 需要登录，并返回 `extraction_available=false`、未接入的 provider 信息。
- 页面有链接/文本和文件输入的布局；提取按钮禁用，并明确提示目前不可提取。
- 应用通过 `create_app(Settings | None)` 工厂创建；导入模块时不读取运行配置。
- `app/resolver/base.py`、`douyin.py`、`huoshan.py` 仅提供契约和未配置 stub，不代表能解析真实分享链接。

以上为初始化阶段的实现边界；该阶段的本地验证已完成，后续 MVP 的实际验证单列于 README。

## 后续实现清单

1. 按平台验证分享链接和整段文字提取，明确抖音、火山的媒体来源是否确为原视频完整音轨。
2. 建立单视频任务状态和取消/超时行为；在本地上传与远端下载处落实容量、时长和请求超时边界。
3. 实现上传、媒体下载、FFprobe 检查、FFmpeg MP3 转换及结果校验。
4. 提供有权限保护的试听与附件下载响应，并处理浏览器 Range 请求。
5. 在成功、失败、超时和进程退出等路径清理临时文件；确定并记录清理保留期。
6. 完成手机浏览器试听、下载，以及 HTTPS 条件下系统分享/保存文件交互。

## 候选运行模型与待验证项

初版目标候选为单实例、单进程应用，以轻量内存状态查询任务；进程重启后不恢复在途任务。尚未通过负载或容错测试，不承诺跨实例、持久任务或进程重启恢复能力。若后续需要多实例或任务恢复，再评估持久化状态及队列方案，不预先引入数据库或 Redis。

Web Share 与文件保存能力需 HTTPS 和用户主动操作；浏览器不能保证微信等特定分享目标一定出现，也不能保证下载固定写入某个目录。

## 参考

- 产品范围：[`../requirements.md`](../requirements.md)
- 系统约束：[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)
