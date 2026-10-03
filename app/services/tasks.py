"""Bounded, browser-owned ephemeral tasks for a single ASGI worker."""

import asyncio
import contextlib
import fcntl
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.resolver.base import MediaResolver
from app.resolver.registry import platform_of
from app.services.artifacts import ARTIFACTS, ArtifactKind, download_filename
from app.services.errors import MediaError
from app.services.media import MediaProcessor
from app.services.network import SafeHTTP

TaskState = Literal[
    "receiving", "resolving", "downloading", "converting", "ready", "failed", "cancelled"
]
TERMINAL = {"ready", "failed", "cancelled"}


@dataclass
class Task:
    id: str
    owner: str
    directory: Path
    title: str
    state: TaskState = "receiving"
    message: str = "正在接收视频"
    author: str = ""
    duration: float | None = None
    expires_at: float | None = None
    runner: asyncio.Task | None = None
    cancellation: asyncio.Task[None] | None = None
    receive_deadline: float | None = None
    deliveries: int = 0
    cleanup_pending: bool = False
    video_ready: bool = False
    video_message: str = ""


class TaskManager:
    def __init__(
        self,
        settings: Settings,
        media: MediaProcessor,
        resolvers: dict[str, MediaResolver],
        network: SafeHTTP,
        *,
        select_platform: Callable[[str], str] = platform_of,
    ) -> None:
        self.settings = settings
        self.media = media
        self.resolvers = resolvers
        self.network = network
        self.select_platform = select_platform
        self.root = settings.data_dir / ".soundpick-tasks"
        self.tasks: dict[str, Task] = {}
        self.cleaner: asyncio.Task | None = None
        self.lock = None

    async def open(self) -> None:
        if self.root.is_symlink():
            raise RuntimeError("任务目录不能为符号链接")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.lock = (self.root / ".lock").open("a")
        (self.root / ".lock").chmod(0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            self.lock = None
            raise RuntimeError("同一任务目录只支持一个应用进程") from None
        try:
            for path in self.root.iterdir():
                if re.fullmatch(r"[a-f0-9]{32}", path.name):
                    if path.is_symlink():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
            self.cleaner = asyncio.create_task(self._clean_loop())
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    async def _clean_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            with contextlib.suppress(OSError):
                await self.sweep()

    def _remove(self, task: Task) -> None:
        try:
            if task.directory.exists():
                shutil.rmtree(task.directory)
            task.cleanup_pending = False
        except OSError:
            # Keep this bounded task reachable for the next sweep/startup cleanup.
            task.cleanup_pending = True

    def _sweep(self) -> None:
        now = time.time()
        for task in list(self.tasks.values()):
            if (
                task.state == "receiving"
                and task.runner is None
                and task.receive_deadline is not None
                and task.receive_deadline <= now
            ):
                self._finish(task, "failed", "上传预约已过期，请重新选择视频")
            if task.cleanup_pending and not task.deliveries:
                self._remove(task)
            if task.expires_at is not None and task.expires_at <= now and not task.deliveries:
                self._remove(task)
                if not task.cleanup_pending:
                    self.tasks.pop(task.id, None)

    async def sweep(self) -> None:
        self._sweep()

    def reserve(
        self, owner: str, title: str, *, require_media: bool = True, task_id: str | None = None
    ) -> Task:
        if task_id is not None and not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise MediaError("任务标识格式无效", "invalid_input", 400)
        self._sweep()
        if task_id in self.tasks:
            return self.get(task_id, owner)
        if require_media and not self.media.available:
            raise MediaError("媒体处理工具未就绪，请联系管理员", "media_unavailable", 503)
        self._sweep()
        if any(task.state not in TERMINAL for task in self.tasks.values()):
            raise MediaError("正在处理一个视频，请完成后再试", "busy", 409)
        if len(self.tasks) >= self.settings.max_retained_tasks:
            raise MediaError("临时结果已满，请等待旧结果到期后再试", "capacity", 409)
        task_id = task_id or secrets.token_hex(16)
        directory = self.root / task_id
        try:
            directory.mkdir(mode=0o700)
        except OSError:
            raise MediaError("临时存储不可用，请稍后重试", "storage_error", 503) from None
        title = title.encode("utf-8", errors="replace").decode("utf-8")
        task = Task(task_id, owner, directory, title[:1000])
        task.receive_deadline = time.time() + self.settings.upload_timeout_seconds
        self.tasks[task.id] = task
        return task

    def get(self, task_id: str, owner: str) -> Task:
        self._sweep()
        task = self.tasks.get(task_id)
        if (
            task is None
            or task.owner != owner
            or (task.expires_at is not None and task.expires_at <= time.time())
        ):
            raise MediaError("任务不存在或已过期，请重新提取", "task_missing", 404)
        return task

    def view(self, task: Task) -> dict:
        ready = task.state == "ready"
        return {
            "id": task.id,
            "state": task.state,
            "message": task.message,
            "title": task.title,
            "author": task.author,
            "duration_seconds": task.duration,
            "expires_at": task.expires_at,
            "preview_url": f"/files/{task.id}" if ready else None,
            "download_url": f"/files/{task.id}?download=1" if ready else None,
            "filename": download_filename(task.title) if ready else None,
            "video_download_url": (
                f"/files/{task.id}?kind=video&download=1" if ready and task.video_ready else None
            ),
            "video_filename": (
                download_filename(task.title, "video") if ready and task.video_ready else None
            ),
            "video_message": task.video_message if ready else "",
        }

    def _finish(self, task: Task, state: str, message: str) -> None:
        task.state, task.message = state, message
        task.expires_at = time.time() + self.settings.result_ttl_seconds
        if state != "ready":
            self._remove(task)

    async def receive(self, task: Task, stream: AsyncIterator[bytes]) -> None:
        self._sweep()
        if task.state != "receiving" or task.runner is not None:
            raise MediaError("任务不再接收视频，请查看原任务状态", "not_receiving", 409)
        task.runner = asyncio.current_task()
        try:
            count = 0
            async with asyncio.timeout(self.settings.upload_timeout_seconds):
                with (task.directory / "input").open("xb") as output:
                    (task.directory / "input").chmod(0o600)
                    async for chunk in stream:
                        count += len(chunk)
                        if count > self.settings.max_media_bytes:
                            raise MediaError("视频超过文件大小限制", "size_limit", 413)
                        output.write(chunk)
            if count == 0:
                raise MediaError("请选择一个非空视频文件", "invalid_input", 400)
        except OSError:
            self._finish(task, "failed", "临时存储无法接收视频，请稍后重试")
            raise MediaError("临时存储无法接收视频，请稍后重试", "storage_error", 503) from None
        except BaseException:
            self._finish(task, "cancelled", "上传已停止，请重新选择视频")
            raise
        finally:
            task.runner = None

    def start(self, task: Task, share_url: str | None = None) -> None:
        if task.state != "receiving" or task.runner is not None:
            raise MediaError("任务已启动或停止，请查看原任务状态", "already_started", 409)
        task.state = "resolving" if share_url else "converting"
        task.message = "正在解析分享链接" if share_url else "正在提取完整音轨"
        task.runner = asyncio.create_task(self._run(task, share_url))

    async def _run(self, task: Task, share_url: str | None) -> None:
        try:
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                if share_url:
                    video = await self.resolvers[self.select_platform(share_url)].resolve(share_url)
                    task.title = video.title or "音拾音频"
                    task.author = video.author
                    task.state, task.message = "downloading", "正在下载原视频"
                    await self.network.download(
                        video.video_url, task.directory / "input", self.settings.max_media_bytes
                    )
                task.state, task.message = "converting", "正在提取完整音轨"
                task.duration = await self.media.extract_audio(
                    task.directory / "input", task.directory / "audio.mp3"
                )
                task.message = "正在准备原视频下载"
                try:
                    await self.media.prepare_video(
                        task.directory / "input", task.directory / "video.mp4"
                    )
                    task.video_ready = True
                except MediaError as exc:
                    if exc.code == "media_timeout":
                        raise
                    task.video_message = "原视频暂无法保存为 MP4，MP3 仍可下载"
                (task.directory / "input").unlink(missing_ok=True)
                self._finish(task, "ready", "提取完成，可以试听或保存")
        except asyncio.CancelledError:
            self._finish(task, "cancelled", "处理已取消")
            raise
        except TimeoutError:
            self._finish(task, "failed", "处理超时，请重试或选择较短的视频")
        except MediaError as exc:
            self._finish(task, "failed", exc.message)
        except Exception:
            self._finish(task, "failed", "处理失败，请稍后重试或上传本地视频")
        finally:
            task.runner = None

    async def cancel(self, task: Task) -> None:
        # All callers await one operation; disconnecting a waiter cannot interrupt cleanup.
        if task.cancellation is None:
            if task.state in TERMINAL:
                return
            task.cancellation = asyncio.create_task(self._cancel(task))
        await asyncio.shield(task.cancellation)

    async def _cancel(self, task: Task) -> None:
        runner = task.runner
        if runner is not None and runner is not asyncio.current_task():
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
        if task.state not in TERMINAL:
            self._finish(task, "cancelled", "处理已取消")

    def acquire_file(self, task: Task, kind: ArtifactKind = "audio") -> Path:
        if task.state != "ready":
            raise MediaError("音频尚未准备好", "not_ready", 409)
        if kind == "video" and not task.video_ready:
            raise MediaError("原视频暂无法保存为 MP4，MP3 仍可下载", "video_unavailable", 409)
        path = task.directory / ARTIFACTS[kind].stored_name
        if not path.is_file():
            raise MediaError("文件已过期，请重新提取", "task_missing", 404)
        task.deliveries += 1
        return path

    async def release_file(self, task: Task) -> None:
        task.deliveries -= 1
        await self.sweep()

    async def close(self) -> None:
        if self.cleaner:
            self.cleaner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.cleaner
        try:
            for task in list(self.tasks.values()):
                await self.cancel(task)
                if not task.deliveries:
                    self._remove(task)
            self.tasks.clear()
        finally:
            if self.lock:
                self.lock.close()
                self.lock = None
