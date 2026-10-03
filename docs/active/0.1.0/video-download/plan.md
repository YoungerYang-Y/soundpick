# 原视频 MP4 下载实施计划

版本：0.1.0；按同目录 spec.md / design.md 的行为与接口执行。

## Goal / Context

完成一次处理即可下载 MP3 和完整原视频 MP4。现有任务成功后删除 input；新增视频交付需要同步改变媒体准备、任务生命周期、文件端点和页面。音频原契约保持兼容。

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
Modify media.py，Create test_video.py：先用真实媒体回归测试描述原 MP4 字节保留，再实现；逐步验证 MOV 无损封装、真实容器、视频/声音包摘要与流数/编码保持一致，各流与容器时长容差沿用设计公式，标题和旋转信息保留；验证大小/失败/取消回收。不得改任务/API/页面。

### T2 页面
Modify app.js/index.html：添加原视频下载按钮和独立不可用提示；复用现有下载到期阻断与同源检查。新任务、异常、到期重置链接。用真实浏览器下载验证双按钮、窄屏、生命周期；不扩展 MP4 系统分享，不改后端契约。

### T3 任务与交付
Modify tasks.py/main.py，Create test_video_delivery.py：聚焦回归先确认原行为缺少视频交付，再接通准备/降级/双产物字段、固定文件选择和媒体类型；两文件共用引用计数。关联 tests/test_tasks.py 与 tests/test_real_http.py 仅在行为实际改变时调整。验证本地及分享输入、认证/owner、HEAD/Range、过期/取消和降级；音频兼容。

### T4 整合与文档
Modify README.md、ARCHITECTURE.md、docs/SECURITY.md、docs/RELIABILITY.md、docs/active/index.md、docs/active/0.1.0/audio-extraction/{spec,design,mobile-acceptance}.md 中“成功删输入”“输出仅 MP3”等约束；docs/active/0.1.0/audio-extraction/acceptance.md 仅追加新增证据并保留旧验收日期与范围，Create docs/active/0.1.0/video-download/acceptance.md。双媒体临时交付决定统一维护在 docs/design-docs/arch-review-hardening.md。先检查相关测试和 Ruff，再按跨媒体/文件/页面影响运行完整套件和真实浏览器流程；验收要求真实媒体测试实际执行、零跳过。临时局域网测试继续原授权，仅验证成功后重启开发服务。

## Integration Acceptance

- 两种输入单次处理同时产生 MP3 和实际 MP4；真实 MP4 字节一致，MOV 封装后编码包一致。
- MP4 封装失败仍交付可播放 MP3；取消/超时/到期正确回收，活动文件响应保护两种产物。
- 未登录不能下载；另一会话不可读取；HEAD/Range 和标准 attachment 成功；非法 kind422、格式错误 Range400、范围超出文件416。
- 页面两种下载实际触发，MP3 试听/分享回归，无旧视频链接或窄屏溢出。
- 新增功能的工程证据与旧容器/真机证据分开；iPhone/Android MP4 保存播放待真实反馈。

## Decision Boundary

契约内的内部辅助函数和测试样本由实现者选择；更改音频已有字段、重编码视频、独立视频任务、上传大小或保留期、公开部署等需重新对齐。
