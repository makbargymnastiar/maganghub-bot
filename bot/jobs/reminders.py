"""Job pengingat harian — jadwal TERKUNCI, tidak bisa diubah/dimatikan dari chat.

Aturan yang dijalankan (kalimat aslinya ada di bot/messages/pengisian.py dkk):
- 08:00            1x: pengingat pagi logbook (hari kerja saja)
- 16:55            cek sore, 1x — menagih yang belum / menandai yang sudah diisi
- 18:00–20:59      tiap 30 menit selama isian belum lengkap
- 21:00–23:29      tiap 15 menit (jam rawan ketiduran)
- 23:30–23:45      tiap 5 menit
- 23:46–23:59      tiap 3 menit
- 00:01–06:00      1x: laporan kemarin terlewat (diblok sampai pagi)
- 08:00–11:59      1x per item: libur hari ini, tagihan ACC ≥5 hari,
                   evaluasi H-7 (tanggal 2–8), pengajuan uang saku (tanggal 9)

Semua "sekali sehari" dijamin flag di notify_flags (kunci per tanggal), jadi
restart bot di tengah hari tidak mengulang pesan yang sudah terkirim. Penjadwal
memanggil ini tiap 1 menit; ketelitian menit (tier 3 mnt) cukup dari selisih
`set_at` flag, bukan dari jumlah tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable

import aiosqlite

from .. import store
from ..messages import absensi, harian, pengisian, tagihan
from ..models import User
from ..monev.status import ALL_LABELS, NAGGABLE_KEYS, NEEDS_REVISION
from ..settings import Settings

log = logging.getLogger("bot.reminder")

PENDING_ACC_DAYS = 5  # absensi menunggu mentor ≥5 hari → ditagihkan tiap pagi
REVISION_AFTER_HOURS = 8  # revisi didiamkan ≥8 jam → nag sekali
EVAL_LAST_DAY = 8  # pengajuan uang saku tanggal 9; evaluasi diingatkan tgl 2–8
STIPEND_DAY = 9


@dataclass(frozen=True)
class Tier:
    flag: str
    start: dtime
    end: dtime  # inklusif
    every_min: int
    build: Callable[[str], str]  # (log_date iso) -> kalimat


TIERS: tuple[Tier, ...] = (
    Tier("rem_soft", dtime(18, 0), dtime(20, 59, 59), 30, pengisian.reminder_soft),
    Tier("rem_rawan", dtime(21, 0), dtime(23, 29, 59), 15, pengisian.reminder_rawan),
    Tier("rem_dekat", dtime(23, 30), dtime(23, 45), 5, pengisian.reminder_dekat),
    Tier("rem_mepet", dtime(23, 46), dtime(23, 59), 3, pengisian.reminder_mepet),
)

SORE_WINDOW = (dtime(16, 55), dtime(17, 5))  # jendela toleransi kalau tick meleset
MISSED_WINDOW = (dtime(0, 1), dtime(6, 0))
MORNING_WINDOW = (dtime(8, 0), dtime(12, 0))


def in_window(clock: dtime, start: dtime, end: dtime) -> bool:
    return start <= clock <= end


def current_tier(clock: dtime) -> Tier | None:
    for tier in TIERS:
        if in_window(clock, tier.start, tier.end):
            return tier
    return None


def interval_due(last_set: datetime | None, now: datetime, every_min: int) -> bool:
    """Belum pernah kirim → harus kirim; kalau sudah, tunggu `every_min` menit."""
    if last_set is None:
        return True
    naive_now = now.replace(tzinfo=None)
    naive_last = last_set.replace(tzinfo=None)
    return (naive_now - naive_last) >= timedelta(minutes=every_min)


def revision_followup_due(now: datetime, first_seen: datetime | None) -> bool:
    if first_seen is None:
        return False
    return (now - first_seen) >= timedelta(hours=REVISION_AFTER_HOURS)


async def _send(
    db: aiosqlite.Connection,
    uid: str,
    log_date: str,
    flag: str,
    text: str,
    notify: Any,
    now: datetime,
    *,
    touch: bool = False,
) -> str:
    """Set flag harian + kirim + catat event; `touch` untuk tier berulang."""
    if touch:
        await store.touch_flag(db, uid, log_date, flag, now)
    else:
        await store.set_flag(db, uid, log_date, flag, now)
    await notify.send_message(uid, text)
    await store.add_event(
        db, uid, log_date, "", "", store.KIND_REMINDER, flag, now
    )
    return flag


async def run_reminders(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    notify: Any,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Satu tick (dipanggil tiap menit). Return daftar flag yang baru terkirim."""
    now = now or settings.now()
    today = now.date()
    today_iso = today.isoformat()
    uid = user.tg_chat_id
    clock = now.timetz().replace(tzinfo=None)
    sent: list[str] = []

    row = await store.get_day_state(db, uid, today_iso)
    label_key = row["label"] if row else "EMPTY"
    label = ALL_LABELS.get(label_key)

    # 1) Cek sore 16:55 — sekali, dua kondisi (menagih / menandai).
    if in_window(clock, *SORE_WINDOW) and not await store.flag_exists(db, uid, today_iso, "sore_check"):
        complete = label is not None and label_key not in NAGGABLE_KEYS
        sent.append(
            await _send(
                db, uid, today_iso, "sore_check",
                pengisian.sore_check(today_iso, complete), notify, now,
            )
        )

    # 2) Pengingat berjenjang — hanya untuk status yang belum layak.
    tier = current_tier(clock)
    if tier is not None and label_key in NAGGABLE_KEYS:
        last = await store.flag_set_at(db, uid, today_iso, tier.flag)
        if interval_due(last, now, tier.every_min):
            sent.append(
                await _send(
                    db, uid, today_iso, tier.flag,
                    tier.build(today_iso), notify, now, touch=True,
                )
            )

    # 3) Kemarin terlewat — sekali, keesokan paginya.
    if in_window(clock, *MISSED_WINDOW):
        yesterday = (today - timedelta(days=1)).isoformat()
        yrow = await store.get_day_state(db, uid, yesterday)
        if (
            yrow is not None
            and yrow["label"] in NAGGABLE_KEYS
            and not await store.flag_exists(db, uid, yesterday, "missed_yesterday")
        ):
            sent.append(
                await _send(
                    db, uid, yesterday, "missed_yesterday",
                    pengisian.missed_yesterday(yesterday, today_iso), notify, now,
                )
            )

    # 4) Revisi didiamkan ≥ 8 jam — sekali per tanggal.
    if label is NEEDS_REVISION and not await store.flag_exists(db, uid, today_iso, "revision_followup"):
        first_seen_raw = row["first_seen_at"] if row else ""
        try:
            first_seen = datetime.fromisoformat(first_seen_raw) if first_seen_raw else None
        except ValueError:
            first_seen = None
        if first_seen is not None:
            first_seen = first_seen.replace(tzinfo=now.tzinfo)
        if revision_followup_due(now, first_seen):
            hours = int((now - first_seen).total_seconds() // 3600)
            sent.append(
                await _send(
                    db, uid, today_iso, "revision_followup",
                    absensi.revision_followup_message(today_iso, hours), notify, now,
                )
            )

    # 5) Paket pagi 08:00–11:59 — tiap item sekali per hari.
    if in_window(clock, *MORNING_WINDOW):
        sent.extend(await run_morning(db, uid, today, notify, now))

    return sent


async def run_morning(
    db: aiosqlite.Connection,
    uid: str,
    today: date,
    notify: Any,
    now: datetime,
) -> list[str]:
    today_iso = today.isoformat()
    sent: list[str] = []

    # 5a) Hari libur (akhir pekan tetap terbaca dari kalender; libur lain dari
    #     label portal) → tidak usah mengisi hari ini.
    row = await store.get_day_state(db, uid, today_iso)
    weekend = today.weekday() >= 5
    portal_holiday = (row["label"] if row else "") == "HOLIDAY" and not weekend
    if (weekend or portal_holiday) and not await store.flag_exists(db, uid, today_iso, "day_off"):
        sent.append(
            await _send(
                db, uid, today_iso, "day_off",
                harian.day_off_message(today_iso, from_portal=portal_holiday), notify, now,
            )
        )

    # 5a2) Pengingat pagi logbook — hari kerja saja, sekali per hari; libur
    #      sudah punya pesan sendiri (day_off) jadi tidak berdua-duaan.
    if not (weekend or portal_holiday) and not await store.flag_exists(db, uid, today_iso, "reminder_pagi"):
        sent.append(
            await _send(
                db, uid, today_iso, "reminder_pagi",
                pengisian.morning_message(today_iso), notify, now,
            )
        )

    # 5b) Tagihan ACC: tanggal ≥5 hari masih menunggu mentor.
    if not await store.flag_exists(db, uid, today_iso, "pending_tagihan"):
        max_date = (today - timedelta(days=PENDING_ACC_DAYS)).isoformat()
        rows = await store.list_day_states_with_labels(
            db, uid, ("PENDING_REVIEW", "REVISION_SUBMITTED"), max_date
        )
        if rows:
            oldest = date.fromisoformat(rows[0]["log_date"])
            days = (today - oldest).days
            sent.append(
                await _send(
                    db, uid, today_iso, "pending_tagihan",
                    tagihan.pending_mentor_message([r["log_date"] for r in rows], days),
                    notify, now,
                )
            )

    # 5c) Evaluasi mentor H-7..H-1 (tanggal 2–8).
    if 2 <= today.day <= EVAL_LAST_DAY and not await store.flag_exists(db, uid, today_iso, "eval_reminder"):
        sent.append(
            await _send(
                db, uid, today_iso, "eval_reminder",
                harian.eval_reminder_message(STIPEND_DAY - today.day), notify, now,
            )
        )

    # 5d) Hari-H pengajuan uang saku (tanggal 9).
    if today.day == STIPEND_DAY and not await store.flag_exists(db, uid, today_iso, "stipend"):
        sent.append(
            await _send(
                db, uid, today_iso, "stipend", harian.stipend_message(), notify, now,
            )
        )

    return sent
