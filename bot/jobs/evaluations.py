"""Job pemantau evaluasi bulanan mentor.

Deteksi "Belum Diisi" -> "Sudah Diisi Mentor" dilakukan tanpa menyimpan salinan
payload: cukup satu flag `eval:<periode>:filled` per pengguna. Begitu flag ada,
periode itu tidak pernah dibahas lagi — termasuk kalau mentor mengedit nilainya
belakangan, karena mengirim ulang kartu yang mirip cuma jadi spam.

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


def _flag(period: str) -> str:
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


async def run_evaluation_job(
    db: aiosqlite.Connection,
    settings: Settings,
    user: User,
    client: MonevClient,
    notify: Any,
    *,
    now: datetime | None = None,
) -> int:
    """Kirim kartu hasil evaluasi yang baru muncul. Return jumlah pesan terkirim."""
    now = now or settings.now()
    if not user.participant_id:
        return 0
    sent = 0

    for period in periods_to_watch(now.date().isoformat(), MONTHS_BACK):
        if await store.flag_exists(db, user.tg_chat_id, "", _flag(period)):
            continue
        try:
            result = await lookup_evaluation(db, user, client, period, now)
        except Exception as exc:  # noqa: BLE001 - job info tidak boleh menghentikan polling
            log.warning("gagal mengambil evaluasi %s untuk user %s: %s", period, user.tg_chat_id, exc)
            return sent
        if not result.is_filled:
            continue

        await store.set_flag(db, user.tg_chat_id, "", _flag(period), now)
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
        sent += 1

    return sent
