"""Behavior of the process-local protection fallback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.errors import MediaError
from app.services.memory_protection import (
    DAILY_SUCCESS_LIMIT,
    MemoryProtection,
    beijing_date,
    rate_budgets,
    seconds_until_midnight,
)

BEIJING_OFFSET = 8 * 60 * 60


class Clock:
    def __init__(self, timestamp: float) -> None:
        self.timestamp = timestamp

    def __call__(self) -> float:
        return self.timestamp


def test_helpers_apply_beijing_date_and_budgets():
    timestamp = 1_735_689_600
    assert beijing_date(timestamp) == "2025-01-01"
    assert seconds_until_midnight(timestamp) == 24 * 60 * 60 - BEIJING_OFFSET
    assert seconds_until_midnight(timestamp + 24 * 60 * 60 - BEIJING_OFFSET - 0.1) == 1
    assert rate_budgets("extract", "client") == [
        ("extract:ip:60", "client", 6, 60),
        ("extract:global:60", "__soundpick_global__", 20, 60),
        ("extract:global:3600", "__soundpick_global__", 30, 3600),
    ]
    assert len(rate_budgets(("login", "upload"), "client")) == 6


def test_pending_crosses_midnight_and_success_counts_on_completion_day():
    midnight = 1_735_689_600
    clock = Clock(midnight + 24 * 60 * 60 - BEIJING_OFFSET - 1)
    protection = MemoryProtection(clock=clock)
    for index in range(DAILY_SUCCESS_LIMIT - 1):
        protection.reserve(f"done-{index}")
        protection.succeed(f"done-{index}")
    protection.reserve("crossing")
    with pytest.raises(MediaError) as full:
        protection.reserve("last-before-midnight")
    assert full.value.code == "daily_quota"
    assert full.value.retry_after == 1

    clock.timestamp += 1
    protection.reserve("new-day")
    protection.succeed("crossing")
    protection.succeed("new-day")
    for index in range(8):
        protection.reserve(f"today-{index}")
        protection.succeed(f"today-{index}")
    with pytest.raises(MediaError) as next_full:
        protection.reserve("next")
    assert next_full.value.retry_after == 24 * 60 * 60


def test_quota_transitions_are_idempotent_and_failed_work_releases_slot():
    protection = MemoryProtection(clock=Clock(1_735_689_600))
    protection.reserve("successful")
    protection.succeed("successful")
    protection.succeed("successful")
    with pytest.raises(MediaError) as reused:
        protection.reserve("successful")
    assert reused.value.code == "input_conflict"

    protection.reserve("failed")
    protection.release("failed")
    protection.release("missing")
    protection.reserve("replacement")
    with pytest.raises(MediaError) as missing:
        protection.succeed("missing")
    assert missing.value.code == "task_missing"

    snapshot = protection.quota_snapshot()
    snapshot.clear()
    assert protection.quota_snapshot() == {"successful": "2025-01-01", "replacement": "pending"}
    protection.recover()
    assert protection.quota_snapshot() == {"successful": "2025-01-01"}


def test_quota_snapshot_prunes_successes_older_than_seven_days():
    clock = Clock(1_735_689_600)
    protection = MemoryProtection(clock=clock)
    protection.set_quota({"old": "2024-12-24", "kept": "2024-12-25", "pending": "pending"})
    assert protection.quota_snapshot() == {"kept": "2024-12-25", "pending": "pending"}


def test_concurrent_reservations_serialize_the_last_daily_slot():
    protection = MemoryProtection(clock=Clock(1_735_689_600))
    for index in range(DAILY_SUCCESS_LIMIT - 1):
        protection.reserve(f"done-{index}")
        protection.succeed(f"done-{index}")

    def reserve(task_id: str) -> str:
        try:
            protection.reserve(task_id)
            return "reserved"
        except MediaError as error:
            assert error.code == "daily_quota"
            return "full"

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(reserve, [f"race-{index}" for index in range(8)]))
    assert results.count("reserved") == 1


def test_global_rate_limits_apply_across_identities_and_long_windows():
    clock = Clock(1_735_689_600)
    protection = MemoryProtection(clock=clock)
    for batch in range(5):
        for index in range(6):
            protection.check_rate("extract", f"client-{batch}-{index}")
        clock.timestamp += 60
    with pytest.raises(MediaError) as exhausted:
        protection.check_rate("extract", "new-client")
    assert exhausted.value.code == "rate_limit"
    assert exhausted.value.retry_after == 3300

    login = MemoryProtection(clock=Clock(1_735_689_600))
    for index in range(60):
        login.check_rate("login", f"client-{index}")
    with pytest.raises(MediaError) as global_limit:
        login.check_rate("login", "another-client")
    assert global_limit.value.retry_after == 60


def test_multi_group_checks_are_atomic_and_record_false_does_not_consume():
    clock = Clock(1_735_689_600)
    protection = MemoryProtection(clock=clock)
    for _ in range(10):
        protection.check_rate("login", "client")
    with pytest.raises(MediaError):
        protection.check_rate(("extract", "login"), "client")
    for _ in range(6):
        protection.check_rate("extract", "client")
    with pytest.raises(MediaError):
        protection.check_rate("extract", "client")

    probe = MemoryProtection(clock=clock)
    for _ in range(20):
        probe.check_rate("upload", "client", record=False)
    for _ in range(6):
        probe.check_rate("upload", "client")
    with pytest.raises(MediaError):
        probe.check_rate("upload", "client")


def test_sliding_window_uses_actual_seconds_and_ceil_retry():
    clock = Clock(1_735_689_600)
    protection = MemoryProtection(clock=clock)
    for _ in range(6):
        protection.check_rate("extract", "client")
    clock.timestamp += 10.2
    with pytest.raises(MediaError) as limited:
        protection.check_rate("extract", "client")
    assert limited.value.retry_after == 50
    clock.timestamp += 49.8
    protection.check_rate("extract", "client")


def test_rate_key_capacity_never_evicts_active_keys_and_cleans_expired_keys():
    clock = Clock(1_735_689_600)
    protection = MemoryProtection(clock=clock, max_rate_keys=2)
    protection.check_rate("poll", "client-a")
    with pytest.raises(MediaError) as full:
        protection.check_rate("poll", "client-b")
    assert full.value.code == "rate_limit"
    assert full.value.retry_after == 60

    clock.timestamp += 61
    protection.check_rate("poll", "client-b")


def test_record_false_checks_full_capacity_without_creating_buckets():
    protection = MemoryProtection(clock=Clock(1_735_689_600), max_rate_keys=2)
    protection.check_rate("poll", "client-a")

    for _ in range(2):
        with pytest.raises(MediaError) as full:
            protection.check_rate("poll", "client-b", record=False)
        assert full.value.code == "rate_limit"
        assert full.value.retry_after == 60

    protection.check_rate("poll", "client-a", record=False)


def test_unknown_group_is_rejected_without_recording():
    protection = MemoryProtection(clock=Clock(1_735_689_600))
    with pytest.raises(ValueError):
        protection.check_rate("unknown", "client")
