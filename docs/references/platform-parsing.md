# 平台解析与证据边界

检查日期：2026-10-02。本记录用于说明外部来源的实际限制，不代替 [需求规格](../active/0.1.0/audio-extraction/spec.md)。

## 原视频来源规则

一律取得原视频后转换音轨。平台 `music` / `music_url` 可能指背景音乐素材，禁止用其替代用户视频中的完整声音。内置适配必须精确匹配视频 ID，推荐作品、缺失 ID 或跳转平台首页均不能成功。

抖音 Feed 精确匹配路径参考 [ucmao/media-parser 实现](https://github.com/ucmao/media-parser/blob/main/src/parsers/douyin_parser.py)，分享页结构参考 [parse-video 抖音实现](https://github.com/wujunwei928/parse-video/blob/main/parser/douyin.go)。平台公开 Web 详情存在 Cookie/访问限制，维护者 [yt-dlp 实现](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py) 也报告该边界，不承诺全部链接免登录可解析。

## 本轮实网检查

| 来源 | 本地实网结果 | 尚不能证明 |
|---|---|---|
| 抖音 video/7521023890996514083 | 实际本项目 SafeHTTP 固定公网 DNS 请求，Feed 精确匹配目标；下载 15,254,144 字节，MP4 ftyp 标记存在；未读 music 字段 | FFprobe 音轨探测、全长 MP3、首尾声音对比尚缺运行工具 |
| 抖音短链 mXosMBsOHF0 | 调查中可跳转分享页，但该 ID 的 Feed 仅返回其他推荐视频；应用必须拒绝 | 不能拿推荐首条作成功样本 |
| 火山 share.huoshan.com/hotsoon/s/XwMt9xLSG58 | 调查中旧短链及 item/info 接口跳转至抖音首页，规范页返回 502；当前无可用样本 | 不证明火山功能可用；仍需新分享样本或可靠解析端点 |

火山旧实现来源为 [parse-video 火山适配](https://github.com/wujunwei928/parse-video/blob/master/parser/huoshan.go)。保留可配置 JSON provider 是替换路径，不代表已经有成功接入的外部服务。

## 服务替换

项目 provider 接受固定 GET 端点，url 查询参数输入分享地址，规范输出 video_url/title/author。可参考 [parse-video API](https://github.com/wujunwei928/parse-video) 的扁平结构；其他服务需明确映射并核对原视频语义。令牌仅放本机 `.env`，不在日志或聊天展示。无密钥服务无需配置 token。

新平台扩展：登记精确输入主机集合、实现 MediaResolver、补充错误目标 ID 与 BGM 回归、用真实样本验证视频到全长 MP3，然后再声明该平台通过验收。
