"""Хранилище состояния: метрики, состояния алертов, журнал действий."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from .models import SystemMetrics

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL PRIMARY KEY,
    cpu REAL NOT NULL,
    mem REAL NOT NULL,
    swap REAL NOT NULL,
    disk REAL NOT NULL,
    load1 REAL NOT NULL,
    tcp INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS alert_state (
    key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    changed_at REAL NOT NULL,
    notified_at REAL
);

CREATE TABLE IF NOT EXISTS alert_mutes (
    key TEXT PRIMARY KEY,
    until REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    telegram_id INTEGER,
    action TEXT NOT NULL,
    params TEXT,
    ok INTEGER NOT NULL,
    result TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


class StorageError(RuntimeError):
    """База повреждена или недоступна: бот не должен стартовать вслепую."""


class Storage:
    """Потокобезопасная обёртка над SQLite.

    Методы синхронные, но короткие; из async-кода вызываются через
    ``asyncio.to_thread``. Поэтому соединение создаётся с
    ``check_same_thread=False``, а обращения сериализуются блокировкой —
    это безопасно в пределах одного процесса.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
            # Даём другим читателям/писателям шанс освободить базу: без этого
            # параллельный доступ падал с «database is locked».
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self.conn.executescript(SCHEMA)
        except sqlite3.DatabaseError as exc:
            self.conn.close()
            raise StorageError(
                f"База {self.path} повреждена или недоступна: {exc}. "
                "Перенесите или удалите файл — бот создаст новую "
                "(история метрик и журнал действий будут потеряны)."
            ) from exc
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        """База содержит журнал действий — закрываем её от других пользователей.

        umask процесса обычно уже 0077, но файл мог остаться от прежней
        установки с более широкими правами: приводим его к 0600 явно.
        """
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{suffix}")
            with suppress(OSError):
                if candidate.exists():
                    candidate.chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # --------------------------------------------------------------- samples

    def record_sample(self, metrics: SystemMetrics) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO samples (ts, cpu, mem, swap, disk, load1, tcp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    metrics.collected_at,
                    metrics.cpu_percent,
                    metrics.mem_percent,
                    metrics.swap_percent,
                    metrics.disk_percent,
                    metrics.load1,
                    metrics.tcp_connections,
                ),
            )

    def prune_samples(self, older_than: float) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM samples WHERE ts < ?", (older_than,))

    def get_series(self, since: float, limit: int = 24) -> list[sqlite3.Row]:
        with self._lock:
            rows = self.conn.execute(
                """
                -- CAST обязателен: (ts / 3600) * 3600 на REAL даёт
                -- «почти одинаковые» границы часов из-за ошибок float,
                -- и группировка схлопывается в по одному ряду на сэмпл.
                SELECT CAST(ts AS INTEGER) / 3600 * 3600 AS bucket,
                       AVG(cpu) AS cpu, AVG(mem) AS mem, AVG(disk) AS disk,
                       MAX(load1) AS load1, COUNT(*) AS samples
                FROM samples
                WHERE ts >= ?
                GROUP BY bucket
                ORDER BY bucket DESC
                LIMIT ?
                """,
                (since, limit),
            ).fetchall()
        return list(reversed(rows))

    def count_samples(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM samples").fetchone()
            return int(row["n"]) if row else 0

    def get_latest_sample(self) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self.conn.execute(
                "SELECT * FROM samples ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            return row

    # ----------------------------------------------------------- alert state

    def get_alert_state(self, key: str) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self.conn.execute(
                "SELECT * FROM alert_state WHERE key = ?", (key,)
            ).fetchone()
            return row

    def set_alert_state(
        self,
        key: str,
        state: str,
        *,
        changed_at: float | None = None,
    ) -> None:
        """Записывает новое состояние правила и сбрасывает отметку «отправлено».

        Сброс обязателен: пока сообщение не доставлено, правило должно
        повторяться, а cooldown считаться от последней успешной отправки.
        """
        now = changed_at if changed_at is not None else time.time()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO alert_state (key, state, changed_at, notified_at)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(key) DO UPDATE SET
                    state = excluded.state,
                    changed_at = excluded.changed_at,
                    notified_at = NULL
                """,
                (key, state, now),
            )

    def mark_alert_sent(self, key: str, *, notified_at: float | None = None) -> None:
        """Отмечает, что сообщение по ключу доставлено."""
        now = notified_at if notified_at is not None else time.time()
        with self._lock:
            self.conn.execute(
                "UPDATE alert_state SET notified_at = ? WHERE key = ?",
                (now, key),
            )

    def all_alert_keys(self) -> list[str]:
        with self._lock:
            return [
                row["key"]
                for row in self.conn.execute("SELECT key FROM alert_state").fetchall()
            ]

    # ----------------------------------------------------------------- audit

    def audit(
        self,
        action: str,
        *,
        telegram_id: int | None,
        params: dict[str, Any] | None = None,
        ok: bool = True,
        result: str | None = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO audit_log (ts, telegram_id, action, params, ok, result) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    telegram_id,
                    action,
                    json.dumps(params, ensure_ascii=False) if params else None,
                    int(ok),
                    result,
                ),
            )

    def recent_audit(self, limit: int = 15, *, only: str = "") -> list[sqlite3.Row]:
        """Последние записи журнала.

        ``only``: пусто — все, ``denied`` — только отказы, ``actions`` — только
        действия. Разбор инцидента ночью начинается с фильтра, а не с чтения
        всего подряд.
        """
        query = "SELECT * FROM audit_log"
        if only == "denied":
            query += " WHERE action = 'denied'"
        elif only == "actions":
            query += " WHERE action != 'denied'"
        query += " ORDER BY ts DESC LIMIT ?"
        with self._lock:
            return list(self.conn.execute(query, (limit,)).fetchall())

    def mute(self, key: str, seconds: float) -> float:
        """«Знаю, до утра»: заглушить алерт по ключу, вернуть время окончания."""
        until = time.time() + seconds
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO alert_mutes (key, until) VALUES (?, ?)",
                (key, until),
            )
        return until

    def is_muted(self, key: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT until FROM alert_mutes WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return False
            if float(row["until"]) <= time.time():
                self.conn.execute("DELETE FROM alert_mutes WHERE key = ?", (key,))
                return False
            return True

    def prune_mutes(self) -> None:
        with self._lock:
            self.conn.execute(
                "DELETE FROM alert_mutes WHERE until <= ?", (time.time(),)
            )
