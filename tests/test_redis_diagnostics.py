"""Actionable Redis diagnostics preserve memory fallback and hide credentials."""

import sys

import pytest

from app.services.protection import ProtectionStore


@pytest.mark.parametrize(
    ("exception_name", "reason"),
    [
        ("TimeoutError", "timeout"),
        ("AuthenticationError", "authentication"),
        ("AuthorizationError", "permission"),
        ("NoPermissionError", "permission"),
        ("ConnectionError", "connection"),
        ("ResponseError", "redis_error"),
    ],
)
def test_failure_logs_identify_cause_without_changing_fallback_or_cooldown(
    monkeypatch, caplog, exception_name, reason
):
    import redis

    attempts = []
    clock = [1_735_689_600.0]

    def unavailable(*args, **kwargs):
        attempts.append(1)
        raise getattr(redis.exceptions, exception_name)("redis://:diagnostic-secret@localhost")

    monkeypatch.setattr(redis.Redis, "from_url", unavailable)
    store = ProtectionStore(
        redis_url="redis://:diagnostic-secret@localhost",
        clock=lambda: clock[0],
        retry_clock=lambda: clock[0],
    )
    store.open()
    try:
        store.recover()
        store.reserve("completed")
        store.succeed("completed")
        store.check_rate("extract", "owner")
        assert store.mode == "memory"
        assert store.memory.quota_snapshot() == {"completed": "2025-01-01"}
        assert len(attempts) == 1
        clock[0] += 30
        store.check_rate("poll", "owner")
        assert len(attempts) == 2
        assert f"reason={reason}" in caplog.text
        assert "30" in caplog.text
        assert "diagnostic-secret" not in caplog.text
        assert "redis://" not in caplog.text
    finally:
        store.close()


def test_missing_client_is_distinguished_while_memory_protection_stays_available(
    monkeypatch, caplog
):
    monkeypatch.setitem(sys.modules, "redis", None)
    store = ProtectionStore(redis_url="redis://:diagnostic-secret@localhost")
    store.open()
    try:
        store.recover()
        store.reserve("completed")
        store.succeed("completed")
        assert store.mode == "memory"
        assert len(store.memory.quota_snapshot()) == 1
        assert "reason=client_unavailable" in caplog.text
        assert "diagnostic-secret" not in caplog.text
    finally:
        store.close()
