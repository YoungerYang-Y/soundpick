"""Protection backend changes emit bounded, credential-free events."""

import logging

from app.services.protection import ProtectionStore


def test_memory_mode_logs_once_without_redis_configuration(caplog):
    store = ProtectionStore(redis_url="")
    store.open()
    try:
        with caplog.at_level(logging.INFO, logger="app.services.protection"):
            store.recover()
            store.reserve("task-one")
            store.reserve("task-two")
        events = [record for record in caplog.records if record.msg == "protection.memory"]
        assert len(events) == 1
        assert events[0].backend == "memory"
        assert events[0].error_category == "redis_not_configured"
    finally:
        store.close()


def test_redis_degrade_retry_and_reconnect_emit_fixed_safe_events(redis_controller, caplog):
    retry_clock = [0.0]
    store = ProtectionStore(
        redis_url=redis_controller.url,
        retry_seconds=5,
        retry_clock=lambda: retry_clock[0],
    )
    store.open()
    try:
        with caplog.at_level(logging.INFO, logger="app.services.protection"):
            store.recover()
            assert store.mode == "redis"
            redis_controller.stop()
            store.reserve("task-one")
            store.reserve("task-two")
            assert store.mode == "memory"
            retry_clock[0] = 5.0
            redis_controller.start()
            store.reserve("task-three")

        degraded = [
            record for record in caplog.records if record.msg == "protection.redis_degraded"
        ]
        connected = [
            record for record in caplog.records if record.msg == "protection.redis_connected"
        ]
        assert len(degraded) == 1
        assert degraded[0].backend == "memory"
        assert degraded[0].error_category == "connection"
        assert degraded[0].retry_after_seconds == 5
        assert len(connected) == 2
        assert all(record.backend == "redis" for record in connected)
        assert [record.previous_backend for record in connected] == ["memory", "memory"]
        assert store.mode == "redis"
    finally:
        store.close()
