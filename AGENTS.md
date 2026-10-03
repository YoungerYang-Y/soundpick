# AGENTS.md

本文件是智能体的唯一入口，保持为"地图而不是手册"。

## 项目概述

音拾（soundpick）是供本人和家人使用的手机优先网页，用于从抖音、火山的分享内容或本地视频中取得完整原音轨并保存为 MP3。项目采用 Python 3.12–3.14、FastAPI、Jinja2 与原生 JavaScript；已实现 MVP 主链路，真实媒体与平台验收仍需核对，不能以缺工具时的跳过测试宣称完整可用。

## 全局规范

1. 默认简体中文；区分本地检查、CI、容器运行和真机验证。本文件提供项目约定，不改变用户授权或上级指令。
2. 只改当前任务必要范围，保留已有修改。commit、push、merge、发布和部署均需用户明确要求；初始化仓库不授权这些操作。
3. 用户要求提交时，使用 Conventional Commits，message 中文：`<type>(<scope>): <中文描述>`。
4. 需求与实现不一致时，先明确差异；不得仅根据代码把未完成的需求改写为已完成。
5. Python 使用项目 `.mise.toml` 与 `.venv`；Node 22 用于文档工具和前端脚本检查，无前端构建步骤。
6. 仅单进程、单活动任务；任务和文件绑定创建浏览器会话。只从匹配目标 ID 的原视频提取音轨，不能用 music/BGM 代替。媒体工具须真实可运行，平台可解析性单独实网验证。

## 导航

### A. 长期约束（授权变更后同步相关设计）

- 系统边界与依赖方向：[`ARCHITECTURE.md`](./ARCHITECTURE.md)
- 工程信条：[`docs/design-docs/core-beliefs.md`](./docs/design-docs/core-beliefs.md)
- 业务领域划分：[`docs/DOMAINS.md`](./docs/DOMAINS.md)
- 安全策略：[`docs/SECURITY.md`](./docs/SECURITY.md)
- 可靠性标准：[`docs/RELIABILITY.md`](./docs/RELIABILITY.md)

### B. 流转文档

- 活跃版本：[`docs/active/index.md`](./docs/active/index.md)
- 版本归档：[`docs/archive/index.md`](./docs/archive/index.md)
- 技术债：[`docs/active/tech-debt-tracker.md`](./docs/active/tech-debt-tracker.md)
- 设计决策：[`docs/design-docs/index.md`](./docs/design-docs/index.md)
- 已确认需求：[`docs/requirements.md`](./docs/requirements.md)
- 初始化设计与实现边界：[`docs/design-docs/initialization.md`](./docs/design-docs/initialization.md)
- MVP 行为、设计与实施：[`docs/active/0.1.0/audio-extraction/plan.md`](./docs/active/0.1.0/audio-extraction/plan.md)

### C. 参考与产物

- 自动生成的文档（禁止手改）：[`docs/generated/`](./docs/generated/)
- 产品思维：[`docs/PRODUCT_SENSE.md`](./docs/PRODUCT_SENSE.md)
- 初始化期间不生成源码参考文档：[`docs/generated/index.md`](./docs/generated/index.md)

## 代码导航

| 改什么 | 去哪里 |
|--------|--------|
| 页面、样式与浏览器交互 | `app/templates/`、`app/static/` |
| 登录、配置与 Web 路由 | `app/main.py`、`app/auth.py`、`app/config.py`、`app/middleware.py` |
| 平台解析器契约 | `app/resolver/` |
| 媒体任务与转换 | `app/services/` |
| 依赖与本地环境 | `pyproject.toml`、`requirements*.lock`、`scripts/` |

## 开发命令

```bash
./scripts/setup.sh
.venv/bin/python scripts/init_env.py
.venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
make ui-check
make runtime-check
```

首次启动前运行初始化脚本。登录密码保存在项目根目录 `.env` 的 `APP_PASSWORD`；请自行在本机查看，不要粘贴到聊天或日志。环境准备和运行方式见 [README.md](./README.md)。
