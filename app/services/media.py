"""Local media validation, complete MP3 conversion, and lossless MP4 preparation."""

import asyncio
import json
import math
import shutil
from contextlib import suppress
from pathlib import Path

from app.config import Settings
from app.services.errors import MediaError

_PROBE_OUTPUT_LIMIT = 64 * 1024
_MAX_STREAMS = 128
_READINESS_TIMEOUT = 10


class MediaProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ffmpeg = shutil.which(settings.ffmpeg_bin)
        self.ffprobe = shutil.which(settings.ffprobe_bin)
        self.readiness_error: str | None = None
        self.versions: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe and self.readiness_error is None)

    async def check_readiness(self) -> bool:
        """Cache bounded runtime checks before accepting application tasks."""
        self.readiness_error = "媒体工具未通过运行检查"
        self.versions = {}
        if not self.ffmpeg or not self.ffprobe:
            self.readiness_error = "FFmpeg/FFprobe 未就绪，请安装系统工具或配置工具路径。"
            return False
        name = "FFmpeg"
        versions = {}
        try:
            async with asyncio.timeout(_READINESS_TIMEOUT):
                for name, executable in (("FFmpeg", self.ffmpeg), ("FFprobe", self.ffprobe)):
                    output = await self._run(executable, "-version", capture=True)
                    lines = output.decode("utf-8", errors="replace").splitlines()
                    if not lines or not lines[0].lower().startswith(f"{name.lower()} version "):
                        raise MediaError("工具版本无效", "media_unavailable", 503)
                    versions[name] = lines[0]
                name = "FFmpeg"
                output = await self._run(self.ffmpeg, "-hide_banner", "-encoders", capture=True)
                encoders = [
                    line.split() for line in output.decode("utf-8", errors="replace").splitlines()
                ]
                if not any(
                    len(fields) >= 2 and fields[0].startswith("A") and fields[1] == "libmp3lame"
                    for fields in encoders
                ):
                    self.readiness_error = "FFmpeg 缺少 libmp3lame，不能生成 MP3。"
                    return False
        except (OSError, MediaError, TimeoutError):
            self.readiness_error = f"{name} 无法运行、信息无效或检查超时。"
            return False
        self.versions = versions
        self.readiness_error = None
        return True

    async def extract_audio(self, source: Path, target: Path) -> float:
        # A caller error must never cause the input to be removed by output cleanup.
        if source.resolve() == target.resolve():
            raise MediaError("输入和输出文件必须不同", "invalid_media")
        succeeded = False
        try:
            if not self.available:
                raise MediaError("媒体工具不可用，请联系管理员", "media_unavailable", 503)
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                self._validate_source(source)
                streams, container_duration = await self._probe(source)
                self._validate_stream_presence(streams)
                audio = next(s for s in streams if s.get("codec_type") == "audio")
                original_duration = self._duration(audio.get("duration")) or container_duration
                if original_duration is None:
                    raise MediaError("无法确认视频声音时长", "invalid_media")
                if (
                    max(original_duration, container_duration or 0)
                    > self.settings.max_duration_seconds
                ):
                    raise MediaError("视频超过时长限制", "duration_limit")
                await self._run(
                    self.ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-xerror",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(source.resolve()),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    "-map_metadata",
                    "-1",
                    "-fs",
                    str(self.settings.max_duration_seconds * 24000 + 1024 * 1024),
                    "-f",
                    "mp3",
                    str(target.resolve()),
                )
                if not target.is_file() or target.stat().st_size == 0:
                    raise MediaError("声音转换未生成有效文件", "conversion_failed")
                target.chmod(0o600)
                output_streams, output_duration = await self._probe(target)
                if output_duration is None or not any(
                    s.get("codec_type") == "audio" for s in output_streams
                ):
                    raise MediaError("无法确认转换后的声音时长", "conversion_failed")
                tolerance = max(0.25, min(2.0, original_duration * 0.005))
                if abs(output_duration - original_duration) > tolerance:
                    raise MediaError("转换后的声音不完整，请重新提取", "incomplete_audio")
                succeeded = True
                return output_duration
        except TimeoutError:
            raise MediaError("声音处理超时，请重试", "media_timeout") from None
        except OSError:
            raise MediaError("无法读取或处理视频文件，请重试", "media_error") from None
        finally:
            if not succeeded:
                with suppress(OSError):
                    target.unlink(missing_ok=True)

    async def prepare_video(self, source: Path, target: Path) -> float:
        """Preserve actual MP4 bytes; other containers require lossless remuxing."""
        if source.resolve() == target.resolve():
            raise MediaError("输入和输出文件必须不同", "invalid_media")
        succeeded = False
        try:
            if not self.available:
                raise MediaError("媒体工具不可用，请联系管理员", "media_unavailable", 503)
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                self._validate_source(source)
                streams, info = await self._probe_video(source)
                duration = self._validate_video(streams, info)
                if self._is_mp4(source, info):
                    if source.resolve().parent != target.resolve().parent:
                        raise MediaError("视频保存位置无效", "invalid_media")
                    await self._decode_video(source, streams)
                    source.chmod(0o600)
                    source.replace(target)
                else:
                    selected = [
                        stream
                        for stream in streams
                        if stream.get("codec_type") in {"video", "audio"}
                    ]
                    mapping = []
                    for output_index, stream in enumerate(selected):
                        mapping += ["-map", f"0:{stream['index']}"]
                        disposition = stream.get("disposition", {})
                        if not isinstance(disposition, dict):
                            raise MediaError("无法确认视频流属性", "invalid_media")
                        flags = "+".join(
                            flag for flag, enabled in disposition.items() if enabled == 1
                        )
                        mapping += [f"-disposition:{output_index}", flags or "0"]
                    output_limit = self.settings.max_media_bytes + 1024 * 1024
                    await self._run(
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-xerror",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(source.resolve()),
                        *mapping,
                        "-c",
                        "copy",
                        "-map_metadata",
                        "0",
                        "-fs",
                        str(output_limit),
                        "-f",
                        "mp4",
                        str(target.resolve()),
                    )
                    if not target.is_file() or not 0 < target.stat().st_size <= output_limit:
                        raise MediaError("视频输出为空或超过大小限制", "video_unavailable")
                    target.chmod(0o600)
                    output_streams, output_info = await self._probe_video(target)
                    if not self._is_mp4(target, output_info):
                        raise MediaError("无法确认输出为 MP4", "video_unavailable")
                    output_duration = self._validate_video(output_streams, output_info)
                    self._validate_video_copy(selected, output_streams, duration, output_duration)
                    await self._decode_video(target, output_streams)
                    duration = output_duration
                succeeded = True
                return duration
        except TimeoutError:
            raise MediaError("视频处理超时，请重试", "media_timeout") from None
        except OSError:
            raise MediaError("无法读取或处理视频文件，请重试", "media_error") from None
        finally:
            if not succeeded:
                with suppress(OSError):
                    target.unlink(missing_ok=True)

    async def _decode_video(self, source: Path, streams: list[dict]) -> None:
        # Container metadata alone cannot prove that the video packets decode.
        # Decode every video stream to the null muxer without changing deliverable bytes.
        mapping = []
        for stream in streams:
            if self._is_motion_video(stream):
                mapping += ["-map", f"0:{stream['index']}"]
        await self._run(
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-abort_on",
            "empty_output",
            "-err_detect",
            "explode",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source.resolve()),
            *mapping,
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        )

    async def _probe_video(self, path: Path) -> tuple[list[dict], dict]:
        raw = await self._run(
            self.ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_entries",
            "stream=index,codec_type,codec_name,duration:"
            "stream_disposition:stream_tags:stream_side_data:"
            "format=format_name,duration:format_tags",
            "-of",
            "json",
            str(path.resolve()),
            capture=True,
        )
        try:
            result = json.loads(raw)
            streams, info = result["streams"], result["format"]
            if (
                not isinstance(streams, list)
                or len(streams) > _MAX_STREAMS
                or not all(isinstance(stream, dict) for stream in streams)
                or not isinstance(info, dict)
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError):
            raise MediaError("视频损坏或无法识别", "invalid_media") from None
        return streams, info

    @staticmethod
    def _is_motion_video(stream: dict) -> bool:
        if stream.get("codec_type") != "video":
            return False
        disposition = stream.get("disposition", {})
        if not isinstance(disposition, dict):
            raise MediaError("无法确认视频流属性", "invalid_media")
        return disposition.get("attached_pic", 0) != 1

    def _validate_stream_presence(self, streams: list[dict]) -> None:
        if not any(self._is_motion_video(stream) for stream in streams):
            raise MediaError("文件中没有视频画面", "missing_video")
        if not any(stream.get("codec_type") == "audio" for stream in streams):
            raise MediaError("视频中没有声音轨道", "missing_audio")

    def _validate_video(self, streams: list[dict], info: dict) -> float:
        self._validate_stream_presence(streams)
        duration = self._duration(info.get("duration"))
        if duration is None:
            raise MediaError("无法确认视频完整时长", "invalid_media")
        durations = [duration]
        for stream in streams:
            if stream.get("codec_type") not in {"video", "audio"}:
                continue
            if (
                not isinstance(stream.get("codec_name"), str)
                or not stream["codec_name"]
                or type(stream.get("index")) is not int
                or stream["index"] < 0
            ):
                raise MediaError("无法确认视频编码或流标识", "invalid_media")
            stream_duration = self._duration(stream.get("duration"))
            if stream_duration is not None:
                durations.append(stream_duration)
        if max(durations) > self.settings.max_duration_seconds:
            raise MediaError("视频超过时长限制", "duration_limit")
        return duration

    def _validate_video_copy(
        self,
        original: list[dict],
        output: list[dict],
        original_duration: float,
        output_duration: float,
    ) -> None:
        actual = [stream for stream in output if stream.get("codec_type") in {"video", "audio"}]
        if len(original) != len(actual):
            raise MediaError("视频流不完整，请重新处理", "incomplete_video")
        pairs = [(original_duration, output_duration)]
        for before, after in zip(original, actual, strict=True):
            if (before.get("codec_type"), before.get("codec_name")) != (
                after.get("codec_type"),
                after.get("codec_name"),
            ):
                raise MediaError("视频编码发生变化，请重新处理", "incomplete_video")
            before_duration = self._duration(before.get("duration"))
            if before_duration is not None:
                after_duration = self._duration(after.get("duration"))
                if after_duration is None:
                    raise MediaError("无法确认输出视频流时长", "incomplete_video")
                pairs.append((before_duration, after_duration))
        for before, after in pairs:
            if abs(before - after) > max(0.25, min(2.0, before * 0.005)):
                raise MediaError("原视频不完整，请重新处理", "incomplete_video")

    @staticmethod
    def _is_mp4(path: Path, info: dict) -> bool:
        # FFprobe shares one demuxer for MOV/MP4; ftyp must also identify MP4.
        if "mp4" not in str(info.get("format_name", "")).split(","):
            return False
        brands = {b"isom", b"mp41", b"mp42", b"avc1", b"M4V ", b"MSNV", b"dash", b"cmfc", b"cmfs"}
        brands.update(f"iso{version}".encode() for version in range(2, 10))
        file_size = path.stat().st_size
        with path.open("rb") as stream:
            for _ in range(128):
                start = stream.tell()
                header = stream.read(8)
                if len(header) != 8:
                    return False
                box_size = int.from_bytes(header[:4], "big")
                header_size = 8
                if box_size == 1:
                    extended = stream.read(8)
                    if len(extended) != 8:
                        return False
                    box_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif box_size == 0:
                    box_size = file_size - start
                if box_size < header_size or start + box_size > file_size:
                    return False
                if header[4:] == b"ftyp":
                    if box_size < header_size + 8:
                        return False
                    # QuickTime's qt brand is deliberately absent, even with MP4 suffix.
                    return stream.read(4) in brands
                stream.seek(start + box_size)
        return False

    def _validate_source(self, source: Path) -> None:
        size = source.stat().st_size
        if size == 0:
            raise MediaError("视频文件为空", "empty_media", 400)
        if size > self.settings.max_media_bytes:
            raise MediaError("视频文件超过大小限制", "media_size_limit", 413)
        with source.open("rb") as stream:
            header = stream.read(512)
        recognized = (
            header[4:8] in {b"ftyp", b"moov", b"mdat", b"wide", b"free"}
            or header.startswith(b"\x1a\x45\xdf\xa3")
            or (header.startswith(b"RIFF") and header[8:12] == b"AVI ")
            or header.startswith((b"FLV\x01", b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))
            or (len(header) > 376 and header[0] == header[188] == header[376] == 0x47)
        )
        if not recognized:
            raise MediaError("文件不是受支持的视频容器", "invalid_media")

    @staticmethod
    def _duration(value: object) -> float | None:
        try:
            duration = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return duration if math.isfinite(duration) and duration > 0 else None

    async def _probe(self, path: Path) -> tuple[list[dict], float | None]:
        raw = await self._run(
            self.ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_entries",
            "stream=codec_type,duration:stream_disposition:format=duration",
            "-of",
            "json",
            str(path.resolve()),
            capture=True,
        )
        try:
            result = json.loads(raw)
            streams = result["streams"]
            if (
                not isinstance(streams, list)
                or len(streams) > _MAX_STREAMS
                or not all(isinstance(stream, dict) for stream in streams)
            ):
                raise ValueError
            duration = self._duration(result.get("format", {}).get("duration"))
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise MediaError("视频损坏或无法识别", "invalid_media") from None
        return streams, duration

    async def _run(self, executable: str, *args: str, capture: bool = False) -> bytes:
        # stderr may contain private local paths; discard it and expose fixed errors.
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                executable,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                limit=_PROBE_OUTPUT_LIMIT,
            )
        )
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:

            async def reap_spawned() -> None:
                # Retain the spawn task until its handle arrives, even after another cancel.
                process = await spawning
                await self._stop(process)

            with suppress(OSError):
                await self._finish_cleanup(asyncio.create_task(reap_spawned()))
            raise
        try:
            output = bytearray()
            if capture:
                while chunk := await process.stdout.read(8192):
                    output.extend(chunk)
                    if len(output) > _PROBE_OUTPUT_LIMIT:
                        raise MediaError("媒体信息过多，无法处理此视频", "invalid_media")
            await process.wait()
            if process.returncode != 0:
                raise MediaError("视频损坏或声音转换失败", "conversion_failed")
            return bytes(output)
        finally:
            await self._finish_cleanup(asyncio.create_task(self._stop(process)))

    @staticmethod
    async def _finish_cleanup(cleanup: asyncio.Task) -> None:
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        if process.stdout is not None:
            # Drain after kill: a full StreamReader buffer otherwise delays wait().
            while await process.stdout.read(8192):
                pass
        await process.wait()
