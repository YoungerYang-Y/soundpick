"""In-memory quota and request-rate protection for degraded operation."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from math import ceil

from app.services.errors import MediaError

DAILY_SUCCESS_LIMIT = 10
MAX_QUOTA_KEYS = 4096
_BEIJING = timezone(timedelta(hours=8))
_GLOBAL_IDENTITY = "__soundpick_global__"

RATE_LIMITS: dict[str, tuple[tuple[str, int, int], ...]] = {
    "login": (
        ("ip", 10, 60),
        ("ip", 30, 900),
        ("global", 60, 60),
        ("global", 300, 900),
    ),
    "extract": (("ip", 6, 60), ("global", 20, 60), ("global", 30, 3600)),
    "upload": (("ip", 6, 60), ("global", 20, 60)),
    "poll": (("ip", 120, 60), ("global", 600, 60)),
    "download": (("ip", 30, 60), ("global", 120, 60)),
}


def beijing_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, _BEIJING).date().isoformat()


def seconds_until_midnight(timestamp: float) -> int:
    local_now = datetime.fromtimestamp(timestamp, _BEIJING)
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), datetime_time.min, _BEIJING
    )
    return max(1, ceil((next_midnight - local_now).total_seconds()))


def rate_budgets(groups: str | Sequence[str], identity: str) -> list[tuple[str, str, int, int]]:
    names = (groups,) if isinstance(groups, str) else tuple(groups)
    unknown = set(names).difference(RATE_LIMITS)
    if unknown:
        raise ValueError("Unknown protection rate group")

    budgets: list[tuple[str, str, int, int]] = []
    for name in names:
        for kind, limit, window in RATE_LIMITS[name]:
            bucket_identity = identity if kind == "ip" else _GLOBAL_IDENTITY
            budget = (f"{name}:{kind}:{window}", bucket_identity, limit, window)
            if budget not in budgets:
                budgets.append(budget)
    return budgets


class MemoryProtection:
    def __init__(
        self, *, clock: Callable[[], float] = time.time, max_rate_keys: int = 4096
    ) -> None:
        if max_rate_keys < 1:
            raise ValueError("max_rate_keys must be positive")
        self.clock = clock
        self.max_rate_keys = max_rate_keys
        self._lock = threading.RLock()
        self._quota: dict[str, str] = {}
        self._rates: dict[tuple[str, str], tuple[int, deque[float]]] = {}

    def reserve(self, task_id: str) -> None:
        with self._lock:
            now = self.clock()
            self._prune_quota(now)
            current = self._quota.get(task_id)
            if current is not None:
                if current != "pending":
                    raise MediaError("任务标识已使用", "input_conflict", 409)
                return

            used = sum(
                state == "pending" or state == beijing_date(now) for state in self._quota.values()
            )
            if used >= DAILY_SUCCESS_LIMIT:
                retry_after = seconds_until_midnight(now)
                raise MediaError(
                    f"今日全站额度已用完，请在北京时间零点后重试（约 {retry_after} 秒）",
                    "daily_quota",
                    429,
                    retry_after=retry_after,
                )
            if len(self._quota) >= MAX_QUOTA_KEYS:
                raise MediaError("保护存储容量已满", "protection_unavailable", 503)
            self._quota[task_id] = "pending"

    def succeed(self, task_id: str) -> None:
        with self._lock:
            now = self.clock()
            self._prune_quota(now)
            current = self._quota.get(task_id)
            if current is None:
                raise MediaError("任务额度预占不存在", "task_missing", 409)
            if current == "pending":
                self._quota[task_id] = beijing_date(now)

    def release(self, task_id: str) -> None:
        with self._lock:
            if self._quota.get(task_id) == "pending":
                del self._quota[task_id]

    def recover(self) -> None:
        with self._lock:
            self._quota = {
                task_id: state for task_id, state in self._quota.items() if state != "pending"
            }

    def set_quota(self, snapshot: Mapping[str, str]) -> None:
        normalized: dict[str, str] = {}
        for task_id, state in snapshot.items():
            if state != "pending":
                try:
                    state = date.fromisoformat(state).isoformat()
                except (TypeError, ValueError):
                    raise ValueError("Invalid quota snapshot") from None
            normalized[task_id] = state
        if len(normalized) > MAX_QUOTA_KEYS:
            raise MediaError("保护存储容量已满", "protection_unavailable", 503)
        with self._lock:
            self._quota = normalized
            self._prune_quota(self.clock())

    def quota_snapshot(self) -> dict[str, str]:
        with self._lock:
            self._prune_quota(self.clock())
            return dict(self._quota)

    def check_rate(
        self, groups: str | Sequence[str], identity: str, *, record: bool = True
    ) -> None:
        budgets = rate_budgets(groups, identity)
        if not budgets:
            return

        with self._lock:
            now = self.clock()
            self._prune_rates(now)
            retry_after = 0
            for bucket, bucket_identity, limit, window in budgets:
                entry = self._rates.get((bucket, bucket_identity))
                if entry is None:
                    continue
                _, events = entry
                if len(events) >= limit:
                    retry_after = max(retry_after, max(1, ceil(events[0] + window - now)))

            if retry_after:
                raise MediaError(
                    f"请求过于频繁，请 {retry_after} 秒后重试",
                    "rate_limit",
                    429,
                    retry_after=retry_after,
                )
            missing = {
                (bucket, bucket_identity)
                for bucket, bucket_identity, _, _ in budgets
                if (bucket, bucket_identity) not in self._rates
            }
            if len(self._rates) + len(missing) > self.max_rate_keys:
                releases = [
                    max(events) + window for window, events in self._rates.values() if events
                ]
                retry_after = max(1, ceil(min(releases) - now)) if releases else 1
                raise MediaError(
                    f"请求过于频繁，请 {retry_after} 秒后重试",
                    "rate_limit",
                    429,
                    retry_after=retry_after,
                )
            if not record:
                return

            by_key = {
                (bucket, bucket_identity): (limit, window)
                for bucket, bucket_identity, limit, window in budgets
            }
            for key in {(bucket, bucket_identity) for bucket, bucket_identity, *_ in budgets}:
                if key not in self._rates:
                    self._rates[key] = (by_key[key][1], deque())
                self._rates[key][1].append(now)

    def _prune_quota(self, now: float) -> None:
        keep_from = (date.fromisoformat(beijing_date(now)) - timedelta(days=7)).isoformat()
        self._quota = {
            task_id: state
            for task_id, state in self._quota.items()
            if state == "pending" or state >= keep_from
        }

    def _prune_rates(self, now: float) -> None:
        for key, (window, events) in tuple(self._rates.items()):
            cutoff = now - window
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                del self._rates[key]
