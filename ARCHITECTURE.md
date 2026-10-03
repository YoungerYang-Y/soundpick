# ARCHITECTURE.md

本文记录项目当前的系统边界、分层和依赖方向。需要改变这些长期约束时，先在 `docs/design-docs/` 记录架构决策。

## 系统概述

音拾为本人和家人提供单视频处理网页。输入为抖音或火山的分享链接、包含分享链接的整段文字，或用户上传的本地视频；输出是原视频完整音轨的 MP3。产品不做 AI 去人声、裁剪或批量处理，也不提供个人账号和历史记录。

应用使用 FastAPI 提供服务端渲染页面和 JSON 接口，Jinja2 渲染 HTML，原生 JavaScript 完成浏览器交互。配置由 `pydantic-settings` 从环境加载；认证由共享密码和签名 Session Cookie 提供。项目不依赖数据库、Redis 或前端构建步骤。部署目标是 Python 3.14 slim 容器，容器安装 FFmpeg/FFprobe 并以非 root 用户运行。

MVP 使用单实例、单进程的家庭服务：内存任务状态，进程重启后不恢复任务；同一数据目录有进程锁，同一时刻只有一个接收或处理任务。媒体链路已实现，本地真实 FFmpeg 测试、一个抖音原音轨样本和一个当前火山版共用域分享样本已通过；手机真机验收仍待完成，旧火山域样本未恢复。当前证据见 [README.md](./README.md) 和 [平台解析记录](./docs/references/platform-parsing.md)。

## 项目结构

```
soundpick/
├── app/
│   ├── main.py                 # FastAPI 应用工厂与当前 HTTP 路由
│   ├── auth.py                 # 共享密码、会话校验、CSRF 与登录限速
│   ├── config.py               # 环境配置与安全校验
│   ├── middleware.py           # 小请求上限与上传流实际字节边界
│   ├── resolver/               # 分享识别、平台适配与可配置 JSON provider
│   ├── services/               # tasks、media、network 和公开错误
│   ├── templates/              # Jinja2 页面
│   └── static/                 # 原生浏览器脚本和样式
├── scripts/                    # 本地环境准备与配置初始化
├── docs/                       # 需求、架构与维护文档
├── pyproject.toml              # Python 项目及依赖定义
├── requirements.lock           # 运行依赖锁定
└── requirements-dev.lock       # 开发依赖锁定
```

## 分层模型

```mermaid
flowchart TD
  Browser[浏览器] --> Router[页面与 API 路由]
  Router --> Auth[认证与配置]
  Router --> Service[媒体任务服务]
  Service --> Resolver[平台解析器]
  Service --> Media[FFprobe / FFmpeg]
  Service --> TempFiles[临时文件]
```

**依赖规则：**
- 路由层负责 HTTP 校验、认证和响应；业务处理放在服务层。
- 服务层依赖解析器契约和媒体处理能力，不依赖具体页面实现。
- 平台差异封装在 `app/resolver/`，不得让平台解析细节散落在路由或模板中。
- 上传、下载、任务状态与临时目录均由服务层定义边界。路由只读取受限流，不直接转码。
- 安全网络层将核实的公网 DNS 地址固定至连接，保持原主机的 TLS 验证；媒体子进程仅处理本地文件。
- 文件响应保护活动发送引用；到期拒绝新请求，最后一次发送释放后清理。输入、失败产物和启动残留按生命周期回收。

## 技术栈

| 层级 | 技术 | 版本/备注 |
|------|------|-----------|
| 运行时 | Python | `>=3.12,<3.15`；项目 mise 配置为 3.14.5 |
| Web | FastAPI、Uvicorn | 服务端路由与 ASGI 服务 |
| 页面 | Jinja2、原生 JavaScript | 不需要 Node 构建 |
| 配置与会话 | pydantic-settings、itsdangerous | 环境配置、签名 Session Cookie |
| HTTP 客户端 | httpx | 平台请求适配 |
| 媒体工具 | FFmpeg、FFprobe | 要求 libmp3lame；本地路径可配置，容器安装系统工具 |
| 部署 | Docker `python:3.14-slim` | 最终容器以非 root 身份运行 |

## 模块职责

| 模块 | 职责 | 依赖 |
|------|------|------|
| `app/main.py` | 应用工厂、路由装配、HTTP 输入输出 | 配置、认证、静态与模板资源 |
| `app/auth.py`、`app/middleware.py` | 共享密码会话、CSRF、登录限速和请求体边界 | 配置、Starlette |
| `app/config.py` | 加载和校验运行配置 | pydantic-settings |
| `app/resolver/` | 分享识别、平台原视频地址适配，排除错误 ID/BGM | 安全网络、resolver 基础契约 |
| `app/services/tasks.py` | 单活动任务、会话所属结果、容量、取消与清理 | media、resolver、network、临时文件系统 |
| `app/services/media.py` | 视频/声音流、时长与完整 MP3 校验，子进程回收 | FFmpeg/FFprobe、配置 |
| `app/services/network.py` | 公网 DNS 固定、逐跳校验、有限响应与流式下载 | httpx |
| `app/templates/`、`app/static/` | 手机优先页面与交互 | 页面/API 契约 |
| `scripts/` | 准备本地环境、安全初始化 `.env` | mise、Python、pip |

## 关键架构决策

初始化实现边界和仍待验证的部署候选见 [`docs/design-docs/initialization.md`](./docs/design-docs/initialization.md)。

当前 MVP 的精确接口、容量默认、错误和验收见 [`MVP 技术设计`](./docs/active/0.1.0/audio-extraction/design.md)。初始化文档保留最初提交快照，不代表当前功能范围。
