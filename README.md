<div align="center">

<img src="app/static/apple-touch-icon.png" alt="音拾图标" width="80" height="80">

# 音拾 · soundpick

**从短视频中拾取完整原声，试听并保存。**

面向本人和家人的手机优先网页，支持抖音、火山分享内容和本地视频。<br>
一次处理一个视频，生成完整原音轨 MP3，并提供可用的原视频 MP4。

![Version](https://img.shields.io/badge/version-0.1.0-2f5543?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.12%20%E2%80%93%203.14-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?style=flat-square&logo=ffmpeg&logoColor=white)
![Status](https://img.shields.io/badge/status-MVP-73521f?style=flat-square)

[快速开始](#快速开始) · [功能](#功能) · [配置](#配置) · [开发](#开发) · [文档](#文档)

</div>

## 预览

<div align="center">
<img src="docs/images/workspace.png" alt="音拾手机布局：分享链接与本地视频输入" width="320">
<img src="docs/images/result.png" alt="音拾处理结果：原声试听、MP3 与原视频 MP4 下载" width="320">
</div>

截图来自本地 Chromium；结果页使用合成视频，展示实际处理流程。

## 功能

| 能力 | 说明 |
| --- | --- |
| 分享内容解析 | 粘贴抖音、火山链接或整段分享文字，按目标视频 ID 获取原视频 |
| 本地视频上传 | 流式接收，显示上传进度，校验大小、时长和音视频流 |
| 完整原音轨 | 提取第一条完整声音轨道，输出 192 kbps MP3，不使用 music/BGM 字段替代 |
| 原视频下载 | MP4 保留原始字节；其他容器尝试无损封装，无法封装时仍保留有效 MP3 |
| 试听与保存 | 原生播放器、Range 试听、中文文件名下载；浏览器支持时提供系统文件分享 |
| 任务控制 | 进度、取消、断线重连，以及当前标签页刷新后的任务恢复 |
| 临时数据 | 共享密码保护访问；文件绑定创建任务的会话，结果到期自动清理 |

不提供个人账户、历史记录、批量任务、裁剪或 AI 去人声。每个实例同时只接收或处理一个视频，繁忙时不排队。

> 平台解析受链接状态和平台访问策略影响。已有抖音和当前火山版各一个实网样本通过，不能据此保证所有链接可用；旧火山域样本仍未恢复。解析失败时可上传本地视频。手机真机保存、系统分享和新版容器验收仍需单独完成，详见[验收记录](#验证状态)。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 服务端 | Python 3.12–3.14 · FastAPI · Uvicorn |
| 页面与交互 | Jinja2 · 原生 JavaScript（ES Modules）· CSS |
| 配置与会话 | pydantic-settings · 签名 Session Cookie · CSRF 校验 |
| 平台请求 | httpx · 公网地址校验 · DNS 固定连接 |
| 媒体处理 | FFmpeg / FFprobe · libmp3lame |
| 工程检查 | pytest · coverage.py · Ruff · Playwright / Chromium |
| 容器 | Docker Compose · 非 root · 只读根文件系统 |

无需数据库、Redis 或前端构建。Node 22 仅用于前端与浏览器检查。系统边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 快速开始

### 前置条件

- Linux / WSL 与 [mise](https://mise.jdx.dev/)，按 `.mise.toml` 准备运行时；项目开发环境固定为 Python 3.14.5。
- 可实际运行的 FFmpeg、FFprobe，且 FFmpeg 包含 `libmp3lame` 编码器。

项目脚本安装 Python 项目依赖，不自动安装 mise、运行时或系统媒体工具。

### 安装与启动

在仓库根目录运行：

```bash
./scripts/setup.sh
.venv/bin/python scripts/init_env.py
make runtime-check
make dev
```

访问 **http://127.0.0.1:8000**，使用项目根目录 `.env` 中的 `APP_PASSWORD` 登录。

初始化脚本生成随机访问密码和会话密钥，不覆盖已有 `.env`。请只在本机查看密码，不要将 `.env` 提交到仓库或贴进日志。

如果系统工具不在 PATH 中，在 `.env` 配置绝对路径后重启：

```dotenv
FFMPEG_BIN=/absolute/path/to/ffmpeg
FFPROBE_BIN=/absolute/path/to/ffprobe
```

启动时检查工具实际运行能力和 MP3 编码器。工具未就绪时仍可访问页面，创建任务返回 503；`make runtime-check` 提供本地诊断。

### 使用

1. 粘贴分享链接或整段分享文字，或切换到「本地视频」选择文件。
2. 点击「开始提取」，等待处理完成；需要时可取消。
3. 试听完整原声，下载 MP3 或可用的原视频 MP4。

浏览器允许存储时，同一标签页刷新会重新查询当前任务；不会重新上传视频，也不保存处理历史。关闭标签页、退出登录或结果到期后，入口不保证保留，请及时下载。

## 容器运行

先用初始化脚本创建 `.env`，再运行：

```bash
docker compose up --build -d
docker compose logs -f app
```

默认访问 http://127.0.0.1:8000。镜像内置 FFmpeg / FFprobe，Compose 覆盖本机媒体工具路径，使用命名数据卷、单应用进程和日志轮转。

对外部署需要 HTTPS 入口，并在 `.env` 设置：

```dotenv
APP_ENV=production
COOKIE_SECURE=true
```

只能运行一个 Uvicorn worker，同一数据目录受进程锁保护。默认仅绑定回环地址；手机或其他设备访问还需要自行配置网络入口。非 HTTPS 局域网地址可能无法使用文件分享和剪贴板，下载入口仍可使用。

容器环境的历史验证及其版本边界见[容器验证记录](docs/references/container-verification.md)。

## 配置

完整变量见 [.env.example](.env.example)。环境变量优先于 `.env`。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `APP_PASSWORD` | 初始化生成 | 共享访问密码，12–128 字符 |
| `SESSION_SECRET` | 初始化生成 | 会话签名密钥，至少 32 字符 |
| `APP_ENV` / `COOKIE_SECURE` | 初始化为 `local` / `false` | 生产环境必须使用 `production` / `true` 与 HTTPS |
| `DATA_DIR` | `data` | 媒体工作目录 |
| `MAX_MEDIA_BYTES` | `209715200` | 单视频 200 MiB |
| `MAX_DURATION_SECONDS` | `1800` | 最长 30 分钟 |
| `UPLOAD_TIMEOUT_SECONDS` | `300` | 上传时限 5 分钟 |
| `TASK_TIMEOUT_SECONDS` | `600` | 处理时限 10 分钟 |
| `RESULT_TTL_SECONDS` | `3600` | 结果保留 1 小时 |
| `MAX_RETAINED_TASKS` | `32` | 最多保留的任务数，含失败和取消 |
| `APP_PORT` | `8000` | Compose 的本机映射端口；`make dev` 固定为 8000 |

媒体保存在 `DATA_DIR/.soundpick-tasks/`。成功后仅保留交付用的 MP3 和可用 MP4；失败、取消和到期后清理。进程重启不恢复任务，启动时清理本应用残留；已开始的文件发送结束后再回收对应目录。

### 可选解析服务

设置 `DOUYIN_PARSER_URL`、`HUOSHAN_PARSER_URL` 和可选 `PARSER_API_TOKEN`，可接入固定 JSON 解析端点：

```http
GET <endpoint>?url=<分享链接>
```

```json
{"video_url": "https://example.com/original-video.mp4", "title": "视频标题", "author": "作者"}
```

`title`、`author` 可省略；`author` 也可为包含 `name` 的对象。令牌只发送给固定端点，不跟随跳转，不应写进 URL。返回的视频地址仍受公网地址、跳转和容量限制；接入前须确认其返回目标原视频，不能使用背景音乐地址冒充。

## 开发

```bash
make test           # Python 回归，要求真实媒体工具，缺工具直接失败
make coverage       # Python 语句与分支覆盖，综合门槛 85%
make lint           # Ruff 检查与格式检查
make ui-check       # JavaScript 语法与受控异步交互回归
make browser-check  # 独立 HTTP 服务 + Chromium + 真实媒体，默认重复 5 轮
```

`make test` 和 `make coverage` 自动读取媒体工具配置。局部检查可运行 `.venv/bin/python -m pytest tests/test_auth.py`；直接 pytest 在缺工具时可能跳过真实媒体测试，跳过不能当作完整验收。

浏览器回归需要 Node 22、Playwright 模块和 Chromium。使用现有工具时可指定：

```bash
SOUNDPICK_PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
SOUNDPICK_CHROMIUM_BIN=/absolute/path/to/chromium \
mise exec -- make browser-check
```

必要时设置 `SOUNDPICK_NODE_BIN`；`SOUNDPICK_BROWSER_REPEAT` 必须为大于 1 的整数。浏览器检查启动独立回环服务和临时数据，不使用开发服务。覆盖率产物位于忽略目录 `work/coverage/`，不包含 JavaScript 或媒体子进程内部。

依赖锁定在 `requirements.lock` 和 `requirements-dev.lock`。修改 `pyproject.toml` 后须同步重新解析锁文件。

### 目录结构

```text
app/
├── main.py          # 应用工厂、页面与 HTTP 接口
├── auth.py          # 会话、CSRF 与登录限速
├── config.py        # 配置与校验
├── middleware.py    # 请求体与上传容量边界
├── resolver/        # 平台识别与原视频解析
├── services/        # 任务、媒体、网络与文件交付
├── templates/       # Jinja2 页面
└── static/          # 浏览器模块、样式与图标
scripts/             # 环境初始化与检查入口
tests/               # Python、交互与浏览器回归
docs/                # 需求、设计与验收证据
```

### 验证状态

本地测试、真实媒体、平台实网、容器和手机验收分别记录，不能互相替代：

- [本轮优化验证](docs/references/system-optimization-verification.md)：Python、覆盖率、浏览器刷新恢复与异步交互检查；[前轮工程回归](docs/references/review-hardening-verification.md)保留历史证据。
- [原音轨验收](docs/active/0.1.0/audio-extraction/acceptance.md)与 [MP4 验收](docs/active/0.1.0/video-download/acceptance.md)：媒体完整性、下载和生命周期。
- [平台解析记录](docs/references/platform-parsing.md)：已验证样本与平台限制。
- [手机验收](docs/active/0.1.0/audio-extraction/mobile-acceptance.md)与[容器验证](docs/references/container-verification.md)：设备和运行环境的待验收项。

## 文档

- [已确认需求](docs/requirements.md)：用途、范围与产品约束。
- [架构](ARCHITECTURE.md) · [安全策略](docs/SECURITY.md) · [可靠性标准](docs/RELIABILITY.md)。
- [活跃版本](docs/active/index.md) · [设计决策](docs/design-docs/index.md) · [技术债](docs/active/tech-debt-tracker.md)。
- [贡献与协作约定](AGENTS.md)：修改范围、工程命令与验收边界。
