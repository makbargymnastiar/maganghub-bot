"""Kesehatan token sesi monev.

Realita dari hasil investigasi HAR + tes live:
- JWT tidak punya klaim `exp`/`iat`, hanya `ttl` (21600 dtk = 6 jam), jadi umur
  diperkirakan dari kapan token disimpan.
- Sesi lama dibatalkan begitu ada login baru (token berumur 2 jam sudah 401),
  sehingga kegagalan harus ditangani sebagai "token perlu disinkron ulang",
  bukan "coba lagi nanti".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from .. import store
from ..models import User
from ..settings import Settings, mask_token
from ..tg import format as fmt

log = logging.getLogger("bot.token")

WARNING_LEAD_MINUTES = 30
FLAG_TOKEN_WARNING = "token_warning"
FLAG_TOKEN_ALERT = "token_alert"


def minutes_left(user: User, now: datetime) -> int | None:
    """Perkiraan sisa umur token; None bila waktu penyimpanan tidak diketahui."""
    if user.token_saved_at is None:
        return None
    deadline = user.token_saved_at + timedelta(seconds=user.token_ttl_seconds)
    return int((deadline - now).total_seconds() // 60)


async def run_token_health(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    notify: Any,
    *,
    now: datetime | None = None,
) -> bool:
    """Peringatan H-30 menit, maksimal satu kali per hari."""
    now = now or settings.now()
    if user.token_invalid:
        return False
    left = minutes_left(user, now)
    if left is None or left > WARNING_LEAD_MINUTES:
        return False

    today = now.date().isoformat()
    if await store.flag_exists(db, user.tg_chat_id, today, FLAG_TOKEN_WARNING):
        return False

    await store.set_flag(db, user.tg_chat_id, today, FLAG_TOKEN_WARNING, now)
    await notify.send_message(user.tg_chat_id, fmt.token_warning_message(max(left, 0)))
    await store.add_event(
        db, user.tg_chat_id, today, "", "", store.KIND_TOKEN, f"sisa umur token {left} menit", now
    )
    return True


async def mark_token_invalid(
    db: aiosqlite.Connection,
    user: User,
    notify: Any,
    reason: str,
    now: datetime,
) -> None:
    """Tandai token mati + minta token baru, hanya sekali per hari (anti spam)."""
    already = user.token_invalid
    await store.set_token_invalid(db, user.tg_chat_id, True)
    if already:
        return

    today = now.date().isoformat()
    if await store.flag_exists(db, user.tg_chat_id, today, FLAG_TOKEN_ALERT):
        return
    await store.set_flag(db, user.tg_chat_id, today, FLAG_TOKEN_ALERT, now)
    await notify.send_message(user.tg_chat_id, fmt.token_expired_message(reason))
    await store.add_event(db, user.tg_chat_id, today, "", "", store.KIND_TOKEN, reason, now)
    log.warning("token user %s invalid: %s", mask_token(user.bearer_token), reason)


async def mark_token_valid(db: aiosqlite.Connection, user: User, now: datetime) -> None:
    if user.token_invalid:
        await store.set_token_invalid(db, user.tg_chat_id, False)
        await store.add_event(
            db, user.tg_chat_id, now.date().isoformat(), "", "", store.KIND_TOKEN, "token pulih", now
        )
