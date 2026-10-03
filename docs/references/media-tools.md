# 本地媒体工具安装记录

安装日期：2026-10-03。用户明确授权准备所需工具；仅安装 FFmpeg 和 FFprobe，保留项目既有 Python、Node 与依赖环境。

## 来源与校验

使用 [FFmpeg 官方下载页](https://ffmpeg.org/download.html) 列出的 BtbN Linux 预编译来源。本次锁定 [2026-10-01 日期发行](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-01-13-06) 中的 9.0 稳定分支构建，适配当前 WSL 的 x86_64、glibc 2.39。

| 项目 | 实际值 |
|---|---|
| 发行标签 | `autobuild-2026-10-01-13-06` |
| 归档 | `ffmpeg-n9.0.2-22-g46d8f462ee-linux64-gpl-9.0.tar.xz` |
| 下载大小 | 151,030,404 字节 |
| 归档 SHA-256 | `a6170faecf757381ad0338d7a6ba26e97c2ebe2b9c1568633421e15d1ed436a9` |
| 校验方式 | 下载大小、GitHub 资产摘要与发行方 `checksums.sha256` 均一致 |
| 实际工具版本 | `n9.0.2-22-g46d8f462ee-20261001`，为 9.0 分支的日期构建 |
| MP3 编码器 | `libmp3lame` 可用，真实转换与解码测试通过 |

归档校验通过后检查相对路径，只解出 Linux ELF `bin/ffmpeg`、`bin/ffprobe` 和 `LICENSE.txt`。安装记录及两个可执行文件的摘要保存在同目录 `install-manifest.json`。

## 本机接入

安装位置为项目内已忽略的 `work/tools/ffmpeg-n9.0.2-22-g46d8f462ee-linux64-gpl-9.0/`。可执行文件权限为 755；项目 `.env` 权限保持 600，只更新 `FFMPEG_BIN`、`FFPROBE_BIN` 两项绝对路径，保留其他配置。

未修改系统包或全局 PATH。二进制、下载缓存、媒体样本与私有 `.env` 不提交 Git；其他机器需自行准备兼容工具，不应照搬本机绝对路径。容器使用镜像中的工具。

应用和运行检查会读取 `.env`。测试配置与私有 `.env` 隔离，运行真实媒体测试时应显式提供工具变量：

```bash
.venv/bin/python -m scripts.check_runtime

SOUNDPICK_MEDIA_BIN="$PWD/work/tools/ffmpeg-n9.0.2-22-g46d8f462ee-linux64-gpl-9.0/bin"
FFMPEG_BIN="$SOUNDPICK_MEDIA_BIN/ffmpeg" FFPROBE_BIN="$SOUNDPICK_MEDIA_BIN/ffprobe" make test
```

当前工具的版本和编码能力检查退出码为 0；真实媒体与平台检查范围见 [MVP 验收记录](../active/0.1.0/audio-extraction/acceptance.md)。容器使用独立的 Debian 媒体工具，已完成实际构建与运行检查，版本和证据见 [容器验收记录](./container-verification.md)。
