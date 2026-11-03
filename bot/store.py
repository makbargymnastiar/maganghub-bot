"""Akses DB tingkat aplikasi: user, status harian, flag reminder, event log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import aiosqlite

from .models import User
from .settings import Settings

KIND_STATUS = "status"
KIND_REMINDER = "reminder"
KIND_ERROR = "error"
KIND_TOKEN = "token"
KIND_INFO = "info"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _row_to_user(row: aiosqlite.Row) -> User:
    saved_at = row["token_saved_at"]
    refresh_at = row["refresh_saved_at"] if "refresh_saved_at" in row.keys() else ""
    return User(
        tg_chat_id=row["tg_chat_id"],
        participant_id=row["participant_id"],
        name=row["name"],
        bearer_token=row["bearer_token"],
        token_fingerprint=row["token_fingerprint"],
        token_saved_at=datetime.fromisoformat(saved_at) if saved_at else None,
        token_ttl_seconds=row["token_ttl"],
        token_invalid=bool(row["token_invalid"]),
        active=bool(row["active"]),
        reminders_on=bool(row["reminders_on"]),
        refresh_cookie=(row["refresh_cookie"] if "refresh_cookie" in row.keys() else "") or "",
        refresh_saved_at=datetime.fromisoformat(refresh_at) if refresh_at else None,
    )


async def ensure_user(db: aiosqlite.Connection, settings: Settings, name: str = "") -> User:
    """Seed satu baris user dari `config.py`; token lama di DB tidak ditimpa."""
    now = _iso(settings.now())
    await db.execute(
        """INSERT INTO users (tg_chat_id, participant_id, name, bearer_token, refresh_cookie, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(tg_chat_id) DO UPDATE SET
               participant_id = excluded.participant_id,
               refresh_cookie = CASE WHEN users.refresh_cookie = '' THEN excluded.refresh_cookie
                                     ELSE users.refresh_cookie END""",
        (
            settings.telegram_chat_id,
            settings.participant_id,
            name,
            settings.bearer_token,
            settings.refresh_cookie,
            now,
        ),
    )
    if name:
        await db.execute(
            "UPDATE users SET name = ? WHERE tg_chat_id = ? AND name = ''",
            (name, settings.telegram_chat_id),
        )
    await db.commit()
    user = await get_user(db, settings.telegram_chat_id)
    assert user is not None
    return user


async def get_user(db: aiosqlite.Connection, chat_id: str) -> User | None:
    async with db.execute("SELECT * FROM users WHERE tg_chat_id = ?", (str(chat_id),)) as cur:
        row = await cur.fetchone()
    return _row_to_user(row) if row else None


async def list_users(db: aiosqlite.Connection, active_only: bool = True) -> list[User]:
    sql = "SELECT * FROM users"
    if active_only:
        sql += " WHERE active = 1"
    async with db.execute(sql + " ORDER BY created_at") as cur:
        rows = await cur.fetchall()
    return [_row_to_user(r) for r in rows]


async def update_token(
    db: aiosqlite.Connection,
    user_id: str,
    token: str,
    fingerprint: str,
    ttl_seconds: int,
    at: datetime,
) -> None:
    await db.execute(
        """UPDATE users SET bearer_token = ?, token_fingerprint = ?, token_ttl = ?,
                            token_saved_at = ?, token_invalid = 0
           WHERE tg_chat_id = ?""",
        (token, fingerprint, ttl_seconds, _iso(at), user_id),
    )
    await db.commit()


async def set_token_invalid(db: aiosqlite.Connection, user_id: str, invalid: bool) -> None:
    await db.execute(
        "UPDATE users SET token_invalid = ? WHERE tg_chat_id = ?", (int(invalid), user_id)
    )
    await db.commit()


async def update_refresh_cookie(
    db: aiosqlite.Connection, user_id: str, cookie: str, at: datetime
) -> None:
    """Simpan cookie refresh (`nama=nilai`); nilai kosong berarti matikan auto-refresh."""
    await db.execute(
        "UPDATE users SET refresh_cookie = ?, refresh_saved_at = ? WHERE tg_chat_id = ?",
        (cookie or "", _iso(at), user_id),
    )
    await db.commit()


async def set_reminders_on(db: aiosqlite.Connection, user_id: str, on: bool) -> None:
    await db.execute("UPDATE users SET reminders_on = ? WHERE tg_chat_id = ?", (int(on), user_id))
    await db.commit()


# --- status harian -------------------------------------------------------------

async def get_day_state(
    db: aiosqlite.Connection, user_id: str, log_date: str
) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT * FROM day_state WHERE user_id = ? AND log_date = ?", (user_id, log_date)
    ) as cur:
        return await cur.fetchone()


async def list_day_states(
    db: aiosqlite.Connection, user_id: str, since_date: str, limit: int = 60
) -> list[aiosqlite.Row]:
    async with db.execute(
        """SELECT * FROM day_state WHERE user_id = ? AND log_date >= ?
           ORDER BY log_date DESC LIMIT ?""",
        (user_id, since_date, limit),
    ) as cur:
        return list(await cur.fetchall())


async def upsert_day_state(
    db: aiosqlite.Connection,
    user_id: str,
    log_date: str,
    label: str,
    claims: dict[str, Any],
    at: datetime,
) -> str | None:
    """Simpan status; kembalikan label sebelumnya (None bila belum pernah terlihat)."""
    previous = None
    async with db.execute(
        "SELECT label FROM day_state WHERE user_id = ? AND log_date = ?", (user_id, log_date)
    ) as cur:
        row = await cur.fetchone()
        if row:
            previous = row["label"]

    now = _iso(at)
    if previous is None:
        await db.execute(
            """INSERT INTO day_state (user_id, log_date, attendance_status, approval_status,
                                      log_state, label, log_id, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                log_date,
                claims.get("attendance_status"),
                claims.get("approval_status"),
                claims.get("log_state"),
                label,
                claims.get("log_id"),
                now,
                now,
            ),
        )
    else:
        await db.execute(
            """UPDATE day_state SET attendance_status = ?, approval_status = ?, log_state = ?,
                                     label = ?, log_id = ?, last_seen_at = ?
               WHERE user_id = ? AND log_date = ?""",
            (
                claims.get("attendance_status"),
                claims.get("approval_status"),
                claims.get("log_state"),
                label,
                claims.get("log_id"),
                now,
                user_id,
                log_date,
            ),
        )
    await db.commit()
    return previous


