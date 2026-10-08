---
id: production-protection
status: in-progress
owner: project
created: 2026-10-08
version: 0.1.0
updated: 2026-10-08
---

# 生产额度与防刷 Spec

**Version:** 0.1.0

## Behavior: 全站每日额度

### Scenario: 正常提取
Given 北京当天成功数为 9、没有活动任务；When 一个登录会话创建任务并成功完成；Then 当天成功数为 10，MP3 与同任务 MP4 合计一首。

### Scenario: 满额与跨零点
Given 北京当天已有 10 首成功结果；When 另一浏览器或重启后的用户创建任务；Then 返回 429 和距北京次日零点的等待秒数。Given 次日零点到达；When 再创建任务；Then 新日额度可用。跨零点成功归入完成日，旧日活动任务继续占用新日可用额度。

### Scenario: 失败与并发
Given 当天只有一个剩余额度；When 请求并发创建任务；Then 最多一个预占成功。When 该任务失败、取消、预约过期或重启中断；Then 释放预占且成功数不增加。重复确认成功或重复取消不重复计数。

## Behavior: 接口防刷

### Scenario: 正常请求
Given 单 IP 与全站请求次数均低于限额；When 登录、提取、上传、轮询或下载；Then 保留原接口行为，各类限额独立。

### Scenario: 超限与恢复
Given 某类请求达到限额；When 再次请求；Then 返回 429、Retry-After 等待秒数；JSON 接口同时返回 code 和 retry_after。When 对应窗口到期；Then 允许请求。重启、换浏览器和改伪造转发头不清空限频。

### Scenario: 失败刷接口
Given 请求包含错误密码、无效 CSRF、错误链接或不存在的任务；When 持续请求；Then 这些请求仍消耗对应频率预算；提取尝试另受全站 30 次/小时限制。超限上传在读取任何视频块前拒绝。

## Behavior: 输入与会话边界

### Scenario: 合法上传与文件
Given 创建会话已登录且 CSRF 有效；When 上传限额内的有效视频并提取；Then 可访问同任务的音频与视频。

### Scenario: 大小边界
Given 上传实际字节超过 MAX_MEDIA_BYTES；When 缺少或伪造 Content-Length 的上传到达；Then 返回 413、停止接收并释放任务预占。声明长度超过上限的请求在读取前返回 413。

### Scenario: 跨会话与代理伪造
Given 任务属于会话 A；When 会话 B 轮询、上传、取消或下载（含 HEAD/Range）；Then 返回 404 且不读取上传块或交付文件。When 直连客户端伪造 X-Forwarded-For；Then 不改变限频身份。

## Constraints

- 用户指定：全站每天成功最多 10 首，时区 UTC+8，单实例单进程单活动任务。
- 默认防刷参数：登录 IP 10/60秒及30/900秒，全站60/60秒及300/900秒；提取 IP6/60秒，全站20/60秒及30/3600秒；上传 IP6/60秒、全站20/60秒；轮询 IP120/60秒、全站600/60秒；下载 IP30/60秒、全站120/60秒。
- `REDIS_URL` 可选；未配置时纯内存防护。Redis 正常且部署配置 AOF/RDB 时，额度与限频跨重启保留。Redis 故障自动降级到内存，服务不停止；故障期间是软额度，进程重启可能丢失额度状态并超过每日 10 首。
- Redis 故障期间保留本进程已知成功额度和 pending，不清零；恢复时用 Lua 原子合并成功任务 ID 与当前 pending，若合并后超额则拒绝新任务。故障期限频不回灌 Redis，内存窗口继续限流。
- Redis 连接与套接字超时默认 0.1 秒，不自动重试；故障探测冷却默认 30 秒。`REDIS_KEY_PREFIX` 默认 `soundpick:{protection}`，共享 Redis 的部署使用不同 prefix，同一 prefix 不得运行多个应用实例。
- 内存保护限频最多跟踪 4096 个桶，额度记录另设 4096 条上限，不淘汰已有 key；容量耗尽时拒绝无法跟踪的新 key。Redis 持久化由部署者配置；旧 SQLite 文件不迁移或自动删除。
