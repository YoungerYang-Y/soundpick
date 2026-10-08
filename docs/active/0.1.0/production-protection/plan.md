# 生产额度与防刷 Implementation Plan

**Version:** 0.1.0

## Goal / Context / Key Decisions

落实 [Spec](spec.md) 与 [Design](design.md)。保持单实例、单进程锁、单活动任务、会话 owner、流式上传大小边界和代理信任约束。Redis 为可选持久主存储；`REDIS_URL` 留空使用纯内存。Redis 故障时自动降级到限频最多4096个桶，额度记录另设4096条上限 的进程内状态，服务继续运行；故障期间额度为软保护，重启可能丢失内存额度并超过每日10首。

配置默认值为 `REDIS_KEY_PREFIX=soundpick:{protection}`、`REDIS_TIMEOUT_SECONDS=0.1`、`REDIS_RETRY_SECONDS=30`。Redis client 使用严格连接与 socket timeout，不自动重试。保留本进程已知额度及 rate shadow；恢复通过 Lua 原子合并成功 ID 与当前 pending；限频不回灌，但有效内存窗口继续限流。旧 SQLite 文件不迁移或自动删除，Redis 持久化由部署者现有的 AOF/RDB 配置负责。

## Behavior Contract

维持既定全站每日成功10首（北京时区）及登录、提取、上传、轮询、下载的 IP/全站频率桶和 429 契约。pending 跨日占额，成功归完成日；失败、取消、预约超时和进程重启中断释放预约。直接上传仍同时消耗提取及上传额度；失败请求仍计频；超限检查仍在请求体读取前。Redis恢复若合并后超额，则阻止新任务。各接口的请求体大小、上传与请求体超时、会话隔离及可信代理配置保持现有边界。

## Task Dependencies / Tasks

| Task | Depends on | 文件范围与目的 |
| --- | --- | --- |
| T1 Redis 保护存储 | 无 | Redis key/prefix、Lua 额度合并、滑动频率窗口；严格 socket/connect timeout；无自动重试；超时、错误和熔断处理 |
| T2 内存保护与切换 | T1 | 有界限频桶（4096）及额度记录（4096）保护；额度与 rate shadow；故障冷却探测、恢复合并、恢复超额阻止创建；不淘汰 key、不回灌限频 |
| T3 生命周期及 HTTP 集成 | T1/T2 | 额度预占/成功/释放与任务生命周期；请求早期限频、429响应及原有限额保持；请求大小/timeout、session owner、代理 trust 保持 |
| T4 部署文档与验收 | T1–T3 | 配置说明、Redis AOF/RDB 责任、prefix 隔离、软额度重启边界、旧 SQLite 文件处理；运行下列待验收清单并记录实际证据 |

## Acceptance Checklist

以下为本地实现验收结果；不代表已部署或用户生产 Redis 已核验。

- [x] `REDIS_URL` 留空使用纯内存；Redis 正常时使用指定 prefix；共享 Redis 中不同 prefix 数据隔离。
- [x] Redis connect/socket timeout 均受0.1秒限制，client 不自动重试；故障冷却30秒后才探测恢复。
- [x] Redis 故障不停止服务；切换时不清零已知成功额度、pending 或 rate shadow。
- [x] 内存限频最多4096个桶，额度记录另设4096条上限；达到上限不淘汰已有 key，也不放行无法跟踪的新 key。
- [x] 每日额度和所有既有限频 IP/全站窗口在 Redis、内存及切换路径保持相同参数；失败请求计频，超限请求体读取前拒绝。
- [x] 恢复时 Lua 原子合并成功任务 ID 与当前 pending；幂等处理重复 ID；合并后超额会拒绝新任务。
- [x] 故障期 rate 不回灌 Redis；恢复后有效的本地窗口继续限流至窗口过期。
- [x] 应用重启后正常 Redis 中的额度/限频保留；故障期重启可丢失内存额度并超10首，恢复合并后拦截新任务。生产 Redis 自身重启的持久性取决于 AOF/RDB，未核验其配置。
- [x] 原有会话隔离、上传大小与超时、普通请求体超时、可信代理头边界及任务失败清理行为保持。
- [x] README、ARCHITECTURE、需求、安全、可靠性和本功能设计文档内容一致；不连接用户生产 Redis、不部署、不自动删除旧 SQLite 文件。

## Validation Record

| 检查 | 实际结果与证据 |
| --- | --- |
| 单元 / 集成回归 | 2026-10-08，`python -m scripts.run_tests --require-redis`：270 passed，0 skipped；包含真实 FFmpeg/FFprobe。测试在沙箱外执行本机隔离端口，不访问生产。 |
| Ruff / 前端检查 | Ruff check、48个Python文件格式检查、`git diff --check`、文档 lint 通过；`make ui-check` 47个浏览器模块场景通过；`make runtime-check` 媒体工具与MP3编码器可用。 |
| Redis 故障与恢复验收 | 临时 Redis 7.4.2 + redis-py 8.1.0 wheel：真实断连/恢复、写入后丢响应、quota key 丢失、坏状态、原子最后一额度、Lua各组429、多组拒绝不消费、prefix隔离、超时/零重试配置均通过。客户端从临时wheel载入，未安装到共享venv。 |
| 容器与生产 Redis | 未执行；不连接用户生产 Redis、不部署 |


独立代码审查六维评分均为4；已修复 Redis key 丢失导致 shadow 覆盖的问题，并补齐真实 Lua 拒绝分支和严格验收缺依赖的检查。0.1秒是单连接/socket操作超时，DNS和多地址尝试可能累计耗时，已在运行文档说明。

本轮审查反馈已修复：需求文档明确限频桶与额度记录各自4096的上限；Redis quota hash 在批量读取前检查分配占用。新增8MiB字段/值用例先确认旧实现会执行批量读取，修复后在读取前拒绝并降级；合法近1MiB账本及hash结构开销用例通过。最终严格回归270 passed、0 skipped，Ruff和文档检查通过。若使用自定义Redis ACL，需允许 MEMORY USAGE；无权限时自动内存降级。
