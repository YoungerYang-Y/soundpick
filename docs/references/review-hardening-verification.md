# 整仓审查修复验证

日期：2026-10-03。证据来自本地 WSL；不代表 CI、容器、新版手机或平台实网验收。

本轮修复上传响应丢失后的恢复与取消、重复取消期间子进程回收、损坏画面的 MP4 降级、文件响应构造失败及关闭期间下载引用回收、上游畸形集合备用解析、页面恢复时旧响应覆盖，并集中平台登记、交付格式及浏览器模块职责。设计边界见[修复决策](../design-docs/arch-review-hardening.md)。

| 检查 | 结果与边界 |
|---|---|
| `python -m scripts.run_tests --coverage -o addopts= -q -p no:cacheprovider` | 162 项通过、0 跳过；真实 FFmpeg/FFprobe 参与媒体验证。使用项目 `.venv/bin/python` |
| coverage.py | 语句 1100/1173（93.8%），分支 301/352（85.5%）；综合 91.9%，超过 85% 门槛。混合单测与集成测试，不包括 JS 和媒体工具内部 |
| Ruff 检查与格式检查 | 全部通过 |
| 三个浏览器模块语法检查及 `tests/ui_flow.cjs` | 32 项受控时序场景通过，包含响应丢失、取消竞态、页面恢复与分享降级 |
| `python -m scripts.run_tests --browser` | 真实 Chromium 连续 5 轮通过：375px 页面、本地上传、MP3 播放器加载、原字节 MP4 下载、服务器已处理但 202 尚未送达时的取消、临时文件删除 |

真实浏览器测试通过 `SOUNDPICK_NODE_BIN`、`SOUNDPICK_PLAYWRIGHT_MODULE`、`SOUNDPICK_CHROMIUM_BIN` 指定当前环境已有工具。测试启动独立 `127.0.0.1` 临时端口服务，使用合成凭据和临时数据；结束后回收进程、浏览器和数据，不更改现有 8000 端口手机测试服务。

关键回归额外验证：封面图片音频不被当作视频；损坏视频包保留有效 MP3 并拒绝 MP4；MP4 完整画面校验受处理超时约束；重复取消与调用者断开不打断子进程回收；预约重放不重启处理，过期预约在读取上传流前拒绝；同一任务两次交付在关闭后按最后一次引用释放清理；第三平台适配器经过完整路由到真实媒体交付；火山解析器语句与分支覆盖均为 100%。

已知检查边界：Python 测试有一条上游 Starlette TestClient/httpx 弃用告警，未导致失败。文档结构检查的基线与当前版本均有原有的 `audio-extraction/spec.md`、`audio-extraction/design.md` 两处 frontmatter 缺口；忽略目录中的旧快照另会被该工具扫描，本轮没有清理这些历史文件。iPhone/Android 保存路径、HTTPS 系统分享、平台实网及新版容器仍须单独验收。
