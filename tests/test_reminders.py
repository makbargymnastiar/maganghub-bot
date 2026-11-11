"""Uji jadwal pengingat TERKUNCI — berjenjang, jendela, dan interval antar-kirim."""

from datetime import date, datetime, time as dtime

from bot import db as dbmod
from bot import store
from bot.jobs.reminders import (
    MISSED_WINDOW,
    MORNING_WINDOW,
    REVISION_AFTER_HOURS,
    SORE_WINDOW,
    TIERS,
    current_tier,
    in_window,
    interval_due,
    nagged_eval_period,
    revision_followup_due,
    run_reminders,
)
from bot.settings import load_settings

TZ = "+07:00"


def at(hour: int, minute: int = 0, day: str = "2026-11-03") -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00{TZ}")


def clock(hour: int, minute: int = 0) -> dtime:
    return dtime(hour, minute)


def test_tier_berjenjang_sesuai_jam_yang_disetujui():
    assert current_tier(clock(17, 59)) is None
    assert current_tier(clock(18, 0)).flag == "rem_soft"
    assert current_tier(clock(20, 59)).flag == "rem_soft"
    assert current_tier(clock(21, 0)).flag == "rem_rawan"
    assert current_tier(clock(23, 29)).flag == "rem_rawan"
    assert current_tier(clock(23, 30)).flag == "rem_dekat"
    assert current_tier(clock(23, 45)).flag == "rem_dekat"
    assert current_tier(clock(23, 46)).flag == "rem_mepet"
    assert current_tier(clock(23, 59)).flag == "rem_mepet"
    assert current_tier(clock(0, 30)) is None


def test_interval_tier_terkunci():
    assert tuple(t.every_min for t in TIERS) == (30, 15, 5, 3)


def test_jendela_pembantu():
    assert in_window(clock(16, 55), *SORE_WINDOW) is True
    assert in_window(clock(17, 5), *SORE_WINDOW) is True
    assert in_window(clock(17, 6), *SORE_WINDOW) is False
    assert in_window(clock(0, 1), *MISSED_WINDOW) is True
    assert in_window(clock(8, 0), *MORNING_WINDOW) is True
    assert in_window(clock(12, 0), *MORNING_WINDOW) is True
    assert in_window(clock(13, 0), *MORNING_WINDOW) is False


def test_interval_due_pertama_kali_selalu_ya():
    assert interval_due(None, at(18, 0), 30) is True


def test_interval_due_hanya_setelah_selang_waktu():
    assert interval_due(at(18, 0), at(18, 29), 30) is False
    assert interval_due(at(18, 0), at(18, 30), 30) is True


def test_interval_due_toleransi_db_naive():
    """set_at di DB tersimpan tanpa tz — jangan sampai perbandingan crash."""
    assert interval_due(datetime(2026, 11, 3, 18, 0), at(18, 31), 30) is True


def test_followup_revisi_setelah_delapan_jam():
    assert REVISION_AFTER_HOURS == 8
    first_seen = at(9, 0)
    assert revision_followup_due(at(16, 59), first_seen) is False
    assert revision_followup_due(at(17, 0), first_seen) is True
    assert revision_followup_due(at(17, 0), None) is False


def test_periode_evaluasi_yang_dikejar_adalah_bulan_sebelumnya():
    assert nagged_eval_period(date(2026, 11, 5)) == "2026-10"
    assert nagged_eval_period(date(2026, 12, 9)) == "2026-11"
    assert nagged_eval_period(date(2027, 1, 3)) == "2026-12"


# --- integrasi: run_reminders dengan DB nyata + notify tiruan ------------------


class Notify:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((str(chat_id), text))
        return {"ok": True}


async def make_env(tmp_path, today: str, label: str, name: str = "bot.db"):
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "x",
            "TELEGRAM_CHAT_ID": "111",
            "PARTICIPANT_ID": "p1",
            "DB_PATH": str(tmp_path / name),
        }
    )
    database = await dbmod.connect(settings.db_path)
    user = await store.ensure_user(database, settings, name="Tester")
    noon = datetime.fromisoformat(f"{today}T07:00:00{TZ}")
    await store.upsert_day_state(database, user.tg_chat_id, today, label, {}, noon)
    return settings, user, database


