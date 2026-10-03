# 平台解析与证据边界

解析调查日期：2026-10-02；真实抖音及火山版转码复核：2026-10-03。本记录用于说明外部来源的实际限制，不代替 [需求规格](../active/0.1.0/audio-extraction/spec.md)。

## 原视频来源规则

| 规则 / 依据 | 内容 |
|---|---|
| 音轨来源 | 一律取得原视频后转换音轨；平台 `music` / `music_url` 可能是背景音乐素材，禁止替代用户视频中的完整声音 |
| 匹配要求 | 内置适配必须精确匹配视频 ID；推荐作品、缺失 ID 或跳转平台首页均不能成功 |
| Feed 匹配参考 | [ucmao/media-parser 抖音实现](https://github.com/ucmao/media-parser/blob/main/src/parsers/douyin_parser.py) |
| 分享页结构参考 | [parse-video 抖音实现](https://github.com/wujunwei928/parse-video/blob/main/parser/douyin.go) |
| 访问限制 | 平台公开 Web 详情存在 Cookie/访问限制；维护者的 [yt-dlp 实现](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py)也报告该边界；不承诺全部链接免登录可解析 |

## 本轮实网检查

| 来源 | 本地实网结果 | 尚不能证明 |
|---|---|---|
| 抖音 video/7521023890996514083 | 当前 API 实网解析、下载 15,254,144 字节、真实 FFmpeg 转码、附件下载及 Range 206 通过；原音轨与 MP3 均为 319.852993 秒，输出 7,678,162 字节；原视频与 MP3 首、中、尾采样声音相关性均高于 0.999 | 仅证明这个样本，不能保证所有抖音分享链接；不证明手机真机保存 |
| 抖音短链 mXosMBsOHF0 | 调查中可跳转分享页，但该 ID 的 Feed 仅返回其他推荐视频；应用必须拒绝 | 不能拿推荐首条作成功样本 |
| 火山版短链 v.douyin.com/DUMJbobJ4gY/ | 官方跳转标识 aweme_hotsoon，精确目标 7659736603242784995；当前 API 实网下载 2,986,909 字节并转换为 290,942 字节 MP3；原音轨与输出均为 12.050998 秒；附件、Range 206 与任务清理通过 | 一个当前火山版共用抖音域的分享样本；不证明全部火山链接或手机真机保存 |
| 火山旧域 share.huoshan.com/hotsoon/s/XwMt9xLSG58 | 调查中旧短链及 item/info 接口跳转至抖音首页，规范页返回 502 | 旧 huoshan.com 域样本仍未恢复，不能用当前共用域样本替代该证据 |

| 项目 | 记录 |
|---|---|
| 火山旧实现来源 | [parse-video 火山适配](https://github.com/wujunwei928/parse-video/blob/master/parser/huoshan.go) |
| JSON provider | 保留可配置 JSON provider 作为替换路径；不代表已有成功接入的外部服务 |
| 抖音原视频 SHA-256 | `dbaca1bc3647bf46f657e6f68b2702e545c9220aff5e28e2cf56395d431bca02`；实网重下载值与先前下载的精确 ID 样本一致 |
| 抽样位置 | 0.3 秒、159.9264965 秒、318.552993 秒；每处取 1 秒声音 |
| 抽样相关性 | 0.999992、0.999984、0.999948 |
| 证据边界 | 抽样内容结合全长时长与第一音轨映射；不声称逐个采样点无损一致 |
| 清理与工具 | 临时媒体及检查输出已在后续裁剪中清理；本文保留来源、摘要与测量结果。工具来源见[媒体工具安装记录](./media-tools.md) |

## 当前火山版分享证据

| 项目 | 记录 |
|---|---|
| 分享来源 | [头条主站公开分享帖](https://www.toutiao.com/video/7659994808300011560/)发布日期为 2026-07-08；文案含“复制打开抖音火山版”和短链 `https://v.douyin.com/DUMJbobJ4gY/` |
| 跳转调查 | 2026-10-03 使用项目 SafeHTTP GET 短链；官方 302 指向 `www.iesdouyin.com/share/video/7659736603242784995/`，参数为 `app=aweme_hotsoon`、`from_aid=8663` |
| 凭据与地址 | 未使用登录凭据；记录不保留完整签名地址 |
| 解析器依据 | [parse-video-py 实现](https://github.com/wujunwei928/parse-video-py/blob/dab12708368eae054ba2dae5db16d8b040b9029e/src/parse_video_py/parser/douyin.py)也将抖音和抖音火山版共用解析器 |
| 应用匹配 | 当前输入主机已登记；现有 DouyinResolver 按官方视频 ID 精确选取 `video.play_addr`，无需新增适配代码 |
| API 检查 | 输入为含“复制打开抖音火山版”的分享文字；执行真实公网解析、下载及 FFmpeg 转码 |
| 原视频 | SHA-256 `6ac4535cc2a85e3f01fbd603cec22bd32fa1c5989089bee6f904eaf53d044026`；与独立来源调查的完整响应一致 |
| 抽样 | 0.3 秒、6.025499 秒、10.750998 秒处各解码 1 秒；相关性 0.999782、0.999951、0.999989 |
| MP3 | SHA-256 `ec0d81fa6ae1d673a0889b06e0d34c8e1727b53417381ed7114bcf58eae503cb` |
| 证据边界 | 来源、跳转和 API 抽样来自当时临时检查；原始 JSON 已在后续裁剪中清理。本次由 TestClient 执行真实外部请求和真实媒体工具，不是手机真机测试。首期火山版输入有一个成功样本；旧域适配限制仍单独保留 |

## 服务替换

- 项目 provider 接受固定 GET 端点；分享地址通过 `url` 查询参数输入；规范输出为 `video_url/title/author`。
- 可参考 [parse-video API](https://github.com/wujunwei928/parse-video)的扁平结构；其他服务需明确映射并核对原视频语义。
- 令牌仅放本机 `.env`，不在日志或聊天展示；无密钥服务无需配置 token。
- 新平台须登记精确输入主机集合、实现 MediaResolver、补充错误目标 ID 与 BGM 回归，并用真实样本验证视频到全长 MP3；完成后再声明该平台通过验收。
