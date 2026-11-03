"""Job info tambahan: pengumuman monev + warning keterlambatan absensi.

Keduanya di-dedup lewat `notify_flags` (flag `ann:<id>` / `warn:<id>`) supaya
satu pengumuman hanya masuk sekali, dan tidak berubah jadi spam tiap siklus.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiosqlite

from .. import store
from ..messages import absensi
from ..monev import endpoints
from ..monev.client import MonevClient
from ..models import User
from ..settings import Settings

log = logging.getLogger("bot.info")


def _summary(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "").strip()
    return f"{title}\n\n{content}" if content else title


async def run_info_job(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    client: MonevClient,
    notify: Any,
    *,
    now: datetime | None = None,
) -> int:
    """Kirim pengumuman & warning yang belum pernah dikirim. Return jumlah pesan."""
    now = now or settings.now()
    sent = 0

    try:
        announcements = await endpoints.fetch_announcements(client)
    except Exception as exc:  # noqa: BLE001 - info job tidak boleh menggagalkan polling
        log.warning("gagal mengambil pengumuman: %s", exc)
        announcements = []

    for item in announcements:
        if not item.get("enabled", True):
            continue
        ann_id = item.get("id")
        if ann_id is None:
            continue
        flag = f"ann:{ann_id}"
        if await store.flag_exists(db, user.tg_chat_id, "", flag):
            continue
        await store.set_flag(db, user.tg_chat_id, "", flag, now)
        await notify.send_message(
            user.tg_chat_id,
            absensi.announcement_message(
                str(item.get("title") or "Pengumuman"),
                _summary(item),
                str(item.get("type") or "INFO"),
            ),
        )
        await store.add_event(
            db, user.tg_chat_id, "", "", "", store.KIND_INFO, f"pengumuman {ann_id}", now
        )
        sent += 1

    try:
        warnings = await endpoints.fetch_absence_warnings(client)
    except Exception as exc:  # noqa: BLE001
        log.warning("gagal mengambil warning absensi: %s", exc)
        warnings = []

    for item in warnings:
        warn_id = item.get("id")
        if warn_id is None:
            continue
        flag = f"warn:{warn_id}"
        if await store.flag_exists(db, user.tg_chat_id, "", flag):
            continue
        await store.set_flag(db, user.tg_chat_id, "", flag, now)
        text = str(item.get("message") or item.get("reason") or _summary(item))
        await notify.send_message(user.tg_chat_id, absensi.warning_message(text))
        await store.add_event(
            db, user.tg_chat_id, "", "", "", store.KIND_INFO, f"warning {warn_id}", now
        )
        sent += 1

    return sent
