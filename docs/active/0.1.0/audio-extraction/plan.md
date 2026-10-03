# MVP Implementation Plan

| 项目 | 依据 |
|---|---|
| 逻辑版本 | 0.1.0 |
| 行为 / 接口与边界 | [spec.md](./spec.md) / [design.md](./design.md) |
| 制定计划时的基线 | 只有登录和页面骨架，全部媒体行为新增 |
| 当前验证进展 | [验收记录](./acceptance.md) |

## Goal / Key Decisions

- 目标：两个输入（分享文本/本地视频）→ 原视频完整MP3、试听、下载、支持时系统分享。
- 容量：单进程单活动任务；工程默认200MiB/1800秒/结果3600秒可配置，非用户硬性要求。
- 上传：原始流，不将200MiB缓冲进内存。
- 来源：平台视频地址必须来自真实来源，禁用BGM替代。
- 所属：所有任务和文件归创建浏览器会话。

## Task Dependencies / Ownership

| 任务 | 依赖 | 独占范围 |
|---|---|---|
| T1 公共契约与限制 | 无 | 主代理：config、resolver/base、services/errors及network契约 |
| T2 媒体转换 | T1 | 媒体代理：services/media.py、tests/test_media.py |
| T3 平台与安全下载 | T1、网页研究 | 主代理：services/network.py、resolver/、tests/test_resolver.py、test_network.py |
| T4 任务与Web API | T2、T3 | 主代理：services/tasks.py、main、middleware、任务/API测试 |
| T5 手机交互 | T4接口冻结 | UI代理：templates/index、static、UI检查 |
| T6 整合验收与文档 | T2–T5 | 主代理：README、AGENTS、ARCHITECTURE、安全/可靠性/需求文档、活跃索引 |

## Tasks

### T1 公共契约与限制

- Modify `app/config.py`：新增可配置媒体工具、输入大小/时长/时间/保留/解析端点；保持秘密隐藏。
- Create `app/services/errors.py`：安全可读错误。
- Modify `app/resolver/base.py`：沿用现有视频契约。
- 分派：明确network API后再进行；禁止共享配置并发改写。

验收：配置边界测试，非法阈值拒绝；现有登录安全不退化。

### T2 媒体转换

- Create `app/services/media.py` 及 `tests/test_media.py`；输入Path、Settings，输出duration与本地MP3。
- 校验：容器magic/媒体流/时长，及输出时长。
- 转换：协议file/pipe、无shell；整段第一条原声音流；禁止裁剪、分离或music字段。
- 生命周期：超时/取消回收进程。
- 验证：测试先描述目标行为，真实生成视频、验证MP3；缺工具不能把skip当成功。

### T3 平台与安全下载

- 文件：Create `app/services/network.py`；Modify `app/resolver/{base,douyin,huoshan}.py`；Create分享识别/通用provider辅助及相关测试。
- 扩展：平台登记支持后续扩展。
- 网络：逐跳DNS公网与固定IP/TLS/Host；body上限；固定provider凭据不转发。
- 证据：真实页面字段有证据再实现；fixture测页面结构、BGM陷阱；实际样本证明平台可用。

### T4 任务与Web API

- 文件：Create `app/services/tasks.py`；Modify `app/main.py`、`app/middleware.py`、相关tests。
- API：实现design全部API；保留共享密码CSRF，登录随机owner。
- 生命周期：一个活动任务含上传，内存结果≤32；随机目录/私有权限。
- 上传：不缓冲整个上传；保持小请求实际字节限制，上传超限断流并清理。
- 验证：停止/取消/超时/过期/重启路径有测试；FileResponse Range与会话隔离有真实HTTP证据。

### T5 手机交互

- 文件：Modify `app/templates/index.html`、`app/static/{app.js,style.css}`；只修改UI独占范围，契约不自行变更。
- 交互：沿用现有token、大按钮与焦点；能力检测、链接/文件输入、粘贴、上传进度、轮询/取消/错误重试、原生音频播放与下载。
- 保存：下载指引始终存在；分享先准备File，再由用户明确点击调用；条件不满足仍可下载。
- 内容：用textContent展示来源文字，避免HTML拼接。
- 结论边界：不可声称浏览器保存成功或固定微信分享目标。

### T6 整合验收与文档

- 检查：相关测试、Ruff、Node语法、真实HTTP上传/播放Range/下载/取消/过期；可用窄屏及桌面浏览器。
- 文档：逐项审计spec，区分真实本地、实网、容器与手机证据；更新当前实现及剩余限制，不能缩减需求来宣称完成。
- Docker无守护进程：说明未验证，不部署。
- 上游登录/限流、样本或凭据缺失：继续其他工作，并具体请求必要输入。

## Decision Boundary

- 可自行决定：既定接口与行为内的内部结构、测试夹具和可配置默认值。
- 需另行对齐：新增收费provider、持久存储、用户系统、批任务或发布。
- 授权：本计划不自行授权 commit/push/deploy；Git 操作与部署依据用户后续明确指令。
