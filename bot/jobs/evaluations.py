"""Job pemantau evaluasi bulanan mentor.

Deteksi "Belum Diisi" -> "Sudah Diisi Mentor" dilakukan tanpa menyimpan salinan
payload: cukup satu flag `eval:<periode>:filled` per pengguna. Begitu flag ada,
periode itu tidak pernah dibahas lagi — termasuk kalau mentor mengedit nilainya
belakangan, karena mengirim ulang kartu yang mirip cuma jadi spam.

Satu perubahan status = DUA pesan berurutan: alert bahwa mentor baru saja mengisi
(rincian disebut sedang dimuat), lalu kartu hasilnya. Keduanya berasal dari query
yang sama, jadi tidak ada jeda di antaranya; alert dikirim duluan supaya notifikasi
di layar user langsung berisi. Flag ditulis SEBELUM mengirim, supaya kalau koneksi
Telegram putus di tengah jalan bot tidak mengirim alert yang sama dua kali.

Periode yang dilihat: bulan berjalan + bulan sebelumnya. Evaluasi bulan November
baru ditulis mentor sekitar tanggal 9 Desember, jadi bulan lalu wajib di-poll juga.

Dulu job ini masih harus menebak nama endpoint-nya (plus path terkunci dan backoff
24 jam). Path aslinya sudah ditemukan dari bundle SPA portal, jadi seluruh mekanisme
probe itu dihapus: satu query nyata per periode, tanpa state tambahan di DB.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiosqlite

from .. import store
from ..messages import evaluasi
from ..monev import endpoints
from ..monev.client import MonevClient
from ..monev.evaluation import periods_to_watch
from ..models import EvaluationResult, User
from ..settings import Settings

log = logging.getLogger("bot.evaluasi")

MONTHS_BACK = 2


def filled_flag(period: str) -> str:
    """Flag global (log_date kosong) yang menandai evaluasi periode sudah terisi.

    Dipakai juga oleh pengingat pagi: jadwal H-7 tidak layak diteruskan kalau
    mentor ternyata sudah mengisi — lihat bot/jobs/reminders.py.
    """
    return f"eval:{period}:filled"


async def lookup_evaluation(
    db: aiosqlite.Connection,
    user: User,
    client: MonevClient,
    period: str,
    now: datetime,
) -> EvaluationResult:
    """Ambil hasil evaluasi satu periode. `db`/`now` tidak dipakai lagi, tapi tetap
    diterima supaya panggilan dari job dan `/evaluasi` tidak perlu bercabang."""
    return await endpoints.fetch_evaluation(client, user.participant_id, period)


async def lookup_latest_evaluation(
    db: aiosqlite.Connection,
    user: User,
    client: MonevClient,
    now: datetime,
    months_back: int = MONTHS_BACK,
) -> EvaluationResult:
    """Evaluasi terbaru yang memang sudah ada nilainya, dari periode yang masih wajar.

    Dipakai `/evaluasi` tanpa argumen. Bulan berjalan ditanya duluan, tapi evaluasi
    bulan lalu biasanya baru ditulis mentor pada awal bulan berikutnya — jadi kalau
    bulan ini masih kosong, periode sebelumnya ikut dilihat. Kartu hasil selalu
    menyebut periodenya sendiri, jadi tidak ada bulan yang salah diklaim.

    Kalau tidak ada satu pun yang terisi, hasil periode berjalan (kosong) yang
    dikembalikan supaya pemanggil tinggal menampilkan Respons A dengan bulan yang benar.
    """
    fallback: EvaluationResult | None = None
    for period in periods_to_watch(now.date().isoformat(), months_back):
        result = await lookup_evaluation(db, user, client, period, now)
        if result.is_filled:
            return result
        if fallback is None:
            fallback = result
    return fallback or EvaluationResult(period=now.date().isoformat()[:7])


async def run_evaluation_job(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    client: MonevClient,
    notify: Any,
    *,
    now: datetime | None = None,
) -> int:
    """Kirim alert + kartu hasil untuk evaluasi yang baru muncul. Return jumlah pesan."""
    now = now or settings.now()
    if not user.participant_id:
        return 0
    sent = 0

    for period in periods_to_watch(now.date().isoformat(), MONTHS_BACK):
        if await store.flag_exists(db, user.tg_chat_id, "", filled_flag(period)):
            continue
        try:
            result = await lookup_evaluation(db, user, client, period, now)
        except Exception as exc:  # noqa: BLE001 - job info tidak boleh menghentikan polling
            log.warning("gagal mengambil evaluasi %s untuk user %s: %s", period, user.tg_chat_id, exc)
            return sent
        if not result.is_filled:
            continue

        await store.set_flag(db, user.tg_chat_id, "", filled_flag(period), now)
        # Pesan 1: alert instan. Pesan 2: rincian nilainya.
        await notify.send_message(user.tg_chat_id, evaluasi.eval_alert_message())
        await notify.send_message(user.tg_chat_id, evaluasi.eval_result_message(result))
        await store.add_event(
            db,
            user.tg_chat_id,
            period,
            "belum diisi",
            "diisi mentor",
            store.KIND_INFO,
            f"evaluasi {period} keluar (rata-rata {result.average:.1f})",
            now,
        )
        log.info("evaluasi %s terisi untuk user %s", period, user.tg_chat_id)
        sent += 2

    return sent
