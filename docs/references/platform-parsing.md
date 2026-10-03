# 平台解析与证据边界

解析调查日期：2026-10-02；真实抖音转码复核：2026-10-03。本记录用于说明外部来源的实际限制，不代替 [需求规格](../active/0.1.0/audio-extraction/spec.md)。

## 原视频来源规则

一律取得原视频后转换音轨。平台 `music` / `music_url` 可能指背景音乐素材，禁止用其替代用户视频中的完整声音。内置适配必须精确匹配视频 ID，推荐作品、缺失 ID 或跳转平台首页均不能成功。

抖音 Feed 精确匹配路径参考 [ucmao/media-parser 实现](https://github.com/ucmao/media-parser/blob/main/src/parsers/douyin_parser.py)，分享页结构参考 [parse-video 抖音实现](https://github.com/wujunwei928/parse-video/blob/main/parser/douyin.go)。平台公开 Web 详情存在 Cookie/访问限制，维护者 [yt-dlp 实现](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py) 也报告该边界，不承诺全部链接免登录可解析。

## 本轮实网检查

| 来源 | 本地实网结果 | 尚不能证明 |
|---|---|---|
| 抖音 video/7521023890996514083 | 当前 API 实网解析、下载 15,254,144 字节、真实 FFmpeg 转码、附件下载及 Range 206 通过；原音轨与 MP3 均为 319.852993 秒，输出 7,678,162 字节；原视频与 MP3 首、中、尾采样声音相关性均高于 0.999 | 仅证明这个样本，不能保证所有抖音分享链接；不证明手机真机保存 |
| 抖音短链 mXosMBsOHF0 | 调查中可跳转分享页，但该 ID 的 Feed 仅返回其他推荐视频；应用必须拒绝 | 不能拿推荐首条作成功样本 |
| 火山 share.huoshan.com/hotsoon/s/XwMt9xLSG58 | 调查中旧短链及 item/info 接口跳转至抖音首页，规范页返回 502；当前无可用样本 | 不证明火山功能可用；仍需新分享样本或可靠解析端点 |

火山旧实现来源为 [parse-video 火山适配](https://github.com/wujunwei928/parse-video/blob/master/parser/huoshan.go)。保留可配置 JSON provider 是替换路径，不代表已经有成功接入的外部服务。

抖音实网重下载得到的原视频 SHA-256 为 `dbaca1bc3647bf46f657e6f68b2702e545c9220aff5e28e2cf56395d431bca02`，与先前下载的精确 ID 样本一致。基于该视频的本地转换抽样对比位置为 0.3 秒、159.9264965 秒和 318.552993 秒，各取 1 秒声音，相关性分别为 0.999992、0.999984、0.999948；这是抽样内容证据，结合全长时长与第一音轨映射，不声称逐个采样点无损一致。测试用媒体和检查结果仅保留在 Git 忽略目录；工具来源见 [媒体工具安装记录](./media-tools.md)。

## 服务替换

项目 provider 接受固定 GET 端点，url 查询参数输入分享地址，规范输出 video_url/title/author。可参考 [parse-video API](https://github.com/wujunwei928/parse-video) 的扁平结构；其他服务需明确映射并核对原视频语义。令牌仅放本机 `.env`，不在日志或聊天展示。无密钥服务无需配置 token。

新平台扩展：登记精确输入主机集合、实现 MediaResolver、补充错误目标 ID 与 BGM 回归、用真实样本验证视频到全长 MP3，然后再声明该平台通过验收。
