"""Redis protection with a bounded memory shadow and outage circuit breaker."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence

from app.services.errors import MediaError
from app.services.memory_protection import DAILY_SUCCESS_LIMIT, MemoryProtection

__all__ = ["DAILY_SUCCESS_LIMIT", "ProtectionStore"]
logger = logging.getLogger(__name__)


class ProtectionStore:
    def __init__(
        self,
        *,
        redis_url: str = "",
        key_prefix: str = "soundpick:{protection}",
        timeout_seconds: float = 0.1,
        retry_seconds: float = 30,
        clock: Callable[[], float] = time.time,
        retry_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock = clock
        self.retry_clock = retry_clock
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.timeout_seconds = timeout_seconds
        self.retry_seconds = retry_seconds
        self.memory = MemoryProtection(clock=lambda: self.clock())
        self._backend = None
        self._opened = False
        self._connected = False
        self._next_retry = 0.0
        self._lock = threading.RLock()

    @property
    def mode(self) -> str:
        return "redis" if self._connected else "memory"

    def open(self) -> None:
        with self._lock:
            self._opened = True

    def close(self) -> None:
        with self._lock:
            if self._backend is not None:
                try:
                    self._backend.close()
                except Exception:
                    pass  # Closing a failed connection must not prevent process-lock release.
                self._backend = None
            self._opened = False
            self._connected = False

    def recover(self) -> None:
        with self._lock:
            self._require_open()
            self.memory.recover()
            self._connected = False
            self._next_retry = 0
            self._ensure_remote()

    def reserve(self, task_id: str) -> None:
        self._quota("reserve", task_id)

    def succeed(self, task_id: str) -> None:
        self._quota("succeed", task_id)

    def release(self, task_id: str) -> None:
        self._quota("release", task_id)

    def _quota(self, operation: str, task_id: str) -> None:
        with self._lock:
            self._require_open()
            self._ensure_remote()
            previous = self.memory.quota_snapshot()
            getattr(self.memory, operation)(task_id)
            if self._connected:
                try:
                    snapshot = self._backend.quota(operation, task_id, previous)
                    self.memory.set_quota(snapshot)
                except MediaError:
                    self.memory.set_quota(previous)
                    raise
                except Exception:
                    # A timeout can follow a committed write. Keep the local operation;
                    # ID-based reconciliation makes reserve/succeed/release idempotent.
                    self._degrade()

    def check_rate(self, groups: str | Sequence[str], identity: str) -> None:
        with self._lock:
            self._require_open()
            local_error = None
            try:
                self.memory.check_rate(groups, identity, record=False)
            except MediaError as error:
                local_error = error
            self._ensure_remote()
            if self._connected:
                try:
                    # A locally denied request only probes the remote wait; it must
                    # not consume any remote budget, even when Redis would allow it.
                    self._backend.check_rate(groups, identity, record=local_error is None)
                except MediaError as error:
                    if local_error is None or error.retry_after > local_error.retry_after:
                        raise
                except Exception:
                    self._degrade()
            if local_error is not None:
                raise local_error
            self.memory.check_rate(groups, identity)

    def _ensure_remote(self) -> None:
        if not self.redis_url or self._connected or self.retry_clock() < self._next_retry:
            return
        try:
            if self._backend is None:
                from app.services.redis_protection import RedisBackend

                self._backend = RedisBackend(
                    redis_url=self.redis_url,
                    key_prefix=self.key_prefix,
                    timeout_seconds=self.timeout_seconds,
                    clock=lambda: self.clock(),
                )
            # Replace orphan remote reservations, union successes, and retain all live
            # local reservations. No failure/release queue can grow during an outage.
            snapshot = self._backend.quota("merge", "", self.memory.quota_snapshot())
            self.memory.set_quota(snapshot)
        except Exception:
            self._degrade()
            return
        self._connected = True
        logger.info("防刷存储使用 Redis；内存额度已合并")

    def _degrade(self) -> None:
        # Log only transitions/probes, never connection strings or exception details.
        logger.warning("Redis 防刷存储不可用，使用内存保护；稍后重试")
        self._connected = False
        self._next_retry = self.retry_clock() + self.retry_seconds

    def _require_open(self) -> None:
        if not self._opened:
            raise MediaError("保护存储未启动", "protection_unavailable", 503)
