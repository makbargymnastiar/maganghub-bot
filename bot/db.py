"""Lapisan SQLite (aiosqlite): koneksi, skema, dan migrasi ringan.

`state.json` pada versi lama diganti DB ini supaya:
- status terakhir tidak hilang/rusak saat proses mati di tengah tulis,
- perubahan status punya riwayat (`event_log`),
- kolom `user_id` sudah ada sejak awal -> upgrade multi-user tanpa migrasi.
"""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_chat_id        TEXT PRIMARY KEY,
    participant_id    TEXT NOT NULL,
    name              TEXT NOT NULL DEFAULT '',
    bearer_token      TEXT NOT NULL DEFAULT '',
    token_fingerprint TEXT NOT NULL DEFAULT '',
    token_saved_at    TEXT NOT NULL DEFAULT '',
    token_ttl         INTEGER NOT NULL DEFAULT 21600,
    token_invalid     INTEGER NOT NULL DEFAULT 0,
    refresh_cookie    TEXT NOT NULL DEFAULT '',
    refresh_saved_at  TEXT NOT NULL DEFAULT '',
    active            INTEGER NOT NULL DEFAULT 1,
    reminders_on      INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_state (
    user_id           TEXT NOT NULL,
    log_date          TEXT NOT NULL,
    attendance_status TEXT,
    approval_status   TEXT,
    log_state         TEXT,
    label             TEXT NOT NULL,
    log_id            TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, log_date)
);

CREATE TABLE IF NOT EXISTS notify_flags (
    user_id TEXT NOT NULL,
    log_date TEXT NOT NULL,
    flag    TEXT NOT NULL,
    set_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, log_date, flag)
);

CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    log_date    TEXT NOT NULL DEFAULT '',
    from_label  TEXT NOT NULL DEFAULT '',
    to_label    TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'status',
    message     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_user_time ON event_log (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Kolom yang baru ada sejak versi "auto-refresh token". Ditambahkan sendiri kalau
# DB lama belum punya, supaya pengguna tidak perlu menghapus data/bot.db.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("users", "refresh_cookie", "TEXT NOT NULL DEFAULT ''"),
    ("users", "refresh_saved_at", "TEXT NOT NULL DEFAULT ''"),
    # Multi-user: status registrasi per pengguna ('pending' | 'active').
    ("users", "status", "TEXT NOT NULL DEFAULT 'pending'"),
]


async def _migrate(db: aiosqlite.Connection) -> None:
    for table, column, ddl in MIGRATIONS:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


async def connect(db_path: str | os.PathLike[str]) -> aiosqlite.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(SCHEMA)
    await _migrate(db)
    await db.commit()
    return db