async def test_berjenjang_kirim_lalu_hening_sampai_selang_waktu(tmp_path):
    settings, user, db = await make_env(tmp_path, "2026-11-03", "PARTIAL")
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(18, 0))
        assert sent == ["rem_soft"]
        assert "PENGINGAT ABSENSI" in tg.messages[-1][1]
        # tick berikutnya dalam menit yang sama / sebelum 30 menit → diam
        assert await run_reminders(db, settings, user, tg, now=at(18, 29)) == []
        assert await run_reminders(db, settings, user, tg, now=at(18, 30)) == ["rem_soft"]
    finally:
        await db.close()


async def test_status_lengkap_tidak_ditagih(tmp_path):
    settings, user, db = await make_env(tmp_path, "2026-11-03", "PENDING_REVIEW")
    tg = Notify()
    try:
        assert await run_reminders(db, settings, user, tg, now=at(21, 0)) == []
    finally:
        await db.close()


async def test_pengingat_pagi_hari_kerja_sekali(tmp_path):
    # 2026-11-04 = Rabu (hari kerja) → 08:00 dapat PENGINGAT PAGI ABSENSI, sekali saja
    settings, user, db = await make_env(tmp_path, "2026-11-04", "EMPTY", name="pagi.db")
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0, day="2026-11-04"))
        assert "reminder_pagi" in sent
        text = next(t for _, t in tg.messages if "PENGINGAT PAGI ABSENSI" in t)
        assert "Yuk catat logbook hari ini:" in text
        assert "• Uraian Aktivitas" in text and "Bismillah, selamat beraktivitas!" in text
        assert await run_reminders(db, settings, user, tg, now=at(9, 0, day="2026-11-04")) == []
    finally:
        await db.close()


async def test_pengingat_pagi_diam_saat_libur(tmp_path):
    # 2026-11-07 = Sabtu → hanya day_off, tidak ada pengingat logbook
    settings, user, db = await make_env(tmp_path, "2026-11-07", "EMPTY", name="libur.db")
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0, day="2026-11-07"))
        assert "reminder_pagi" not in sent
        assert "PENGINGAT PAGI" not in " ".join(t for _, t in tg.messages)
    finally:
        await db.close()


async def test_cek_sore_dua_kondisi_sekali_sehari(tmp_path):
    settings, user, db = await make_env(tmp_path, "2026-11-03", "EMPTY")
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(16, 55))
        assert sent == ["sore_check"]
        assert "Absensi Hari Ini Belum Diisi" in tg.messages[-1][1]
        assert await run_reminders(db, settings, user, tg, now=at(17, 0)) == []
    finally:
        await db.close()

    settings, user, db = await make_env(tmp_path, "2026-11-04", "APPROVED", name="bot2.db")
    tg = Notify()
    try:
        await run_reminders(db, settings, user, tg, now=at(16, 56, day="2026-11-04"))
        assert "Logbook Harian Lengkap" in tg.messages[-1][1]
    finally:
        await db.close()


async def test_kemarin_terlewat_sekali_saja(tmp_path):
    settings, user, db = await make_env(tmp_path, "2026-11-03", "EMPTY")
    await store.upsert_day_state(db, user.tg_chat_id, "2026-11-02", "PARTIAL", {}, at(7, 0))
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(0, 30))
        assert "missed_yesterday" in sent
        assert "ABSENSI TERLEWAT" in tg.messages[-1][1]
        assert await run_reminders(db, settings, user, tg, now=at(1, 0)) == []
    finally:
        await db.close()


async def test_paket_libur_akhir_pekan(tmp_path):
    # 2026-11-07 = Sabtu
    settings, user, db = await make_env(tmp_path, "2026-11-07", "EMPTY")
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0, day="2026-11-07"))
        assert "day_off" in sent
        assert "HARI INI LIBUR" in tg.messages[0][1]
        assert "Selamat beristirahat!" in tg.messages[0][1]
        assert await run_reminders(db, settings, user, tg, now=at(9, 0, day="2026-11-07")) == []
    finally:
        await db.close()


