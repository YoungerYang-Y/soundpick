"""Redis atomic admission and bounded memory fallback behavior."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.errors import MediaError
from app.services.protection import DAILY_SUCCESS_LIMIT, ProtectionStore

BEIJING_OFFSET = 8 * 60 * 60


class Clock:
    def __init__(self, timestamp: float) -> None:
        self.timestamp = timestamp

    def __call__(self) -> float:
        return self.timestamp


def opened(url: str, clock: Clock) -> ProtectionStore:
    store = ProtectionStore(redis_url=url, clock=clock, retry_clock=clock)
    store.open()
    store.recover()
    return store


def test_reservations_count_successes_and_all_pending_across_beijing_midnight(redis_server):
    midnight = 1_735_689_600  # 2025-01-01 00:00:00 UTC.
    clock = Clock(midnight + 24 * 60 * 60 - BEIJING_OFFSET - 1)
    path = redis_server
    store = opened(path, clock)
    try:
        for index in range(DAILY_SUCCESS_LIMIT - 1):
            store.reserve(f"old-{index}")
            store.succeed(f"old-{index}")
        store.reserve("crossing")
        with pytest.raises(MediaError) as error:
            store.reserve("last-before-midnight")
        assert error.value.code == "daily_quota"
        assert error.value.status == 429
        assert error.value.retry_after == 1

        clock.timestamp += 1
        store.reserve("new-day-slot")
        store.succeed("crossing")
        store.succeed("new-day-slot")
        for index in range(8):
            store.reserve(f"new-day-{index}")
            store.succeed(f"new-day-{index}")
        with pytest.raises(MediaError) as next_error:
            store.reserve("next")
        assert next_error.value.code == "daily_quota"
        assert next_error.value.retry_after == 24 * 60 * 60
    finally:
        store.close()


def test_success_is_idempotent_and_successful_id_cannot_be_reused(redis_server):
    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    try:
        store.reserve("task")
        store.succeed("task")
        store.succeed("task")
        with pytest.raises(MediaError) as error:
            store.reserve("task")
        assert error.value.status == 409
        assert error.value.code == "input_conflict"
    finally:
        store.close()


def test_release_recover_and_reopen_preserve_success_but_clear_pending(redis_server):
    path = redis_server
    clock = Clock(1_735_689_600)
    first = opened(path, clock)
    first.reserve("kept")
    first.succeed("kept")
    first.reserve("released")
    first.release("released")
    first.reserve("orphan")
    first.close()

    second = opened(path, clock)
    try:
        second.reserve("another-pending")
        second.recover()
        second.reserve("after-recovery")
        with pytest.raises(MediaError) as error:
            second.reserve("kept")
        assert error.value.code == "input_conflict"
        second.release("after-recovery")
        second.release("another-pending")
        second.reserve("replacement")
    finally:
        second.close()


def test_success_without_pending_fails_closed(redis_server):
    store = opened(redis_server, Clock(1_735_689_600))
    try:
        with pytest.raises(MediaError) as error:
            store.succeed("missing")
        assert error.value.status == 409
    finally:
        store.close()


def test_independent_connections_serialize_the_last_daily_slot(redis_server):
    path = redis_server
    clock = Clock(1_735_689_600)
    seed = opened(path, clock)
    for index in range(DAILY_SUCCESS_LIMIT - 1):
        seed.reserve(f"done-{index}")
        seed.succeed(f"done-{index}")
    seed.close()

    def reserve(task_id: str) -> str:
        from app.services.redis_protection import RedisBackend

        backend = RedisBackend(
            redis_url=path, key_prefix="soundpick:{protection}", timeout_seconds=0.1, clock=clock
        )
        try:
            backend.quota("reserve", task_id)
            return "reserved"
        except MediaError as error:
            assert error.code == "daily_quota"
            return "full"
        finally:
            backend.close()

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(reserve, [f"race-{i}" for i in range(8)]))
    assert results.count("reserved") == 1


@pytest.mark.parametrize(
    ("group", "limit", "window"),
    [
        ("login", 10, 60),
        ("extract", 6, 60),
        ("upload", 6, 60),
        ("poll", 120, 60),
        ("download", 30, 60),
    ],
)
def test_rate_groups_enforce_their_configured_sliding_window(redis_server, group, limit, window):
    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    try:
        for _ in range(limit):
            store.check_rate(group, "hashed-client")
        with pytest.raises(MediaError) as error:
            store.check_rate(group, "hashed-client")
        assert error.value.code == "rate_limit"
        assert error.value.status == 429
        assert error.value.retry_after == window
        clock.timestamp += window
        store.check_rate(group, "hashed-client")
    finally:
        store.close()


def test_login_has_additional_long_window_and_retry_uses_max_remaining(redis_server):
    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    try:
        for _ in range(10):
            store.check_rate("login", "hashed-client")
        for _ in range(2):
            clock.timestamp += 60
            for _ in range(10):
                store.check_rate("login", "hashed-client")
        with pytest.raises(MediaError) as error:
            store.check_rate("login", "hashed-client")
        assert error.value.retry_after == 780
    finally:
        store.close()


def test_multi_group_check_is_atomic_and_uses_longest_wait(redis_server):
    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    try:
        for _ in range(10):
            store.check_rate("login", "hashed-client")
        with pytest.raises(MediaError) as error:
            store.check_rate(("extract", "login"), "hashed-client")
        assert error.value.retry_after == 60
        store.check_rate("extract", "hashed-client")
        store.check_rate("extract", "hashed-client")
        store.check_rate("extract", "hashed-client")
        store.check_rate("extract", "hashed-client")
        store.check_rate("extract", "hashed-client")
        store.check_rate("extract", "hashed-client")
        with pytest.raises(MediaError):
            store.check_rate("extract", "hashed-client")
    finally:
        store.close()


def test_full_multi_group_wait_is_max_of_each_bucket(redis_server):
    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    try:
        for _ in range(10):
            store.check_rate("login", "hashed-client")
        clock.timestamp += 20
        for _ in range(40):
            store.check_rate("login", f"other-client-{_}")
        with pytest.raises(MediaError) as error:
            store.check_rate(("login", "extract"), "hashed-client")
        assert error.value.retry_after == 40
    finally:
        store.close()


def test_global_hourly_attempt_budget_cannot_be_reset_by_new_ip_or_restart(redis_server):
    path = redis_server
    clock = Clock(1_735_689_600)
    store = opened(path, clock)
    for batch in range(5):
        for _ in range(6):
            store.check_rate("extract", f"client-{batch}")
        clock.timestamp += 60
    store.close()
    store = opened(path, clock)
    try:
        with pytest.raises(MediaError) as error:
            store.check_rate("extract", "new-client")
        assert error.value.retry_after == 3300
        clock.timestamp += 3300
        store.check_rate("extract", "new-client")
    finally:
        store.close()


def test_global_login_budget_counts_rotating_ip_addresses(redis_server):
    store = opened(redis_server, Clock(1_735_689_600))
    try:
        for index in range(60):
            store.check_rate("login", f"client-{index}")
        with pytest.raises(MediaError) as error:
            store.check_rate("login", "another-client")
        assert error.value.status == 429
        assert error.value.retry_after == 60
    finally:
        store.close()


def test_real_connection_outage_preserves_quota_and_recovers_after_cooldown(redis_controller):
    clock = Clock(1_735_689_600)
    store = opened(redis_controller.url, clock)
    try:
        for index in range(8):
            store.reserve(f"done-{index}")
            store.succeed(f"done-{index}")
        store.reserve("active")
        for _ in range(5):
            store.check_rate("extract", "owner")
        redis_controller.stop()
        store.succeed("active")
        assert store.mode == "memory"
        store.check_rate("extract", "owner")
        with pytest.raises(MediaError) as limited:
            store.check_rate("extract", "owner")
        assert limited.value.code == "rate_limit"
        store.reserve("failed")
        store.release("failed")
        store.reserve("last")
        with pytest.raises(MediaError) as full:
            store.reserve("extra")
        assert full.value.code == "daily_quota"
        redis_controller.start()  # New empty Redis: shadow must restore successful usage.
        store.succeed("last")
        assert store.mode == "memory"  # No connection attempt during the cooldown.
        clock.timestamp += 30
        with pytest.raises(MediaError):
            store.reserve("after-recovery")
        assert store.mode == "redis"
        assert len(store.memory.quota_snapshot()) == 10
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["reserve", "succeed", "release"])
def test_committed_write_with_lost_response_is_reconciled_idempotently(
    redis_server, monkeypatch, operation
):
    import redis

    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    if operation != "reserve":
        store.reserve("task")
    original = store._backend._get_client().eval

    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise redis.ConnectionError("simulated response loss")

    monkeypatch.setattr(store._backend._get_client(), "eval", lost_response)
    try:
        getattr(store, operation)("task")
        assert store.mode == "memory"
        monkeypatch.setattr(store._backend._get_client(), "eval", original)
        clock.timestamp += 30
        store.check_rate("poll", "owner")
        assert store.mode == "redis"
        snapshot = store.memory.quota_snapshot()
        if operation == "reserve":
            assert snapshot == {"task": "pending"}
        elif operation == "succeed":
            assert snapshot == {"task": "2025-01-01"}
        else:
            assert snapshot == {}
    finally:
        store.close()


def test_restart_while_redis_is_down_accepts_soft_quota_then_merges_remote_successes(
    redis_server, monkeypatch
):
    import redis

    from app.services.redis_protection import RedisBackend

    clock = Clock(1_735_689_600)
    first = opened(redis_server, clock)
    for index in range(10):
        first.reserve(f"old-{index}")
        first.succeed(f"old-{index}")
    first.close()
    original = RedisBackend.quota

    def unavailable(*args, **kwargs):
        raise redis.ConnectionError("simulated startup outage")

    monkeypatch.setattr(RedisBackend, "quota", unavailable)
    second = opened(redis_server, clock)
    try:
        second.reserve("offline")
        second.succeed("offline")
        assert second.mode == "memory"
        monkeypatch.setattr(RedisBackend, "quota", original)
        clock.timestamp += 30
        with pytest.raises(MediaError) as error:
            second.reserve("extra")
        assert error.value.code == "daily_quota"
        assert second.mode == "redis"
        assert len(second.memory.quota_snapshot()) == 11
    finally:
        second.close()


def test_circuit_breaker_limits_network_attempts_and_never_logs_credentials(caplog, monkeypatch):
    from app.services.redis_protection import RedisBackend

    clock = Clock(1_735_689_600)
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append(1)
        raise OSError("redis://secret-password@localhost")

    monkeypatch.setattr(RedisBackend, "quota", unavailable)
    store = opened("redis://secret-password@localhost", clock)
    for _ in range(10):
        store.check_rate("login", "owner")
    assert len(attempts) == 1
    assert "secret-password" not in caplog.text
    clock.timestamp += 30
    store.check_rate("poll", "owner")
    assert len(attempts) == 2
    store.close()


def test_redis_key_eviction_cannot_erase_known_successes_or_fail_an_active_task(redis_server):
    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    try:
        for index in range(9):
            store.reserve(f"done-{index}")
            store.succeed(f"done-{index}")
        store.reserve("active")
        # Delete only this isolated deployment's quota key, as an eviction would.
        store._backend._get_client().delete(f"{store.key_prefix}:quota")
        store.succeed("active")
        assert store.mode == "redis"
        with pytest.raises(MediaError) as error:
            store.reserve("extra")
        assert error.value.code == "daily_quota"
        assert len(store.memory.quota_snapshot()) == 10
    finally:
        store.close()


@pytest.mark.parametrize(
    ("group", "limit"),
    [
        ("login", 10),
        ("extract", 6),
        ("upload", 6),
        ("poll", 120),
        ("download", 30),
    ],
)
def test_redis_lua_itself_enforces_each_window(redis_server, group, limit):
    from app.services.redis_protection import RedisBackend

    clock = Clock(1_735_689_600)
    backend = RedisBackend(redis_server, clock=clock)
    try:
        for _ in range(limit):
            backend.check_rate(group, "owner")
        with pytest.raises(MediaError) as error:
            backend.check_rate(group, "owner")
        assert error.value.code == "rate_limit"
        assert error.value.retry_after == 60
        clock.timestamp += 60
        backend.check_rate(group, "owner")
    finally:
        backend.close()


def test_redis_multi_group_denial_does_not_consume_other_budget(redis_server):
    from app.services.redis_protection import RedisBackend

    backend = RedisBackend(redis_server)
    try:
        for _ in range(10):
            backend.check_rate("login", "owner")
        with pytest.raises(MediaError):
            backend.check_rate(("extract", "login"), "owner")
        for _ in range(6):
            backend.check_rate("extract", "owner")
        with pytest.raises(MediaError):
            backend.check_rate("extract", "owner")
    finally:
        backend.close()


def test_remote_rate_rejection_is_not_a_connection_failure(redis_server):
    clock = Clock(1_735_689_600)
    store = opened(redis_server, clock)
    try:
        for _ in range(10):
            store._backend.check_rate("login", "owner")
        with pytest.raises(MediaError) as error:
            store.check_rate("login", "owner")
        assert error.value.code == "rate_limit"
        assert store.mode == "redis"
        assert not store.memory._rates
    finally:
        store.close()


def test_namespaces_isolate_usage_and_client_has_bounded_timeouts_and_no_retries(redis_server):
    from app.services.redis_protection import RedisBackend

    clock = Clock(1_735_689_600)
    first = RedisBackend(
        redis_server, key_prefix="first:{protection}", clock=clock, timeout_seconds=0.1
    )
    second = RedisBackend(redis_server, key_prefix="second:{protection}", clock=clock)
    try:
        for index in range(10):
            first.quota("reserve", str(index))
            first.quota("succeed", str(index))
        second.quota("reserve", "own")
        for _ in range(10):
            first.check_rate("login", "owner")
        second.check_rate("login", "owner")
        kwargs = first._get_client().connection_pool.connection_kwargs
        assert kwargs["socket_timeout"] == kwargs["socket_connect_timeout"] == 0.1
        assert kwargs["retry"].get_retries() == 0
        assert kwargs["protocol"] == 2
        assert first._get_client().connection_pool.max_connections == 2
    finally:
        first.close()
        second.close()


def test_invalid_remote_state_falls_back_without_erasing_local_usage(redis_server):
    store = opened(redis_server, Clock(1_735_689_600))
    try:
        store.reserve("kept")
        store.succeed("kept")
        store._backend._get_client().hset(f"{store.key_prefix}:quota", "bad", "2025-99-99")
        store.reserve("offline")
        store.succeed("offline")
        assert store.mode == "memory"
        assert store.memory.quota_snapshot() == {
            "kept": "2025-01-01",
            "offline": "2025-01-01",
        }
    finally:
        store.close()


def test_remote_quota_rejection_rolls_back_local_reservation_and_keeps_redis_mode(redis_server):
    store = opened(redis_server, Clock(1_735_689_600))
    try:
        for index in range(10):
            store._backend.quota("reserve", str(index))
            store._backend.quota("succeed", str(index))
        with pytest.raises(MediaError) as error:
            store.reserve("extra")
        assert error.value.code == "daily_quota"
        assert store.mode == "redis"
        assert not store.memory.quota_snapshot()
    finally:
        store.close()


@pytest.mark.parametrize("large_field", [False, True], ids=["large-value", "large-field"])
def test_oversized_remote_quota_is_rejected_before_bulk_read_and_falls_back(
    redis_server, large_field
):
    import redis

    store = opened(redis_server, Clock(1_735_689_600))
    try:
        store.reserve("kept")
        store.succeed("kept")
        client = store._backend._get_client()
        oversized = "x" * (8 * 1024 * 1024)
        client.hset(
            f"{store.key_prefix}:quota",
            oversized if large_field else "bad",
            "pending" if large_field else oversized,
        )
        before = client.info("commandstats").get("cmdstat_hgetall", {}).get("calls", 0)
        with pytest.raises(redis.ResponseError):
            store._backend.quota("snapshot")
        after = client.info("commandstats").get("cmdstat_hgetall", {}).get("calls", 0)
        assert after == before, "Oversized quota must be rejected before copying it into Lua"
        store.reserve("offline")
        store.succeed("offline")
        assert store.mode == "memory"
        assert store.memory.quota_snapshot() == {
            "kept": "2025-01-01",
            "offline": "2025-01-01",
        }
    finally:
        store.close()


def test_quota_memory_precheck_allows_valid_payload_with_hash_overhead(redis_server):
    from app.services.memory_protection import MAX_QUOTA_KEYS
    from app.services.redis_protection import RedisBackend

    backend = RedisBackend(redis_server, clock=Clock(1_735_689_600))
    try:
        # Near the existing payload cap, with enough entries to use a hash table.
        snapshot = {
            f"{index:032x}".ljust(240, "a"): "2025-01-01" for index in range(MAX_QUOTA_KEYS)
        }
        key = f"{backend.key_prefix}:quota"
        client = backend._get_client()
        client.hset(key, mapping=snapshot)
        assert client.memory_usage(key, samples=0) > 1024 * 1024
        assert backend.quota("snapshot") == snapshot
    finally:
        backend.close()
