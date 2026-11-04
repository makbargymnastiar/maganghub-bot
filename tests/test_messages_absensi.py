"""Uji kalimat notif status (messages/absensi.py) + alurnya lewat run_poll."""

from datetime import datetime

import pytest

from bot import db as dbmod
from bot import store
from bot.jobs.poll import run_poll
from bot.messages import absensi
from bot.monev.status import APPROVED, NEEDS_REVISION, PENDING_REVIEW
from bot.settings import load_settings
from tests.test_poll_diff import FakeMonevClient, FakeTelegram, make_payloads

TODAY = datetime.fromisoformat("2026-11-03T10:00:00+07:00")
FULL = {"activity_log": "Merakit PC", "lesson_learned": "Belajar subnetting", "obstacles": "Kabel kurang"}
ATT = {"date": "2026-11-03", "status": "PRESENT", "approval_status": None}


def daily(**content) -> dict:
    base = {"date": "2026-11-03", "state": "DRAFT", "id": 7, "activity_log": "", "lesson_learned": "", "obstacles": ""}
    base.update(content)
    return base


@pytest.fixture
async def ctx(tmp_path):
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "x",
            "TELEGRAM_CHAT_ID": "111",
            "PARTICIPANT_ID": "pid-1",
            "BEARER_TOKEN": "Bearer abc",
            "DB_PATH": str(tmp_path / "bot.db"),
        }
    )
    database = await dbmod.connect(settings.db_path)
    user = await store.ensure_user(database, settings, name="Tester")
    yield settings, database, user
    await database.close()


async def poll_once(database, settings, user, log, notify):
    await run_poll(
        database, settings, user, FakeMonevClient(make_payloads(attendance=ATT, log=log)), now=TODAY, notify=notify
    )


# --- unit: format satu gaya untuk semua status --------------------------------


def test_format_notif_status_baru():
    msg = absensi.status_change_message(APPROVED, "2026-11-03")
    assert "📊 <b>PERUBAHAN STATUS ABSENSI</b>" in msg
    assert "✅ Status: <b>Hadir Disetujui</b>" in msg
    assert "📅 Tanggal: Selasa, 03 November 2026" in msg
    assert "💬 Catatan Mentor: <i>-</i>" in msg
    assert "Tindakan" not in msg  # baris lama memang dihapus


def test_notif_revisi_ambil_catatan_mentor_dan_petunjuk():
    msg = absensi.status_change_message(NEEDS_REVISION, "2026-11-03", "Bagian jaringan belum rinci")
    assert "💬 Catatan Mentor: <i>Bagian jaringan belum rinci</i>" in msg
    assert "👉 Perbaiki logbook lalu kirim ulang di portal." in msg


def test_catatan_mentor_diescape():
    msg = absensi.status_change_message(PENDING_REVIEW, "2026-11-03", "<script>jahat</script>")
    assert "&lt;script&gt;" in msg and "<script>" not in msg


# --- integrasi: alur pengisian -------------------------------------------------


async def test_alur_pengisian_mengirim_pesan_berjenjang(ctx):
    """Konsep inti user: diingatkan selama belum lengkap, dilapor saat berubah."""
    settings, database, user = ctx
    tg = FakeTelegram()

    await poll_once(database, settings, user, daily(), tg)
    assert tg.messages == []  # siklus pertama = seed, tidak spam

    await poll_once(database, settings, user, daily(activity_log="Merakit PC"), tg)
    assert "Sebagian Terisi" in tg.messages[-1]
    assert "💬 Catatan Mentor: <i>-</i>" in tg.messages[-1]

    # label tetap PARTIAL -> sengaja tidak pesan baru (anti spam)
    count = len(tg.messages)
    await poll_once(database, settings, user, daily(activity_log="Merakit PC", lesson_learned="Belajar subnetting"), tg)
    assert len(tg.messages) == count

    # lengkap = terkirim (portal tidak punya tahap "belum dikirim")
    await poll_once(database, settings, user, daily(**FULL), tg)
    assert "Menunggu ACC Mentor" in tg.messages[-1]

    before = len(tg.messages)
    await poll_once(database, settings, user, daily(state="COMPLETED", **FULL), tg)
    assert len(tg.messages) == before  # COMPLETED tetap PENDING_REVIEW


async def test_pesan_revisi_membawa_catatan_mentor(ctx):
    settings, database, user = ctx
    tg = FakeTelegram()
    await poll_once(database, settings, user, daily(state="COMPLETED", **FULL), tg)

    revision = daily(state="NEEDS_REVISION", reviewer_notes="Bagian jaringan belum rinci", **FULL)
    await poll_once(database, settings, user, revision, tg)

    assert "Perlu Revisi" in tg.messages[-1]
    assert "💬 Catatan Mentor: <i>Bagian jaringan belum rinci</i>" in tg.messages[-1]
