# 活跃版本索引

整体产品范围记录在 [`../requirements.md`](../requirements.md)，系统边界见 [架构](../../ARCHITECTURE.md)。MVP 按 0.1.0 实施。

| 版本号 | 分支 | 状态 | 开始日期 | 路径 |
|--------|------|------|----------|------|
| 0.1.0 | main（本地实施） | 实施中 | 2026-10-02 | [音频提取 MVP](./0.1.0/audio-extraction/plan.md) |

0.1.0 生产额度与请求防护的规格、设计和实施计划见[生产额度与防刷](./0.1.0/production-protection/plan.md)；本地270项严格回归和47个前端场景通过，未连接生产 Redis、未部署。长期约束记录为 [生产额度与请求防护决策](../design-docs/arch-production-protection.md)（verified）。

当前实现与逐项验收证据见 [MVP 验收记录](./0.1.0/audio-extraction/acceptance.md)：

- 已通过：本地真实转码、抖音与当前火山版各一个实网样本、实际容器构建和媒体运行。
- 未完成：旧火山域样本未恢复，手机真机保存及 HTTPS 分享验证尚未完成。

2026-10-03 新增同次处理的原视频 MP4 下载，范围、方案与实施拆分见 [原视频下载](./0.1.0/video-download/plan.md)：

- 已通过：本地 92 项测试、真实 MOV 无损封装、Chromium 双文件下载、当前火山分享 MP4 交付；详见 [新增验收](./0.1.0/video-download/acceptance.md)。
- 验收边界：旧容器和真机证据不自动覆盖该新增能力。
