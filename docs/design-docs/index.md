# 设计决策目录

项目级通用设计决策。跨功能约束记录在本目录，需求范围见 [`../requirements.md`](../requirements.md)。

| id | 主题 | status | owner | 适用范围 | 路径 |
|----|------|--------|-------|----------|------|
| initialization-boundaries | 初始化实现边界与架构候选 | stale | project | 初始化提交快照，当前 MVP 见活跃版本 | [`initialization.md`](./initialization.md) |
| temporary-video-delivery | 双媒体临时交付 | draft | project | 0.1.0 新增 MP4 结果的保留与回收 | [`arch-video-delivery.md`](./arch-video-delivery.md) |

## 状态含义

- **draft**：设计或架构候选尚未通过实现和运行验证；使用时要保留这一不确定性。
- **verified**：设计与当前实现已核对一致。
- **stale**：实现已偏离记录的设计，需要先核对并更新。

架构约束变化先记录决策，再同步 `ARCHITECTURE.md`。需求级方案应放在对应需求文档中。
