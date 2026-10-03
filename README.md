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
- 同次处理提供原视频 MP4 下载；已是 MP4 保留原文件字节，其他容器尝试无损封装，保留完整画面和声音。无法封装时单独提示，已成功的 MP3 仍可下载；不保证源编码兼容所有手机播放器。
- 内存任务进度、取消、超时、会话所属结果；成功保留 MP3 与可用 MP4，失败/取消删除全部媒体，到期清理，重启清理本应用残留。
- 原生播放器、Range 试听、标准 UTF-8 附件下载；安全页面且浏览器支持时可准备文件，再点击系统分享。
- 手机保存指引、上传进度、断网重连与错误重试；不提供账户、处理历史或批量任务。

产品范围见 [`docs/requirements.md`](docs/requirements.md)，实施规格、设计和计划见 [`MVP 目录`](docs/active/0.1.0/audio-extraction/plan.md)。平台可解析性受链接状态与平台访问策略影响，不能保证所有分享链接成功。

## 配置与平台解析

工程默认：单视频 200 MiB、30 分钟；上传超时 300 秒、处理超时 600 秒，结果保留 1 小时，最多保留 32 个任务（含失败和取消）。这些值均可配置，见 `.env.example`。同一实例只有一个接收或处理任务，繁忙时不排队；只能运行一个 Uvicorn worker，同一数据目录有进程锁。

抖音内置路径精确匹配分享视频 ID 后取视频地址，不能把接口推荐作品或 music 字段当结果。当前火山版导出的抖音域短链共用现有解析路径，已有一个真实样本完成下载与转码；旧火山域接口跳转首页的样本仍未恢复。解析失败时可改上传视频。来源与实网证据见 [`平台解析记录`](docs/references/platform-parsing.md)。

可选配置 `DOUYIN_PARSER_URL` / `HUOSHAN_PARSER_URL` 和 `PARSER_API_TOKEN`，连接固定 JSON 解析端点：GET `endpoint?url=<分享链接>`，响应 `{video_url, title?, author?}`；author 可为字符串或包含 name 的对象。令牌只发给配置的固定端点，不跟随跳转。不要把密钥写进 URL；新服务需先核对其响应与原视频来源，不能直接使用 music_url。返回视频地址仍受公网 DNS、逐跳地址与下载容量限制。

处理内容仅在 `DATA_DIR/.soundpick-tasks/` 下保存。成功后保留交付用的 MP3 与可用 MP4，移除其他输入或部分产物；失败和取消删除全部媒体，清理失败会在后续周期重试。两种结果到期拒绝新访问，已进行的任一文件发送结束后删除。退出进程停止任务并清理结果；重启不恢复任务。页面刷新不恢复当前结果，请及时下载。原视频 MP4 新增需求与接口见 [原视频下载设计](docs/active/0.1.0/video-download/design.md)。

## 本地检查

```bash
make test
make coverage
make lint
make ui-check
```

本轮检查区分三层：Python 测试验证媒体边界、任务/文件生命周期、认证及 Range 响应；`tests/ui_flow.cjs` 用模拟 DOM 验证前端异步状态、上传取消与系统分享降级；真实 Chromium 回归由 `make browser-check` 单独运行，不能由模拟 DOM 推断视觉或真机结果。标准 `make test` 与 `make coverage` 从环境或 `.env` 读取两项媒体工具配置，缺工具直接失败。直接运行pytest适用于局部测试，缺工具仍会明确跳过，跳过不代表验收通过。

提供工具后运行 `FFMPEG_BIN=/absolute/ffmpeg FFPROBE_BIN=/absolute/ffprobe make test`，真实生成首尾有标识音的视频，验证第一音轨首尾均保留、MP3 时长、失败清理、取消回收，以及 HTTP 上传/Range/下载/过期链路。

测试客户端当前有一条 Starlette 提示迁移到 `httpx2` 的弃用警告。上述结果是本地检查，不代表 CI、手机真机或容器验收。内置浏览器连接仍有 WSL 路径错误；已找到可运行的缓存 Chromium，可通过独立 Playwright 检查页面。

