# 设计决策目录

项目级通用设计决策。跨功能约束记录在本目录，需求范围见 [`../requirements.md`](../requirements.md)。

| id | 主题 | status | owner | 适用范围 | 路径 |
|----|------|--------|-------|----------|------|
| review-hardening | 任务确认、媒体有效性与可测试性 | verified | project | 0.1.0 审查修复与回归约束 | [`arch-review-hardening.md`](./arch-review-hardening.md) |
| runtime-recovery | 运行能力、网络回退与当前任务恢复 | verified | project | 0.1.0 系统性优化，本地实现与回归已核对 | [`arch-runtime-recovery.md`](./arch-runtime-recovery.md) |

## 状态含义

- **draft**：设计或架构候选尚未通过实现和运行验证；使用时要保留这一不确定性。
- **verified**：设计与当前实现已核对一致。
- **stale**：实现已偏离记录的设计，需要先核对并更新。

架构约束变化先记录决策，再同步 `ARCHITECTURE.md`。需求级方案应放在对应需求文档中。