async def test_tagihan_acc_lima_hari(tmp_path):
    settings, user, db = await make_env(tmp_path, "2026-11-03", "PARTIAL")
    for d in ("2026-10-27", "2026-10-28", "2026-11-01"):
        await store.upsert_day_state(db, user.tg_chat_id, d, "PENDING_REVIEW", {}, at(7, 0))
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0))
        assert "pending_tagihan" in sent
        text = next(t for _, t in tg.messages if "MENUNGGU ACC MENTOR" in t)
        # hanya 27 & 28 Okt yang berusia ≥5 hari per 3 Nov; 01 Nov belum cukup tua
        assert "27 Okt" in text and "28 Okt" in text and "01 Nov" not in text
        assert "(7 HARI)" in text
    finally:
        await db.close()


async def test_tagihan_belum_lima_hari_diam(tmp_path):
    settings, user, db = await make_env(tmp_path, "2026-11-03", "PARTIAL")
    await store.upsert_day_state(db, user.tg_chat_id, "2026-11-01", "PENDING_REVIEW", {}, at(7, 0))
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0))
        assert "pending_tagihan" not in sent
    finally:
        await db.close()


async def test_eval_h_minus_dan_uang_saku(tmp_path):
    # 2026-11-05 → H-4 evaluasi; 2026-11-09 → hari pengajuan
    settings, user, db = await make_env(tmp_path, "2026-11-05", "PARTIAL")
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0, day="2026-11-05"))
        assert "eval_reminder" in sent
        assert "H-4" in next(t for _, t in tg.messages if "PENGINGAT EVALUASI MENTOR" in t)
    finally:
        await db.close()

    settings, user, db = await make_env(tmp_path, "2026-11-09", "PARTIAL")
    tg = Notify()
    try:
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0, day="2026-11-09"))
        # tanggal 9 mengirim dua hal berbeda: batas akhir evaluasi mentor DAN
        # pengajuan uang saku — keduanya wajib ada, tidak boleh saling menimpa
        assert "eval_due" in sent and "stipend" in sent
        assert "HARI-H EVALUASI MENTOR" in next(t for _, t in tg.messages if "EVALUASI MENTOR" in t)
        assert "PENGAJUAN UANG SAKU" in next(t for _, t in tg.messages if "UANG SAKU" in t)
    finally:
        await db.close()


async def test_eval_sudah_diisi_mentor_menghentikan_pengingat_evaluasi(tmp_path):
    # Bug lama: pengingat H-7 dan hari-H tetap dikirim walaupun mentor sudah
    # mengisi — watcher menulis flag eval:<periode>:filled, itu yang sekarang dibaca.
    settings, user, db = await make_env(tmp_path, "2026-11-05", "PARTIAL")
    tg = Notify()
    try:
        await store.set_flag(db, user.tg_chat_id, "", "eval:2026-10:filled", at(7, 30))
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0, day="2026-11-05"))
        assert "eval_reminder" not in sent
        assert not any("PENGINGAT EVALUASI MENTOR" in t for _, t in tg.messages)
        # flag harian tidak ditulis → setelah Evaluasi nanti bisa nagih lagi
        assert not await store.flag_exists(db, user.tg_chat_id, "2026-11-05", "eval_reminder")
    finally:
        await db.close()

    # tanggal 9: eval_due berhenti, tapi pengajuan uang saku tetap jalan
    settings, user, db = await make_env(tmp_path, "2026-11-09", "PARTIAL", name="sudah-diisi.db")
    tg = Notify()
    try:
        await store.set_flag(db, user.tg_chat_id, "", "eval:2026-10:filled", at(7, 30))
        sent = await run_reminders(db, settings, user, tg, now=at(8, 0, day="2026-11-09"))
        assert "eval_due" not in sent and "stipend" in sent
    finally:
        await db.close()
