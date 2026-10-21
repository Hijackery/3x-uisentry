"""Проверки хранилища: метрики, состояния алертов, аудит."""

from __future__ import annotations

import time

from uisentry.models import SystemMetrics
from uisentry.storage import Storage


def make_metrics(cpu: float, mem: float, *, ts: float) -> SystemMetrics:
    return SystemMetrics(
        cpu_percent=cpu,
        mem_percent=mem,
        mem_total_bytes=1024**3,
        swap_percent=0.0,
        disk_percent=10.0,
        disk_free_bytes=10**9,
        load1=0.5,
        uptime_seconds=3600.0,
        tcp_connections=5,
        net_sent_bytes=1,
        net_recv_bytes=2,
        collected_at=ts,
    )


def test_sample_is_stored_and_pruned(storage: Storage) -> None:
    now = time.time()
    storage.record_sample(make_metrics(10.0, 20.0, ts=now - 7200))
    storage.record_sample(make_metrics(30.0, 40.0, ts=now))

    latest = storage.get_latest_sample()
    assert latest is not None
    assert latest["cpu"] == 30.0

    storage.prune_samples(now - 3600)
    rows = storage.get_series(0, limit=100)
    assert len(rows) == 1


def test_series_groups_by_hour(storage: Storage) -> None:
    base = 1_700_000_000
    hour = 3600
    for offset in range(3):
        storage.record_sample(make_metrics(20.0 + offset, 30.0, ts=base + offset * 60))
    storage.record_sample(make_metrics(90.0, 50.0, ts=base + hour))

    rows = storage.get_series(0, limit=10)
    assert len(rows) == 2
    assert rows[-1]["cpu"] == 90.0


def test_alert_state_is_created_and_updated(storage: Storage) -> None:
    assert storage.get_alert_state("cpu") is None
    storage.set_alert_state("cpu", "warning", changed_at=100.0)
    row = storage.get_alert_state("cpu")
    assert row is not None
    assert row["state"] == "warning"
    assert row["notified_at"] is None

    # Отметка «отправлено» ставится отдельно и не трогает состояние.
    storage.mark_alert_sent("cpu", notified_at=150.0)
    sent = storage.get_alert_state("cpu")
    assert sent is not None
    assert sent["state"] == "warning"
    assert sent["notified_at"] == 150.0

    # Новое состояние обязано сбросить отметку, иначе новое оповещение
    # подавится cooldown'ом от предыдущего.
    storage.set_alert_state("cpu", "critical", changed_at=200.0)
    changed = storage.get_alert_state("cpu")
    assert changed is not None
    assert changed["state"] == "critical"
    assert changed["notified_at"] is None
    assert "cpu" in storage.all_alert_keys()


def test_storage_is_thread_safe(storage: Storage) -> None:
    """Один sqlite3.Connection должен выдерживать параллельные записи."""
    import threading

    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            for step in range(25):
                storage.record_sample(
                    make_metrics(index, float(step), ts=time.time() + step)
                )
                storage.set_alert_state(f"thread:{index}", "ok")
                storage.mark_alert_sent(f"thread:{index}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert storage.count_samples() == 4 * 25


def test_audit_records_every_action(storage: Storage) -> None:
    storage.audit("restart_service", telegram_id=42, params={"args": ["x-ui"]}, ok=True)
    storage.audit("reboot", telegram_id=42, ok=False, result="timeout")

    rows = storage.recent_audit(10)
    assert len(rows) == 2
    assert rows[0]["action"] == "reboot"
    assert rows[0]["ok"] == 0
    assert rows[1]["params"] == '{"args": ["x-ui"]}'


def test_pending_jobs_survive_restart(storage: Storage) -> None:
    """Фоновое обновление должно дождаться отчёта даже после рестарта бота."""
    job_id = storage.add_pending_job("apt_upgrade", "upgrade", chat_id=1, user_id=42)
    rows = storage.list_pending_jobs()
    assert len(rows) == 1
    assert rows[0]["action"] == "apt_upgrade"
    assert rows[0]["kind"] == "upgrade"
    assert rows[0]["chat_id"] == 1

    storage.remove_pending_job(job_id)
    assert storage.list_pending_jobs() == []


def test_pending_jobs_migration_adds_message_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """База прошлой версии (без message_id) должна мигрировать, а не падать."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE pending_jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, "
        "kind TEXT NOT NULL, chat_id INTEGER NOT NULL, "
        "user_id INTEGER NOT NULL, created_at REAL NOT NULL)"
    )
    conn.commit()
    conn.close()

    storage = Storage(path)
    try:
        storage.add_pending_job("apt_upgrade", "upgrade", 1, 2, message_id=777)
        rows = storage.list_pending_jobs()
        assert rows[0]["message_id"] == 777
    finally:
        storage.close()


def test_elevation_roundtrip_and_expiry(storage: Storage) -> None:
    now = time.time()
    until = storage.set_elevation(42, 900, now=now)
    assert until == now + 900
    assert storage.elevation_until(42, now=now + 10) == until
    assert storage.elevation_until(42, now=now + 901) == 0.0
    storage.set_elevation(42, 60, now=now + 1000)
    storage.clear_elevation(42)
    assert storage.elevation_until(42, now=now + 1001) == 0.0
