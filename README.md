# 音拾 · soundpick

音拾是供本人和家人使用的手机优先网页，用于从抖音、火山分享内容或本地视频中提取完整原音轨并保存为 MP3。MVP 主链路已实现，本地真实转码、一个抖音样本和一个当前火山版分享样本的实网链路已通过；手机真机保存与分享仍待验证，完整验收仍在进行。

## 本地运行

需要 Python `>=3.12,<3.15` 和 mise；项目配置使用 Python 3.14.5。

```bash
./scripts/setup.sh
.venv/bin/python scripts/init_env.py
.venv/bin/python -m scripts.check_runtime
.venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

首次运行 `init_env.py` 会在项目根目录安全创建 `.env`，其中包含随机共享密码和会话密钥。登录密码可自行在本机读取 `.env` 中的 `APP_PASSWORD`；不要将密码粘贴到聊天或日志。初始化不会覆盖已有 `.env`。

启动后访问 `http://127.0.0.1:8000`。也可以使用 `make dev` 启动。默认仅监听本机回环地址，当前配置用于本地开发。

运行环境需要含 `libmp3lame` 的 FFmpeg 和 FFprobe。可以使用系统工具，或在 `.env` 配置 `FFMPEG_BIN`、`FFPROBE_BIN` 的绝对路径；项目不会自动安装系统软件。工具缺失时页面说明服务未就绪，新任务返回 503。

本机已按授权在项目忽略目录安装 FFmpeg/FFprobe 的 Linux 64 位独立工具，并将两项路径写入私有 `.env`。来源、日期发行、SHA-256 和真实测试命令见 [媒体工具安装记录](docs/references/media-tools.md)。二进制和本机绝对路径不提交到仓库。

运行依赖的精确版本保存在 `requirements.lock`，开发依赖在 `requirements-dev.lock`。调整 `pyproject.toml` 后需要重新解析并更新锁文件，不要仅修改声明后沿用旧锁。

## 当前能力

- `GET /healthz` 提供最小存活检查。
- 共享密码登录、退出与受保护的页面。
- 分享链接或完整分享文本解析、本地视频流式上传，一次处理一个视频。
- 媒体容器、视频/音频流及时长检查；从原视频第一条完整音轨生成 192 kbps MP3，不读取背景音乐字段。
- 内存任务进度、取消、超时、会话所属结果；输入在终止路径删除，MP3 到期清理，重启清理本应用残留。
- 原生播放器、Range 试听、标准 UTF-8 附件下载；安全页面且浏览器支持时可准备文件，再点击系统分享。
- 手机保存指引、上传进度、断网重连与错误重试；不提供账户、处理历史或批量任务。

产品范围见 [`docs/requirements.md`](docs/requirements.md)，实施规格、设计和计划见 [`MVP 目录`](docs/active/0.1.0/audio-extraction/plan.md)。平台可解析性受链接状态与平台访问策略影响，不能保证所有分享链接成功。

## 配置与平台解析

工程默认：单视频 200 MiB、30 分钟；上传超时 300 秒、处理超时 600 秒，结果保留 1 小时，最多保留 32 个任务（含失败和取消）。这些值均可配置，见 `.env.example`。同一实例只有一个接收或处理任务，繁忙时不排队；只能运行一个 Uvicorn worker，同一数据目录有进程锁。

抖音内置路径精确匹配分享视频 ID 后取视频地址，不能把接口推荐作品或 music 字段当结果。当前火山版导出的抖音域短链共用现有解析路径，已有一个真实样本完成下载与转码；旧火山域接口跳转首页的样本仍未恢复。解析失败时可改上传视频。来源与实网证据见 [`平台解析记录`](docs/references/platform-parsing.md)。

可选配置 `DOUYIN_PARSER_URL` / `HUOSHAN_PARSER_URL` 和 `PARSER_API_TOKEN`，连接固定 JSON 解析端点：GET `endpoint?url=<分享链接>`，响应 `{video_url, title?, author?}`；author 可为字符串或包含 name 的对象。令牌只发给配置的固定端点，不跟随跳转。不要把密钥写进 URL；新服务需先核对其响应与原视频来源，不能直接使用 music_url。返回视频地址仍受公网 DNS、逐跳地址与下载容量限制。

处理内容仅在 `DATA_DIR/.soundpick-tasks/` 下保存。成功立即删除视频输入，失败和取消删除部分产物；清理失败会在后续周期重试。结果到期拒绝新访问，已进行的文件发送结束后删除。退出进程停止任务并清理结果；重启不恢复任务。页面刷新不恢复当前结果，请及时下载。

## 本地检查

```bash
make test
make lint
make ui-check
```

本轮检查区分三层：Python 测试验证媒体边界、任务/文件生命周期、认证及 Range 响应；`tests/ui_flow.cjs` 用模拟 DOM 验证前端异步状态、上传取消与系统分享降级；实际 Chromium 检查另行记录，不能由模拟 DOM 推断视觉或真机结果。真实 FFmpeg 测试需运行工具，在缺工具时明确跳过，跳过不代表验收通过。

提供工具后运行 `FFMPEG_BIN=/absolute/ffmpeg FFPROBE_BIN=/absolute/ffprobe make test`，真实生成首尾有标识音的视频，验证第一音轨首尾均保留、MP3 时长、失败清理、取消回收，以及 HTTP 上传/Range/下载/过期链路。

测试客户端当前有一条 Starlette 提示迁移到 `httpx2` 的弃用警告。上述结果是本地检查，不代表 CI、手机真机或容器验收。内置浏览器连接仍有 WSL 路径错误；已找到可运行的缓存 Chromium，可通过独立 Playwright 检查页面。

2026-10-03 接入真实工具后，完整测试为 66 项通过、零跳过；Ruff 和前端流程检查通过。独立 Chromium 使用真实转码的 MP3 验证播放、跳转和中文附件下载，375/768/1440 宽度均无横向溢出。抖音和当前火山版各一个样本通过当前 API 完成实网解析、下载、转码和 Range 下载；原音轨与 MP3 时长分别为约 319.853 秒、12.051 秒，首、中、尾采样声音吻合。逐项证据和剩余验收见 [MVP 验收记录](docs/active/0.1.0/audio-extraction/acceptance.md)。

## 容器运行

```bash
docker compose up --build
```

Compose 读取 `.env`，默认绑定 `127.0.0.1:8000`，使用命名数据卷、非 root 用户、单应用进程和日志轮转。镜像安装 FFmpeg/FFprobe；Compose 的 `environment` 显式指定 `ffmpeg`、`ffprobe`，覆盖 `.env` 的 WSL 专属工具路径。对外部署前需要 HTTPS 入口，并设置 `APP_ENV=production`、`COOKIE_SECURE=true`。通过手机访问非 HTTPS 的局域网地址时，文件分享和剪贴板能力可能不可用，下载入口仍存在。

当前 WSL Docker 客户端启动时报 I/O 错误；Compose 的两项路径覆盖仅通过 YAML 解析与语义差异检查，容器构建与运行尚未验证。MVP 已按用户授权分阶段本地提交，尚未配置远端、推送或部署。
