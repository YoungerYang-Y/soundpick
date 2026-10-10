"""Bounded, browser-owned ephemeral tasks for a single ASGI worker."""

import asyncio
import contextlib
import fcntl
import hashlib
import logging
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.logging import get_request_id
from app.resolver.base import MediaResolver
from app.resolver.registry import platform_of
from app.services.artifacts import ARTIFACTS, ArtifactKind, download_filename
from app.services.errors import MediaError
from app.services.media import MediaProcessor
from app.services.network import SafeHTTP, public_url
from app.services.protection import ProtectionStore

TaskState = Literal[
    "receiving", "resolving", "downloading", "converting", "ready", "failed", "cancelled"
]
InputKind = Literal["upload", "link"]
TERMINAL = {"ready", "failed", "cancelled"}
logger = logging.getLogger(__name__)


@dataclass
class Task:
    id: str
    owner: str
    directory: Path
    title: str
    input_kind: InputKind = "upload"
    input_fingerprint: str | None = None
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
    video_cleanup_pending: bool = False
    video_ready: bool = False
    video_message: str = ""
    request_id: str | None = None
    started_at: float = dataclass_field(default_factory=time.monotonic)


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
        self.protection = ProtectionStore(
            redis_url=settings.redis_url.get_secret_value(),
            key_prefix=settings.redis_key_prefix,
            timeout_seconds=settings.redis_timeout_seconds,
            retry_seconds=settings.redis_retry_seconds,
        )
        self.quota_releases: set[str] = set()

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
            self.protection.open()
            for path in self.root.iterdir():
                if re.fullmatch(r"[a-f0-9]{32}", path.name):
                    if path.is_symlink():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
            self.protection.recover()
            self.quota_releases.clear()
            self.cleaner = asyncio.create_task(self._clean_loop())
        except BaseException:
            self.protection.close()
            self.lock.close()
            self.lock = None
            raise

    async def _clean_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            with contextlib.suppress(OSError, MediaError):
                await self.sweep()

    def _release_quota(
        self,
        task_id: str,
        *,
        request_id: str | None = None,
        input_kind: InputKind | None = None,
    ) -> None:
        try:
            self.protection.release(task_id)
        except MediaError as exc:
            # At most ten reservations can be outstanding. Keep their IDs until
            # storage recovers; deleting media must not lose the release obligation.
            first_defer = task_id not in self.quota_releases
            self.quota_releases.add(task_id)
            if first_defer:
                task = self.tasks.get(task_id)
                logger.warning(
                    "task.quota_release_deferred",
                    extra=self._task_log_fields(
                        task,
                        task_id=task_id,
                        request_id=task.request_id if task else request_id,
                        input_kind=task.input_kind if task else input_kind,
                        error_category=self._safe_category(exc.code),
                    ),
                )
        else:
            self.quota_releases.discard(task_id)

    def _remove(self, task: Task) -> None:
        try:
            if task.directory.exists():
                shutil.rmtree(task.directory)
            was_pending = task.cleanup_pending or task.video_cleanup_pending
            task.cleanup_pending = False
            task.video_cleanup_pending = False
            if was_pending:
                logger.info("task.cleaned", extra=self._task_log_fields(task))
        except OSError:
            # Keep this bounded task reachable for the next sweep/startup cleanup.
            first_defer = not task.cleanup_pending
            task.cleanup_pending = True
            if first_defer:
                logger.warning(
                    "task.cleanup_deferred",
                    extra=self._task_log_fields(task, error_category="storage_error"),
                )

    def _remove_partial_video(self, task: Task) -> None:
        # A degraded task still delivers MP3: never use whole-directory cleanup here.
        try:
            (task.directory / ARTIFACTS["video"].stored_name).unlink(missing_ok=True)
        except OSError:
            if not task.video_cleanup_pending:
                logger.warning(
                    "task.cleanup_deferred",
                    extra=self._task_log_fields(task, error_category="storage_error"),
                )
            task.video_cleanup_pending = True
        else:
            if task.video_cleanup_pending:
                logger.info("task.cleaned", extra=self._task_log_fields(task))
            task.video_cleanup_pending = False

    def _sweep(self) -> None:
        for task_id in list(self.quota_releases):
            self._release_quota(task_id)
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
            if task.video_cleanup_pending:
                self._remove_partial_video(task)
            if task.expires_at is not None and task.expires_at <= now and not task.deliveries:
                self._remove(task)
                if not task.cleanup_pending:
                    self.tasks.pop(task.id, None)

    async def sweep(self) -> None:
        self._sweep()

    def reserve(
        self,
        owner: str,
        title: str,
        *,
        require_media: bool = True,
        task_id: str | None = None,
        input_kind: InputKind = "upload",
        share_url: str | None = None,
    ) -> Task:
        request_id = get_request_id()
        if task_id is not None and not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise MediaError("任务标识格式无效", "invalid_input", 400)
        self._sweep()
        if task_id in self.tasks:
            task = self.get(task_id, owner)
            if task.input_kind != input_kind:
                raise MediaError("任务标识已用于其他输入，请重新创建任务", "input_conflict", 409)
            fingerprint = self._input_fingerprint(input_kind, share_url)
            if task.input_fingerprint != fingerprint:
                raise MediaError("任务标识已用于其他输入，请重新创建任务", "input_conflict", 409)
            return task
        fingerprint = self._input_fingerprint(input_kind, share_url)
        if require_media and not self.media.available:
            raise MediaError("媒体处理工具未就绪，请联系管理员", "media_unavailable", 503)
        self._sweep()
        if any(task.state not in TERMINAL for task in self.tasks.values()):
            raise MediaError("正在处理一个视频，请完成后再试", "busy", 409)
        if len(self.tasks) >= self.settings.max_retained_tasks:
            raise MediaError("临时结果已满，请等待旧结果到期后再试", "capacity", 409)
        task_id = task_id or secrets.token_hex(16)
        directory = self.root / task_id
        self.protection.reserve(task_id)
        try:
            directory.mkdir(mode=0o700)
        except OSError:
            self._release_quota(task_id, request_id=request_id, input_kind=input_kind)
            raise MediaError("临时存储不可用，请稍后重试", "storage_error", 503) from None
        title = title.encode("utf-8", errors="replace").decode("utf-8")
        task = Task(
            task_id,
            owner,
            directory,
            title[:1000],
            input_kind=input_kind,
            input_fingerprint=fingerprint,
            request_id=request_id,
        )
        task.receive_deadline = time.time() + self.settings.upload_timeout_seconds
        self.tasks[task.id] = task
        logger.info(
            "task.created",
            extra=self._task_log_fields(task, input_kind=task.input_kind),
        )
        return task

    @staticmethod
    def _input_fingerprint(input_kind: InputKind, share_url: str | None) -> str | None:
        if input_kind == "upload" and share_url is None:
            return None
        if input_kind == "link" and share_url:
            normalized = str(public_url(share_url))
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        raise MediaError("任务输入类型或分享链接无效", "invalid_input", 400)

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

    def _stage(self, task: Task, state: TaskState, message: str) -> None:
        task.state, task.message = state, message
        logger.info(
            "task.stage",
            extra=self._task_log_fields(
                task,
                stage=state,
                elapsed_ms=max(0, int((time.monotonic() - task.started_at) * 1000)),
            ),
        )

    def _failure(self, task: Task, category: str) -> None:
        # Never log exception messages, URLs, titles or traceback locals.
        logger.warning(
            "task.failed",
            extra=self._task_log_fields(
                task,
                stage=task.state,
                error_category=self._safe_category(category),
                elapsed_ms=max(0, int((time.monotonic() - task.started_at) * 1000)),
            ),
        )

    @staticmethod
    def _safe_category(category: str) -> str:
        if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", category):
            return category
        return "unexpected_error"

    @staticmethod
    def _task_log_fields(task: Task | None, **fields: str | int | None) -> dict:
        values = {
            "task_id": task.id if task else None,
            "request_id": task.request_id if task else None,
            "input_kind": task.input_kind if task else None,
        }
        values.update(fields)
        return {key: value for key, value in values.items() if value is not None}

    def _finish(self, task: Task, state: TaskState, message: str) -> None:
        if state == "ready":
            # Persist success before any response can expose the downloadable result.
            self.protection.succeed(task.id)
        else:
            self._release_quota(task.id)
            self._stage(task, state, message)
            task.expires_at = time.time() + self.settings.result_ttl_seconds
            self._remove(task)
            return
        self._stage(task, state, message)
        task.expires_at = time.time() + self.settings.result_ttl_seconds

    async def receive(self, task: Task, stream: AsyncIterator[bytes]) -> None:
        self._sweep()
        if task.input_kind != "upload":
            raise MediaError("此任务不接收本地视频，请重新创建任务", "input_conflict", 409)
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
        if self._input_fingerprint(task.input_kind, share_url) != task.input_fingerprint:
            raise MediaError("任务输入与创建时不一致，请重新创建任务", "input_conflict", 409)
        if task.state != "receiving" or task.runner is not None:
            raise MediaError("任务已启动或停止，请查看原任务状态", "already_started", 409)
        self._stage(
            task,
            "resolving" if share_url else "converting",
            "正在解析分享链接" if share_url else "正在提取完整音轨",
        )
        task.runner = asyncio.create_task(self._run(task, share_url))

    async def _run(self, task: Task, share_url: str | None) -> None:
        try:
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                if share_url:
                    video = await self.resolvers[self.select_platform(share_url)].resolve(share_url)
                    task.title = video.title or "音拾音频"
                    task.author = video.author
                    self._stage(task, "downloading", "正在下载原视频")
                    await self.network.download(
                        video.video_url, task.directory / "input", self.settings.max_media_bytes
                    )
                self._stage(task, "converting", "正在提取完整音轨")
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
                    self._failure(task, exc.code)
                    task.video_message = "原视频暂无法保存为 MP4，MP3 仍可下载"
                    self._remove_partial_video(task)
                (task.directory / "input").unlink(missing_ok=True)
                self._finish(task, "ready", "提取完成，可以试听或保存")
        except asyncio.CancelledError:
            self._finish(task, "cancelled", "处理已取消")
            raise
        except TimeoutError:
            self._failure(task, "task_timeout")
            self._finish(task, "failed", "处理超时，请重试或选择较短的视频")
        except MediaError as exc:
            self._failure(task, exc.code)
            self._finish(task, "failed", exc.message)
        except Exception as exc:
            self._failure(task, type(exc).__name__)
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
        try:
            if self.cleaner:
                self.cleaner.cancel()
                with contextlib.suppress(asyncio.CancelledError, MediaError, OSError):
                    await self.cleaner
            for task in list(self.tasks.values()):
                await self.cancel(task)
                task.expires_at = time.time()
                if not task.deliveries:
                    self._remove(task)
            self._sweep()
        finally:
            try:
                self.protection.close()
            finally:
                if self.lock:
                    self.lock.close()
                    self.lock = None
