"""Local media validation and complete first-audio-stream MP3 conversion."""

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


class MediaProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ffmpeg = shutil.which(settings.ffmpeg_bin)
        self.ffprobe = shutil.which(settings.ffprobe_bin)

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

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
                if not any(stream.get("codec_type") == "video" for stream in streams):
                    raise MediaError("文件中没有视频画面", "missing_video")
                audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
                if audio is None:
                    raise MediaError("视频中没有声音轨道", "missing_audio")
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
            "stream=codec_type,duration:format=duration",
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
            process = await spawning
            await self._stop(process)
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
            await self._stop(process)

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