2026-10-03 接入真实工具并补齐 TCP 取消验收后，完整测试为 67 项通过、零跳过；Ruff 和前端流程检查通过。真实双音轨视频已核对第一音轨首、中、尾的标识音；通过独立本机 Uvicorn 和实际 TCP 请求，验证取消时真正 FFmpeg 进程退出、输入及部分产物清理，以及重新提交后生成完整 MP3。独立 Chromium 使用真实转码的 MP3 验证播放、跳转和中文附件下载，375/768/1440 宽度均无横向溢出。抖音和当前火山版各一个样本通过当前 API 完成实网解析、下载、转码和 Range 下载；原音轨与 MP3 时长分别为约 319.853 秒、12.051 秒，首、中、尾采样声音吻合。逐项证据和剩余验收见 [MVP 验收记录](docs/active/0.1.0/audio-extraction/acceptance.md)。

## 容器运行

```bash
docker compose up --build
```

Compose 读取 `.env`，默认绑定 `127.0.0.1:8000`，使用命名数据卷、非 root 用户、单应用进程和日志轮转。镜像安装 FFmpeg/FFprobe；Compose 的 `environment` 显式指定 `ffmpeg`、`ffprobe`，覆盖 `.env` 的 WSL 专属工具路径。对外部署前需要 HTTPS 入口，并设置 `APP_ENV=production`、`COOKIE_SECURE=true`。通过手机访问非 HTTPS 的局域网地址时，文件分享和剪贴板能力可能不可用，下载入口仍存在。

2026-10-03 在新增 MP4 需求前，已通过 Windows Docker CLI 连接 Docker Desktop 的 Linux 引擎，成功构建并实际运行当时的镜像；非 root、只读根文件系统、本地视频及火山分享的真实转码、附件、Range 和到期清理通过。独立验收容器与测试数据卷已移除，镜像保留，详情见 [容器验收记录](docs/references/container-verification.md)。旧镜像尚不包含本轮 MP4 功能，该证据不能代替新增功能的容器验收。WSL 默认 Unix socket 仍拒绝连接，未修改 Desktop 集成设置；从当前 WSL 调用可用的 Windows CLI 可使用 `"/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" compose up --build`。MVP 已按用户授权分阶段本地提交，尚未配置远端、推送或部署。

## 原视频 MP4 下载补充

2026-10-03 新增功能的本地完整回归为 92 项通过、零跳过，Ruff 与前端流程检查通过；真实 MP4 字节保留、MOV 全部音视频流无损封装及标题/旋转/语言/default 属性、双文件授权和共享到期保护、视频阶段取消与超时均已验证。Chromium 实际下载中文名称的 MP3 和 MP4，后者与原文件字节一致；当前火山分享实网样本返回 2,986,909 字节 MP4，与源视频 SHA-256 一致。证据和尚未验证的手机保存/播放、新增功能容器运行见 [MP4 验收记录](docs/active/0.1.0/video-download/acceptance.md)。

## 审查修复与可重复回归

文件输入先预约已知任务ID再上传，响应丢失后可重新连接或取消原任务；取消必须获得服务端确认。平台由统一catalog登记，前端分为任务控制、呈现和分享三个原生模块。MP4增加完整画面解码校验，失败时保留有效MP3；详见[修复决策](docs/design-docs/arch-review-hardening.md)。

`make coverage` 使用 [coverage.py](https://coverage.readthedocs.io/en/latest/) 测量Python语句和分支，综合门槛85%，结果保存在忽略目录 `work/coverage/`。它混合单测与集成测试，不包括JavaScript或媒体子进程内部。

真实浏览器回归需要Node 22、可用Playwright模块及其Chromium，不参与产品构建。已安装时可直接 `mise exec -- make browser-check`；工具安装在其他位置时设置 `SOUNDPICK_PLAYWRIGHT_MODULE` 为模块路径、`SOUNDPICK_CHROMIUM_BIN` 为Chromium可执行路径，必要时通过 `SOUNDPICK_NODE_BIN` 指定Node。默认重复5轮，可用 `SOUNDPICK_BROWSER_REPEAT` 指定大于1的次数；测试自行启动独立回环服务并清理临时数据，不访问当前开发服务。

当前修复的测试结果见[工程验证记录](docs/references/review-hardening-verification.md)。真实手机保存、系统分享、平台实网与新容器状态仍按各自验收记录判断。