async def count_day_states(db: aiosqlite.Connection, user_id: str) -> int:
    async with db.execute("SELECT COUNT(*) AS n FROM day_state WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


# --- flag reminder ------------------------------------------------------------

async def flag_exists(db: aiosqlite.Connection, user_id: str, log_date: str, flag: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM notify_flags WHERE user_id = ? AND log_date = ? AND flag = ?",
        (user_id, log_date, flag),
    ) as cur:
        return await cur.fetchone() is not None


async def set_flag(db: aiosqlite.Connection, user_id: str, log_date: str, flag: str, at: datetime) -> None:
    await db.execute(
        """INSERT OR IGNORE INTO notify_flags (user_id, log_date, flag, set_at)
           VALUES (?, ?, ?, ?)""",
        (user_id, log_date, flag, _iso(at)),
    )
    await db.commit()


# --- event log ----------------------------------------------------------------

async def add_event(
    db: aiosqlite.Connection,
    user_id: str,
    log_date: str,
    from_label: str,
    to_label: str,
    kind: str,
    message: str,
    at: datetime,
) -> None:
    await db.execute(
        """INSERT INTO event_log (user_id, log_date, from_label, to_label, kind, message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, log_date, from_label, to_label, kind, message, _iso(at)),
    )
    await db.commit()


async def recent_events(
    db: aiosqlite.Connection, user_id: str, limit: int = 10
) -> list[aiosqlite.Row]:
    async with db.execute(
        """SELECT * FROM event_log WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
        (user_id, limit),
    ) as cur:
        return list(await cur.fetchall())


# --- settings KV --------------------------------------------------------------

async def get_setting(db: aiosqlite.Connection, key: str, default: str = "") -> str:
    async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(db: aiosqlite.Connection, key: str, value: str, at: datetime) -> None:
    await db.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, value, _iso(at)),
    )
    await db.commit()
