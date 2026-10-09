"""Synchronous, atomic Redis protection backend; failures are handled by its facade."""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlsplit

from app.services.errors import MediaError
from app.services.memory_protection import (
    DAILY_SUCCESS_LIMIT,
    MAX_QUOTA_KEYS,
    beijing_date,
    rate_budgets,
    seconds_until_midnight,
)

if TYPE_CHECKING:
    from redis import Redis

_QUOTA_TTL = 8 * 24 * 3600
_MAX_SNAPSHOT_BYTES = 1024 * 1024
# MEMORY USAGE includes allocator/hash overhead; keep room above the payload cap.
_MAX_SNAPSHOT_MEMORY_BYTES = 3 * _MAX_SNAPSHOT_BYTES
_QUOTA_OPERATIONS = frozenset({"reserve", "succeed", "release", "recover", "merge", "snapshot"})

# Read and validate before writing: Lua errors do not roll back prior Redis commands.
# Pair arguments/results keep the snapshot bounded without JSON object ambiguity.
_QUOTA_SCRIPT = """
local key = KEYS[1]
local operation, task_id, today, keep_from = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
local limit, capacity, ttl, max_bytes = tonumber(ARGV[5]), tonumber(ARGV[6]),
    tonumber(ARGV[7]), tonumber(ARGV[8])
local max_memory_bytes = tonumber(ARGV[9])
local function valid_state(value)
    if value == 'pending' then return true end
    local year, month, day = string.match(value, '^(%d%d%d%d)%-(%d%d)%-(%d%d)$')
    if not year then return false end
    year, month, day = tonumber(year), tonumber(month), tonumber(day)
    if year < 1 or month < 1 or month > 12 or day < 1 then return false end
    local days = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}
    if year % 4 == 0 and (year % 100 ~= 0 or year % 400 == 0) then days[2] = 29 end
    return day <= days[month]
end
if redis.call('HLEN', key) > capacity then
    return redis.error_reply('Invalid protection snapshot capacity')
end
-- HLEN bounds this full allocation-size walk. No field/value is copied into Lua.
local memory_bytes = redis.call('MEMORY', 'USAGE', key, 'SAMPLES', 0)
if memory_bytes and memory_bytes > max_memory_bytes then
    return redis.error_reply('Invalid protection snapshot memory size')
end
local original = redis.call('HGETALL', key)
local states, bytes = {}, 0
for index = 1, #original, 2 do
    local id, state = original[index], original[index + 1]
    bytes = bytes + #id + #state
    if bytes > max_bytes or not valid_state(state) then
        return redis.error_reply('Invalid protection snapshot')
    end
    if state == 'pending' or state >= keep_from then states[id] = state end
end
if operation == 'merge' or #ARGV > 9 then
    for id, state in pairs(states) do
        if state == 'pending' then states[id] = nil end
    end
    for index = 10, #ARGV, 2 do
        local id, state = ARGV[index], ARGV[index + 1]
        if not state or not valid_state(state) then
            return redis.error_reply('Invalid protection merge snapshot')
        end
        if not states[id] and (state == 'pending' or state >= keep_from) then
            states[id] = state
        end
    end
end
local code = 'ok'
local current = states[task_id]
if operation == 'reserve' then
    if current and current ~= 'pending' then
        code = 'conflict'
    elseif not current then
        local used = 0
        for _, state in pairs(states) do
            if state == 'pending' or state == today then used = used + 1 end
        end
        if used >= limit then code = 'daily' else states[task_id] = 'pending' end
    end
elseif operation == 'succeed' then
    if not current then code = 'missing'
    elseif current == 'pending' then states[task_id] = today end
elseif operation == 'release' then
    if current == 'pending' then states[task_id] = nil end
elseif operation == 'recover' then
    for id, state in pairs(states) do
        if state == 'pending' then states[id] = nil end
    end
elseif operation ~= 'snapshot' and operation ~= 'merge' then
    return redis.error_reply('Unknown protection operation')
end
local count, result = 0, {}
bytes = 0
for id, state in pairs(states) do
    count = count + 1
    bytes = bytes + #id + #state
    table.insert(result, id)
    table.insert(result, state)
end
if count > capacity or bytes > max_bytes then
    return {'full', {}}
end
for index = 1, #original, 2 do
    if not states[original[index]] then redis.call('HDEL', key, original[index]) end
end
for id, state in pairs(states) do
    redis.call('HSET', key, id, state)
end
redis.call('EXPIRE', key, ttl)
return {code, result}
"""

_RATE_SCRIPT = """
local now, member = tonumber(ARGV[1]), ARGV[2]
local record = ARGV[3] == '1'
local retry_after = 0
-- Validate all buckets before pruning or appending to any of them.
for index, key in ipairs(KEYS) do
    local kind = redis.call('TYPE', key).ok
    if kind ~= 'none' and kind ~= 'zset' then
        return redis.error_reply('Invalid protection rate bucket')
    end
end
for index, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[index * 2 + 2])
    local window = tonumber(ARGV[index * 2 + 3])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
    if redis.call('ZCARD', key) >= limit then
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        retry_after = math.max(retry_after, math.max(1,
            math.ceil(tonumber(oldest[2]) + window - now)))
    end
end
if retry_after > 0 or not record then return retry_after end
for index, key in ipairs(KEYS) do
    local window = tonumber(ARGV[index * 2 + 3])
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window)
end
return 0
"""


