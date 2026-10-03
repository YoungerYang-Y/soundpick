# 原视频 MP4 下载实施计划

版本：0.1.0；按同目录 spec.md / design.md 的行为与接口执行。

## Goal / Context

- 目标：一次处理即可下载 MP3 和完整原视频 MP4。
- 基线：现有任务成功后删除 input。
- 变更面：媒体准备、任务生命周期、文件端点和页面同步改变；音频原契约保持兼容。

## Key Decisions / Behavior Contract

- 已是实际 MP4 时原字节保留；其他容器仅无损封装，保留全部视频/声音流与 MP4 支持的元数据，不重新编码。
- `prepare_video(source:Path,target:Path)->float`；ready TaskView 新增 video_download_url/video_filename/video_message。
- `/files/{id}` 新增 kind=audio|video，默认 audio；video 强制 attachment、video/mp4；不可用409，非法 kind422。
- MP4 准备失败保留 MP3 并明确提示；取消或整体超时删除全部。一个视频的两种文件同会话、同到期时间、同发送引用保护。
- 两种输入、原有 MP3 分享和有限并发不变；不额外下载上游视频。

## Task Dependencies

| 任务 | 依赖 | Ownership |
|---|---|---|
| T1 媒体准备 | 冻结上述契约 | app/services/media.py、tests/test_video.py |
| T2 页面 | 冻结上述字段 | app/static/app.js、app/templates/index.html、必要相邻样式；可与 T1 并行 |
| T3 任务与交付 | T1 契约；整体验证需 T1 完成 | app/services/tasks.py、app/main.py、tests/test_video_delivery.py、必要关联测试 |
| T4 整合与文档 | T1–T3 | README.md、ARCHITECTURE.md、docs/SECURITY.md、docs/RELIABILITY.md、既有设计/验收、活跃索引 |

## Tasks

### T1 媒体准备

- 文件：Modify media.py，Create test_video.py；不得改任务/API/页面。
- 顺序：先用真实媒体回归测试描述原 MP4 字节保留，再实现。
- 验证：MOV 无损封装、真实容器、视频/声音包摘要与流数/编码一致；各流与容器时长容差沿用设计公式；标题和旋转信息保留。
- 边界：大小/失败/取消回收。

### T2 页面

- 文件：Modify app.js/index.html；不改后端契约。
- 结果：添加原视频下载按钮和独立不可用提示；不扩展 MP4 系统分享。
- 边界：复用下载到期阻断与同源检查；新任务、异常、到期重置链接。
- 验证：真实浏览器下载，检查双按钮、窄屏、生命周期。

### T3 任务与交付

- 文件：Modify tasks.py/main.py，Create test_video_delivery.py；关联 tests/test_tasks.py 与 tests/test_real_http.py 仅在行为实际改变时调整。
- 顺序：聚焦回归先确认原行为缺少视频交付，再接通准备/降级/双产物字段、固定文件选择和媒体类型。
- 交付：两文件共用引用计数；音频兼容。
- 验证：本地及分享输入、认证/owner、HEAD/Range、过期/取消和降级。

### T4 整合与文档

| 文档范围 | 动作 |
|---|---|
| README.md、ARCHITECTURE.md、docs/SECURITY.md、docs/RELIABILITY.md、docs/active/index.md、docs/active/0.1.0/audio-extraction/{spec,design,mobile-acceptance}.md | Modify “成功删输入”“输出仅 MP3”等约束 |
| docs/active/0.1.0/audio-extraction/acceptance.md | 仅追加新增证据，保留旧验收日期与范围 |
| docs/active/0.1.0/video-download/acceptance.md | Create |
| docs/design-docs/arch-review-hardening.md | 统一维护双媒体临时交付决定 |

- 检查顺序：相关测试和 Ruff → 按跨媒体/文件/页面影响运行完整套件和真实浏览器流程。
- 验收要求：真实媒体测试实际执行、零跳过。
- 临时局域网测试：继续原授权，仅验证成功后重启开发服务。

## Integration Acceptance

- 两种输入单次处理同时产生 MP3 和实际 MP4；真实 MP4 字节一致，MOV 封装后编码包一致。
- MP4 封装失败仍交付可播放 MP3；取消/超时/到期正确回收，活动文件响应保护两种产物。
- 未登录不能下载；另一会话不可读取；HEAD/Range 和标准 attachment 成功；非法 kind422、格式错误 Range400、范围超出文件416。
- 页面两种下载实际触发，MP3 试听/分享回归，无旧视频链接或窄屏溢出。
- 新增功能的工程证据与旧容器/真机证据分开；iPhone/Android MP4 保存播放待真实反馈。

## Decision Boundary

- 可自行选择：契约内的内部辅助函数和测试样本。
- 需重新对齐：更改音频已有字段、重编码视频、独立视频任务、上传大小或保留期、公开部署等。
