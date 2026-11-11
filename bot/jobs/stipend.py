"""Job pemantau pengajuan uang saku bulanan oleh mentor.

Pemicunya keberadaan satu baris klaim di `/payment/claims` untuk periode yang
dipantau (klaim diajukan mentor ± tanggal 9 untuk bulan sebelumnya). Begitu
ada, kirim DUA pesan sekaligus: alert "mentor baru mengajukan" lalu rincian
estimasi pencairan — semuanya dari satu query, tanpa jeda.

Dedup flag `stipend:<periode>:claimed` ditulis SEBELUM mengirim, konvensi sama
seperti job evaluasi: koneksi Telegram putus di tengah jalan tidak boleh jadi
spam dua kali.

Angka terutama dari klaimnya sendiri (total_working_days, payable_days,
amount — hitungan portal). Kalau respons klaim kebetulan tidak membawa jumlah
hari, jatuh ke hitungan attendance bulan itu; kalau inipun kosong, pesan
rincian menampilkan UMP utuh daripada mengarang kekurangan.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiosqlite

from .. import store
from ..messages import stipend as messages
from ..monev import endpoints, stipend
from ..monev.client import MonevClient
from ..monev.evaluation import periods_to_watch
from ..models import StipendClaim, StipendEstimate, User
from ..settings import Settings

log = logging.getLogger("bot.uangsaku")

MONTHS_BACK = 2


def _flag(period: str) -> str:
    return f"stipend:{period}:claimed"


async def _schedule_id_for(
    db: aiosqlite.Connection, client: MonevClient, user: User, now: datetime
) -> str:
    """Parameter schedule_id — dicari sekali per peserta lalu di-cache di settings."""
    key = f"schedule_id:{user.participant_id}"
    cached = await store.get_setting(db, key)
    if cached:
        return cached
    found = await endpoints.discover_schedule_id(client)
    if found:
        await store.set_setting(db, key, found, now)
    return found


async def _claim_with_days(
    db: aiosqlite.Connection, client: MonevClient, user: User, claim: StipendClaim, now: datetime
) -> StipendClaim:
    """Pakai jumlah hari dari klaim; fallback ke attendances kalau tidak ada."""
    if claim.has_days or claim.amount is not None:
        return claim
    try:
        rows = await endpoints.fetch_attendances(client, user.participant_id)
    except Exception as exc:  # noqa: BLE001 - estimasi fallback tidak boleh gagalkan alert
        log.warning("fallback attendances untuk %s gagal: %s", claim.period, exc)
        return claim
    counted = stipend.claim_from_attendances(claim.period, rows.values())
    return counted if counted.has_days else claim


async def lookup_stipend_estimate(
    db: aiosqlite.Connection, user: User, client: MonevClient, period: str, now: datetime
) -> StipendEstimate | None:
    """Estimasi satu periode; None kalau klaim bulan itu memang belum ada.

    Dipakai job DAN `/klaim`. `MonevError` sengaja dibiarkan naik ke pemanggil
    (konvensi yang sama dengan lookup_evaluation): yang menolak server/token,
    bukan evaluasinya yang kosong.
    """
    if not user.participant_id:
        return None
    schedule_id = await _schedule_id_for(db, client, user, now)
    payload = await endpoints.fetch_stipend_claims(
        client, user.participant_id, f"{period}-01", schedule_id
    )
    claims = stipend.parse_claims(payload, period)
    if not claims:
        return None
    return stipend.estimate(await _claim_with_days(db, client, user, claims[0], now))


async def lookup_latest_stipend_estimate(
    db: aiosqlite.Connection,
    user: User,
    client: MonevClient,
    now: datetime,
    months_back: int = MONTHS_BACK,
) -> StipendEstimate | None:
    """Estimasi terbaru yang memang sudah diajukan, dari periode yang wajar.

    Dipakai `/klaim` tanpa argumen — polanya sama dengan /evaluasi: klaim
    bulan lalu baru muncul ± tanggal 9 bulan berjalan, jadi bulan berjalan
    ditanya duluan tapi bulan sebelumnya ikut dilihat. Rincian selalu menyebut
    periodenya sendiri.
    """
    for period in periods_to_watch(now.date().isoformat(), months_back):
        est = await lookup_stipend_estimate(db, user, client, period, now)
        if est is not None:
            return est
    return None


async def run_stipend_job(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    client: MonevClient,
    notify: Any,
    *,
    now: datetime | None = None,
) -> int:
    """Kirim alert + rincian untuk pengajuan uang saku yang baru muncul."""
    now = now or settings.now()
    if not user.participant_id:
        return 0
    uid = user.tg_chat_id
    sent = 0

    watch = [p for p in periods_to_watch(now.date().isoformat(), MONTHS_BACK)
             if not await store.flag_exists(db, uid, "", _flag(p))]
    if not watch:
        return 0

    for period in watch:
        try:
            est = await lookup_stipend_estimate(db, user, client, period, now)
        except Exception as exc:  # noqa: BLE001 - sama seperti job evaluasi: berhenti halus
            log.warning("gagal mengambil klaim uang saku %s untuk user %s: %s", period, uid, exc)
            return sent
        if est is None:
            continue

        await store.set_flag(db, uid, "", _flag(period), now)
        # Pesan 1: alert instan. Pesan 2: hasil kalkulasi estimasi.
        await notify.send_message(uid, messages.claim_alert_message())
        await notify.send_message(uid, messages.claim_detail_message(est))
        await store.add_event(
            db,
            uid,
            period,
            "belum diajukan",
            "diajukan mentor",
            store.KIND_INFO,
            f"uang saku {period} diajukan ({est.percent}% kehadiran, diterima {est.diterima})",
            now,
        )
        log.info("klaim uang saku %s terdeteksi untuk user %s", period, uid)
        sent += 2

    return sent
