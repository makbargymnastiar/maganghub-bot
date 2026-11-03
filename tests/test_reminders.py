"""Uji penjadwalan reminder — regression untuk bug spam 23:00 & state tertimpa."""

from datetime import datetime, time as dtime

from bot.jobs.reminders import (
    FLAG_EMERGENCY,
    FLAG_EVENING,
    FLAG_FOLLOWUP,
    NAGGABLE,
    ReminderTimes,
    plan_reminders,
    revision_followup_due,
    superseded_flags,
)

TZ = "+07:00"
TIMES = ReminderTimes(evening=dtime(18, 0), followup=dtime(21, 0), emergency=dtime(23, 0))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.fromisoformat(f"2026-11-03T{hour:02d}:{minute:02d}:00{TZ}")


def test_status_sudah_diisi_tidak_ditagih():
    for key in ("APPROVED", "PENDING_REVIEW", "REVISION_SUBMITTED", "HOLIDAY", "ABSENT", "REJECTED"):
        assert plan_reminders(at(23, 30), key, set(), TIMES) == []


def test_hanya_status_kosong_dan_uraian_kosong_yang_ditagih():
    assert NAGGABLE == {"EMPTY", "ATTENDED_NO_LOG"}


def test_sebelum_jam_sore_tidak_ada_reminder():
    assert plan_reminders(at(17, 59), "EMPTY", set(), TIMES) == []


def test_jam_sore_kirim_satu_kali():
    assert plan_reminders(at(18, 0), "EMPTY", set(), TIMES) == [(FLAG_EVENING, "evening")]
    assert plan_reminders(at(18, 5), "EMPTY", {FLAG_EVENING}, TIMES) == []


def test_darurat_punya_flag_sendiri():
    """Regression: versi lama mengirim 'DARURAT' tiap 3 menit karena tanpa flag."""
    assert plan_reminders(at(23, 0), "EMPTY", {FLAG_EVENING, FLAG_FOLLOWUP}, TIMES) == [
        (FLAG_EMERGENCY, "emergency")
    ]
    assert plan_reminders(at(23, 3), "EMPTY", {FLAG_EVENING, FLAG_FOLLOWUP, FLAG_EMERGENCY}, TIMES) == []
    assert plan_reminders(at(23, 9), "EMPTY", {FLAG_EMERGENCY}, TIMES) == []


def test_bot_baru_jalan_malam_hanya_kirim_satu_pesan():
    planned = plan_reminders(at(23, 40), "EMPTY", set(), TIMES)
    assert len(planned) == 1
    assert planned[0][0] == FLAG_EMERGENCY


def test_flag_tingkatan_bawah_ikut_ditandai():
    assert set(superseded_flags(FLAG_EMERGENCY, TIMES, at(23, 40).timetz().replace(tzinfo=None))) == {
        FLAG_EVENING,
        FLAG_FOLLOWUP,
    }
    assert superseded_flags(FLAG_EVENING, TIMES, at(18, 10).timetz().replace(tzinfo=None)) == []


def test_reminder_uraian_kosong_tetap_dikirim():
    assert plan_reminders(at(19, 0), "ATTENDED_NO_LOG", set(), TIMES) == [(FLAG_EVENING, "evening")]


def test_followup_revisi_setelah_enam_jam():
    first_seen = at(9, 0)
    assert revision_followup_due(at(14, 0), first_seen) is False
    assert revision_followup_due(at(15, 0), first_seen) is True
    assert revision_followup_due(at(15, 0), None) is False
