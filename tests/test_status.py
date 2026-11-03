"""Uji precedence status — meniru urutan fungsi `i(e, t, r)` frontend monev."""

from bot.models import DayRecord
from bot.status import (
    ABSENT,
    APPROVED,
    APPROVED_LEAVE,
    EMPTY,
    HOLIDAY,
    NEEDS_REVISION,
    PENDING_REVIEW,
    REJECTED,
    REVISION_SUBMITTED,
    UNKNOWN,
    ATTENDED_NO_LOG,
    log_badge,
    mentor_action_pending,
    resolve,
)


def att(**kwargs):
    base = {"status": "PRESENT", "approval_status": "APPROVED"}
    base.update(kwargs)
    return base


def log(**kwargs):
    base = {"state": "APPROVED"}
    base.update(kwargs)
    return base


def rec(attendance=None, daily=None, holiday=False, off=False, date="2026-11-03"):
    return DayRecord(
        date=date, attendance=attendance, log=daily, is_holiday=holiday, is_off_day=off
    )


def test_dayoff_menangkan_semua():
    record = rec(att(status="DAYOFF"), log(state="NEEDS_REVISION"))
    assert resolve(record) is HOLIDAY


def test_absent_terdeteksi_dari_attendance():
    assert resolve(rec(att(status="ABSENT", approval_status="SUBMITTED"), log(state="COMPLETED"))) is ABSENT


def test_needs_revision_sebelum_rejected():
    record = rec(att(status="PRESENT", approval_status="REJECTED"), log(state="NEEDS_REVISION"))
    assert resolve(record) is NEEDS_REVISION


def test_rejected_ditanpa_revision():
    record = rec(att(status="PRESENT", approval_status="REJECTED"), log(state="COMPLETED"))
    assert resolve(record) is REJECTED


def test_revision_submitted():
    record = rec(att(status="PRESENT", approval_status="SUBMITTED"), log(state="REVISION_SUBMITTED"))
    assert resolve(record) is REVISION_SUBMITTED


def test_completed_jadi_menunggu_persetujuan():
    record = rec(att(status="PRESENT", approval_status="SUBMITTED"), log(state="COMPLETED"))
    assert resolve(record) is PENDING_REVIEW


def test_aproved_hadir():
    record = rec(att(status="PRESENT", approval_status="APPROVED"), log(state="APPROVED"))
    assert resolve(record) is APPROVED


def test_aproved_izin():
    record = rec(att(status="ON_LEAVE", approval_status="APPROVED"), log(state="APPROVED"))
    assert resolve(record) is APPROVED_LEAVE


def test_aproved_hanya_dari_log_state():
    """Kasus nyata: approval_status attendance belum ada, tapi log sudah APPROVED."""
    record = rec(attendance=None, daily={"state": "APPROVED"})
    assert resolve(record) is APPROVED


def test_kosong_sama_sekali():
    assert resolve(rec()) is EMPTY


def test_hari_libur_dari_home_saat_attendance_belum_ada():
    assert resolve(rec(holiday=True)) is HOLIDAY
    assert resolve(rec(off=True)) is HOLIDAY


def test_sudah_absen_tapi_uraian_kosong():
    record = rec(att(status="PRESENT", approval_status=None), daily=None)
    assert resolve(record) is ATTENDED_NO_LOG


def test_draf_dianggap_ch_attended_no_log():
    record = rec(att(status="PRESENT", approval_status=None), log(state="DRAFT"))
    assert resolve(record) is ATTENDED_NO_LOG


def test_status_asing_jadi_unknown():
    record = rec(att(status="PRESENT", approval_status=None), log(state="WEIRD_STATE"))
    assert resolve(record) is UNKNOWN


def test_mentor_action_pending():
    assert mentor_action_pending({"approval_status": "SUBMITTED", "status": "PRESENT"}) is True
    assert mentor_action_pending({"approval_status": "SUBMITTED", "status": "DAYOFF"}) is False
    assert mentor_action_pending({"approval_status": "APPROVED", "status": "PRESENT"}) is False
    assert mentor_action_pending(None) is False


def test_log_badge():
    assert log_badge("COMPLETED") == "Selesai"
    assert log_badge("DRAFT") == "Draf"
    assert log_badge(None) == "Belum Diisi"
