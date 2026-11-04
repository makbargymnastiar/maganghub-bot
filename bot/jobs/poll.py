"""Job polling: gabungkan 3 endpoint, deteksi perubahan status, kirim notifikasi.

Perilaku penting:
- Run pertama memakai mode *seed*: status semua tanggal dicatat tanpa notifikasi,
  supaya riwayat 60 hari tidak berubah jadi banjir pesan.
- Perubahan setelah itu dikirim 1x per tanggal per perubahan (state di DB),
  sehingga restart bot tidak mengulang notifikasi.
- Hanya tanggal dalam `lookback_days` yang boleh memicu notifikasi, agar
  perubahan data historis (mis. ACC telat) tidak mengejutkan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import aiosqlite

from .. import store
from ..messages import absensi
from ..monev import endpoints
from ..monev.client import MonevClient
from ..models import DayRecord, User
from ..monev.status import NAGGABLE_KEYS, UNKNOWN, Label, resolve, unknown_detail
from ..settings import Settings

log = logging.getLogger("bot.poll")

NOTIFY_LOOKBACK_DAYS = 7


@dataclass
class PollResult:
    today: str = ""
    today_label: Label | None = None
    changes: list[tuple[str, str]] = field(default_factory=list)
    seeded: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def _month_first(day: date) -> str:
    return day.replace(day=1).isoformat()


def months_for_window(today: date, lookback_days: int) -> list[str]:
    """Awal bulan yang harus diminta supaya seluruh jendela lookback tertutup.

    API daily-logs hanya menjawab per bulan (`date` = hari pertama bulan), jadi
    jendela yang melewati batas bulan butuh lebih dari satu request. Kalau hanya
    bulan berjalan yang diambil, tanggal di bulan sebelumnya tampak "tidak ada
    log" dan salah dilabeli ATTENDED_NO_LOG/EMPTY.
    """
    cursor = (today - timedelta(days=max(lookback_days, 0))).replace(day=1)
    end = today.replace(day=1)
    months: list[str] = []
    while cursor <= end:
        months.append(cursor.isoformat())
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months


def build_records(
    today: date,
    home: dict[str, Any],
    attendances: dict[str, dict[str, Any]],
    logs: dict[str, dict[str, Any]],
    lookback_days: int = NOTIFY_LOOKBACK_DAYS,
) -> list[DayRecord]:
    """Satukan attendance + daily-log per tanggal, sebatas jendela lookback + hari ini."""
    dates = {d for d in list(attendances) + list(logs)}
    dates.add(today.isoformat())
    floor = (today - timedelta(days=lookback_days)).isoformat()
    today_iso = today.isoformat()

    records = []
    for iso in sorted(dates):
        if iso < floor:
            continue
        records.append(
            DayRecord(
                date=iso,
                attendance=attendances.get(iso),
                log=logs.get(iso),
                is_holiday=bool(home.get("is_holiday")) if iso == today_iso else False,
                is_off_day=bool(home.get("is_scheduled_off_day")) if iso == today_iso else False,
            )
        )
    return records


async def _fetch_indices(
    client: MonevClient, participant_id: str, today: date, lookback_days: int
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    home = await endpoints.fetch_home(client)
    attendances = await endpoints.fetch_attendances(client, participant_id)
    logs: dict[str, dict[str, Any]] = {}
    for month in months_for_window(today, lookback_days):
        logs.update(await endpoints.fetch_daily_logs(client, participant_id, month))
    return attendances, logs, home


async def run_poll(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    client: MonevClient,
    *,
    now: datetime | None = None,
    notify: Any | None = None,
    notify_changes: bool = True,
) -> PollResult:
    """Satu siklus polling. `notify` objek TelegramClient (opsional untuk test)."""
    now = now or settings.now()
    today = now.date()
    result = PollResult(today=today.isoformat())

    attendances, logs, home = await _fetch_indices(client, user.participant_id, today, NOTIFY_LOOKBACK_DAYS)
    records = build_records(today, home, attendances, logs)

    is_first_run = await store.count_day_states(db, user.tg_chat_id) == 0
    result.seeded = is_first_run

    for record in records:
        label = resolve(record)
        previous = await store.upsert_day_state(
            db,
            user.tg_chat_id,
            record.date,
            label.key,
            {
                "attendance_status": record.attendance_status,
                "approval_status": record.approval_status,
                "log_state": record.log_state,
                "log_id": (record.log or {}).get("id") or (record.attendance or {}).get("id"),
            },
            now,
        )
        if record.date == today.isoformat():
            result.today_label = label

        if previous == label.key:
            continue

        await store.add_event(
            db,
            user.tg_chat_id,
            record.date,
            previous or "(baru)",
            label.key,
            store.KIND_STATUS,
            label.title,
            now,
        )

        if is_first_run or not notify_changes:
            result.notes.append(f"seed {record.date} -> {label.key}")
            continue

        # pergantian hari: record hari baru pertama kali hampir selalu EMPTY —
        # bukan perubahan nyata, cukup catat; penagihan pagi handled reminder_pagi 08:00
        if (
            record.date == today.isoformat()
            and previous is None
            and label.key in NAGGABLE_KEYS
        ):
            result.notes.append(f"day-rollover {record.date} -> {label.key} (silent)")
            continue

        # Tanggal lama yang berubah di luar jendela notifikasi: catat, jangan kirim.
        if record.date < (today - timedelta(days=NOTIFY_LOOKBACK_DAYS)).isoformat():
            continue

        if label is UNKNOWN:
            if not await store.flag_exists(db, user.tg_chat_id, record.date, "unknown_notified"):
                await store.set_flag(db, user.tg_chat_id, record.date, "unknown_notified", now)
                if notify is not None:
                    await notify.send_message(
                        user.tg_chat_id,
                        absensi.unknown_status_message(unknown_detail(record), record.date),
                    )
                result.changes.append((record.date, label.key))
            continue

        if notify is not None:
            await notify.send_message(
                user.tg_chat_id,
                absensi.status_change_message(label, record.date, record.reviewer_notes),
            )
        result.changes.append((record.date, label.key))
        log.info("status %s -> %s (%s)", previous, label.key, record.date)

    return result