class RedisBackend:
    def __init__(
        self,
        redis_url: str,
        key_prefix: str = "soundpick:{protection}",
        timeout_seconds: float = 0.25,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Redis timeout must be positive and finite")
        # redis-py gives URL query parameters precedence over explicit kwargs.
        if any(name != "db" for name, _ in parse_qsl(urlsplit(redis_url).query)):
            raise ValueError("Redis URL query may only configure db")
        if not key_prefix:
            raise ValueError("Redis key prefix must not be empty")
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self._client: Redis | None = None
        self._client_lock = threading.RLock()

    def _get_client(self) -> Redis:
        with self._client_lock:
            if self._client is None:
                # Private deployments do not need to import or connect to Redis.
                from redis import Redis
                from redis.backoff import NoBackoff
                from redis.retry import Retry

                self._client = Redis.from_url(
                    self.redis_url,
                    socket_timeout=self.timeout_seconds,
                    socket_connect_timeout=self.timeout_seconds,
                    retry=Retry(NoBackoff(), 0),
                    retry_on_timeout=False,
                    max_connections=2,
                    health_check_interval=0,
                    protocol=2,
                    decode_responses=True,
                )
            return self._client

    def probe(self) -> None:
        """Check connection and Lua/MEMORY support without changing protection data."""
        result = self._get_client().eval(
            "return redis.call('MEMORY', 'USAGE', KEYS[1]) or 0",
            1,
            f"{self.key_prefix}:quota",
        )
        if not isinstance(result, int) or result < 0:
            raise ValueError("Invalid Redis diagnostic response")

    def quota(
        self, operation: str, task_id: str = "", snapshot: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        if operation not in _QUOTA_OPERATIONS:
            raise ValueError("Unknown protection operation")
        if not isinstance(task_id, str):
            raise ValueError("Invalid protection task id")
        now = self.clock()
        today = beijing_date(now)
        keep_from = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
        arguments: list[str | int] = [
            operation,
            task_id,
            today,
            keep_from,
            DAILY_SUCCESS_LIMIT,
            MAX_QUOTA_KEYS,
            _QUOTA_TTL,
            _MAX_SNAPSHOT_BYTES,
            _MAX_SNAPSHOT_MEMORY_BYTES,
        ]
        if operation == "merge" or snapshot is not None:
            normalized = self._validate_snapshot(snapshot if snapshot is not None else {})
            for item in normalized.items():
                arguments.extend(item)
        response = self._get_client().eval(_QUOTA_SCRIPT, 1, f"{self.key_prefix}:quota", *arguments)
        if not isinstance(response, list) or len(response) != 2:
            raise ValueError("Invalid Redis protection response")
        code, pairs = response
        if code == "daily":
            retry_after = seconds_until_midnight(now)
            raise MediaError(
                f"今日全站额度已用完，请在北京时间零点后重试（约 {retry_after} 秒）",
                "daily_quota",
                429,
                retry_after=retry_after,
            )
        if code == "conflict":
            raise MediaError("任务标识已使用", "input_conflict", 409)
        if code == "missing":
            raise MediaError("任务额度预占不存在", "task_missing", 409)
        if code == "full":
            raise MediaError("保护存储容量已满", "protection_unavailable", 503)
        if code != "ok" or not isinstance(pairs, list) or len(pairs) % 2:
            raise ValueError("Invalid Redis protection response")
        if len(pairs) > MAX_QUOTA_KEYS * 2:
            raise ValueError("Invalid Redis protection snapshot capacity")
        result: dict[str, str] = {}
        for index in range(0, len(pairs), 2):
            task, state = pairs[index : index + 2]
            if not isinstance(task, str) or task in result:
                raise ValueError("Invalid Redis protection snapshot")
            result[task] = state
        return self._validate_snapshot(result)

    @staticmethod
    def _validate_snapshot(snapshot: Mapping[str, str]) -> dict[str, str]:
        if len(snapshot) > MAX_QUOTA_KEYS:
            raise ValueError("Invalid protection snapshot capacity")
        result: dict[str, str] = {}
        total_bytes = 0
        for task_id, state in snapshot.items():
            if not isinstance(task_id, str) or not isinstance(state, str):
                raise ValueError("Invalid protection snapshot")
            if state != "pending":
                try:
                    valid = date.fromisoformat(state).isoformat() == state
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError("Invalid protection snapshot")
            total_bytes += len(task_id.encode("utf-8")) + len(state.encode("utf-8"))
            if total_bytes > _MAX_SNAPSHOT_BYTES:
                raise ValueError("Invalid protection snapshot size")
            result[task_id] = state
        return result

    def check_rate(
        self, groups: str | Sequence[str], identity: str, *, record: bool = True
    ) -> None:
        budgets = rate_budgets(groups, identity)
        if not budgets:
            return
        keys: list[str] = []
        arguments: list[str | float | int] = [self.clock(), uuid.uuid4().hex, int(record)]
        for bucket, bucket_identity, limit, window in budgets:
            digest = hashlib.sha256(bucket_identity.encode("utf-8")).hexdigest()
            keys.append(f"{self.key_prefix}:rate:{bucket}:{digest}")
            arguments.extend((limit, window))
        retry_after = self._get_client().eval(_RATE_SCRIPT, len(keys), *keys, *arguments)
        if not isinstance(retry_after, int) or retry_after < 0:
            raise ValueError("Invalid Redis protection rate response")
        if retry_after:
            raise MediaError(
                f"请求过于频繁，请 {retry_after} 秒后重试",
                "rate_limit",
                429,
                retry_after=retry_after,
            )

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None
