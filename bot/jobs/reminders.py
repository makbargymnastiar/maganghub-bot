"""Job reminder harian.

Keputusan reminder dipisah jadi fungsi murni `plan_reminders()` supaya bisa
diuji tanpa menunggu jam dinding, dan tiap jenis pengingat punya flag sendiri
di DB — perbaikan langsung dari bug versi lama yang mengirim pesan "DARURAT"
setiap 3 menit antara jam 23:00-23:59.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

import aiosqlite

from .. import store
from ..models import User
from ..settings import Settings
from ..status import ALL_LABELS, NAGGABLE_KEYS, NEEDS_REVISION
from ..tg import format as fmt

log = logging.getLogger("bot.reminder")

# Hanya status ini yang layak ditagih reminder (satu sumber kebenaran: bot/status.py).
NAGGABLE = set(NAGGABLE_KEYS)

FLAG_EVENING = "evening"
FLAG_FOLLOWUP = "followup"
FLAG_EMERGENCY = "emergency"
FLAG_REVISION = "revision_followup"

REVISION_AFTER_HOURS = 6


@dataclass(frozen=True)
class ReminderTimes:
    evening: dtime = dtime(18, 0)
    followup: dtime = dtime(21, 0)
    emergency: dtime = dtime(23, 0)


def plan_reminders(
    now: datetime,
    label_key: str,
    sent_flags: set[str],
    times: ReminderTimes,
) -> list[tuple[str, str]]:
    """Maksimal satu tindakan per siklus: tingkatan tertinggi yang sudah jatuh tempo.

    Kalau bot baru dinyalakan jam 21:30 dan belum ada flag, hanya `followup` yang
    dikirim; `evening` ditandai selesai supaya tidak menyusul 5 menit kemudian.
    """
    if label_key not in NAGGABLE:
        return []

    clock = now.timetz().replace(tzinfo=None)
    tiers = [
        (FLAG_EMERGENCY, "emergency", times.emergency),
        (FLAG_FOLLOWUP, "followup", times.followup),
        (FLAG_EVENING, "evening", times.evening),
    ]
    for flag, kind, at in tiers:
        if flag in sent_flags:
            # Tingkatan yang lebih tinggi sudah terkirim -> yang di bawahnya tidak perlu lagi.
            return []
        if clock >= at:
            return [(flag, kind)]
    return []


def superseded_flags(chosen_flag: str, times: ReminderTimes, clock) -> list[str]:
    """Flag tingkatan di bawah `chosen_flag` yang ikut dianggap selesai."""
    order = {FLAG_EMERGENCY: 3, FLAG_FOLLOWUP: 2, FLAG_EVENING: 1}
    rank = order.get(chosen_flag, 0)
    lower = [flag for flag, value in order.items() if value < rank]
    windows = {FLAG_EVENING: times.evening, FLAG_FOLLOWUP: times.followup, FLAG_EMERGENCY: times.emergency}
    return [flag for flag in lower if clock >= windows[flag]]


def revision_followup_due(now: datetime, first_seen: datetime | None) -> bool:
    if first_seen is None:
        return False
    return (now - first_seen) >= timedelta(hours=REVISION_AFTER_HOURS)


async def run_reminders(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    notify: Any,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Kirim reminder yang jatuh tempo. Return daftar flag yang terkirim."""
    now = now or settings.now()
    today = now.date().isoformat()
    sent: list[str] = []

    if not user.reminders_on:
        return sent

    row = await store.get_day_state(db, user.tg_chat_id, today)
    label_key = row["label"] if row else "EMPTY"
    label = ALL_LABELS.get(label_key)
    if label is None:
        return sent

    flags = set()
    for flag in (FLAG_EVENING, FLAG_FOLLOWUP, FLAG_EMERGENCY):
        if await store.flag_exists(db, user.tg_chat_id, today, flag):
            flags.add(flag)

    times = ReminderTimes(
        evening=settings.reminder_evening,
        followup=settings.reminder_followup,
        emergency=settings.reminder_emergency,
    )

    for flag, kind in plan_reminders(now, label_key, flags, times):
        await store.set_flag(db, user.tg_chat_id, today, flag, now)
        for skipped in superseded_flags(flag, times, now.timetz().replace(tzinfo=None)):
            await store.set_flag(db, user.tg_chat_id, today, skipped, now)
        await notify.send_message(user.tg_chat_id, fmt.reminder_message(kind, today, label))
        await store.add_event(
            db, user.tg_chat_id, today, label_key, label_key, store.KIND_REMINDER, kind, now
        )
        sent.append(flag)

    if label is NEEDS_REVISION and FLAG_REVISION not in flags:
        first_seen_raw = row["first_seen_at"] if row else ""
        first_seen = datetime.fromisoformat(first_seen_raw) if first_seen_raw else None
        if revision_followup_due(now, first_seen):
            hours = int((now - first_seen).total_seconds() // 3600) if first_seen else 0
            await store.set_flag(db, user.tg_chat_id, today, FLAG_REVISION, now)
            await notify.send_message(user.tg_chat_id, fmt.revision_followup_message(today, hours))
            await store.add_event(
                db, user.tg_chat_id, today, label_key, label_key, store.KIND_REMINDER, "revision", now
            )
            sent.append(FLAG_REVISION)

    return sent


def month_floor(today: date) -> str:
    return today.replace(day=1).isoformat()
