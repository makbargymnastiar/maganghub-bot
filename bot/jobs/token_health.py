"""Kesehatan token sesi monev.

Realita dari hasil investigasi HAR + tes live:
- JWT tidak punya klaim `exp`/`iat`, hanya `ttl` (21600 dtk = 6 jam), jadi umur
  diperkirakan dari kapan token disimpan.
- Sesi lama dibatalkan begitu ada login baru (token berumur 2 jam sudah 401),
  sehingga kegagalan harus ditangani sebagai "token perlu disinkron ulang",
  bukan "coba lagi nanti".
- Portal punya `POST /api/v1/auth/refresh` yang memberi access_token baru selama
  cookie httpOnly `monev_refresh_token` masih hidup. Karena itu token tidak perlu
  disalin ulang tiap 6 jam: `keepalive()` di bawah yang mengurusnya.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from .. import store
from ..api import endpoints
from ..api.client import MonevClient, MonevError, TokenRejected
from ..api.jwt_utils import fingerprint, ttl_seconds
from ..models import User
from ..settings import Settings, mask_token, normalize_cookie
from ..tg import format as fmt

log = logging.getLogger("bot.token")

WARNING_LEAD_MINUTES = 30
FLAG_TOKEN_WARNING = "token_warning"
FLAG_TOKEN_ALERT = "token_alert"
FLAG_REFRESH_FAILED = "refresh_failed"


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


async def keepalive(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    client: MonevClient,
    notify: Any,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> User | None:
    """Perpanjang access_token otomatis lewat cookie refresh.

    Dipanggil dua arah: terjadwal (saat sisa umur token tinggal `lead` menit) dan
    reaktif (`force=True`, setelah API membalas 401). Nilai None berarti tidak ada
    yang berubah: belum ada cookie, belum waktunya, atau refresh ditolak server.
    """
    now = now or settings.now()
    if not user.can_auto_refresh:
        return None
    if not force:
        left = minutes_left(user, now)
        if left is not None and left > settings.token_refresh_lead_minutes:
            return None

    build_id = await endpoints.fetch_frontend_build_id(client, settings.web_url)
    client.set_refresh_cookie(user.refresh_cookie)
    today = now.date().isoformat()
    try:
        token, rotated = await endpoints.refresh_access_token(client, build_id=build_id)
    except TokenRejected as exc:
        # Cookie refresh mati/ganti sesi: manual mode lagi, cukup kabari sekali sehari.
        if not await store.flag_exists(db, user.tg_chat_id, today, FLAG_REFRESH_FAILED):
            await store.set_flag(db, user.tg_chat_id, today, FLAG_REFRESH_FAILED, now)
            await notify.send_message(user.tg_chat_id, fmt.refresh_cookie_dead_message(str(exc)))
            await store.add_event(
                db, user.tg_chat_id, today, "", "", store.KIND_TOKEN, f"refresh cookie mati: {exc}", now
            )
        log.warning("refresh cookie user %s ditolak: %s", user.tg_chat_id, exc)
        return None
    except MonevError as exc:
        log.warning("refresh token gagal (belum tentu salah cookie): %s", exc)
        return None

    await store.update_token(
        db, user.tg_chat_id, token, fingerprint(token), ttl_seconds(token), now
    )
    if rotated:
        normalized = normalize_cookie(f"{client.cookie_name}={rotated}")
        if normalized != user.refresh_cookie:
            await store.update_refresh_cookie(db, user.tg_chat_id, normalized, now)
    await store.set_token_invalid(db, user.tg_chat_id, False)
    await store.add_event(
        db, user.tg_chat_id, today, "", "", store.KIND_TOKEN, "token diperbarui otomatis", now
    )
    await db.execute(
        "DELETE FROM notify_flags WHERE user_id = ? AND flag IN (?, ?)",
        (user.tg_chat_id, FLAG_TOKEN_WARNING, FLAG_TOKEN_ALERT),
    )
    await db.commit()
    log.info("access_token diperbarui otomatis untuk user %s", mask_token(token))
    fresh = await store.get_user(db, user.tg_chat_id)
    if fresh:
        client.set_token(fresh.bearer_token)
    return fresh
