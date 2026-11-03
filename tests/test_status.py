"""Uji precedence status — meniru urutan fungsi `i(e, t, r)` frontend monev."""

from bot.models import DayRecord
from bot.status import (
    ABSENT,
    APPROVED,
    APPROVED_LEAVE,
    EMPTY,
    FILLED_NOT_SUBMITTED,
    HOLIDAY,
    NEEDS_REVISION,
    NAGGABLE_KEYS,
    PARTIAL,
    PENDING_REVIEW,
    REJECTED,
    REVISION_SUBMITTED,
    UNKNOWN,
    ATTENDED_NO_LOG,
    log_badge,
    mentor_action_pending,
    pending_detail,
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


# --- kelengkapan isi: aktivitas / pembelajaran / kendala ----------------------

COMPLETE = {"activity_log": "Merakit PC", "lesson_learned": "Belajar ESD", "obstacles": ""}
FULL = {"activity_log": "Merakit PC", "lesson_learned": "Belajar ESD", "obstacles": "Kabel kurang"}


def test_draf_sudah_terisi_lengkap_dilapor_sudah_diisi():
    """User mengisi tiga kolom tapi belum tekan kirim -> label "Sudah Diisi, Belum Dikirim"."""
    record = rec(att(status="PRESENT", approval_status=None), daily={"state": "DRAFT", **FULL})
    assert resolve(record) is FILLED_NOT_SUBMITTED


def test_sebagian_terisi_bukan_belum_diisi():
    record = rec(att(status="PRESENT", approval_status=None), daily={"state": "DRAFT", **COMPLETE})
    assert resolve(record) is PARTIAL
    assert record.missing_fields == ["kendala"]
    assert record.filled_count == 2
    assert record.content_complete is False


def test_kendala_spasi_saja_dianggap_kosong():
    blank = {"activity_log": "A", "lesson_learned": "B", "obstacles": "   "}
    record = rec(att(status="PRESENT"), daily={"state": "DRAFT", **blank})
    assert resolve(record) is PARTIAL


def test_keputusan_mentor_tetap_menang_isian():
    """Status final (APPROVED) tidak boleh kalah oleh label kelengkapan isi."""
    record = rec(att(status="PRESENT", approval_status="APPROVED"), daily={"state": "APPROVED", **FULL})
    assert resolve(record) is APPROVED


def test_kehadiran_disetujui_tapi_uraian_kosong_masih_ditagih():
    """Deviasi sadar dari frontend: ACC kehadiran != laporan harian selesai."""
    approved_blank = rec(att(status="PRESENT", approval_status="APPROVED"), daily={"state": "DRAFT"})
    assert resolve(approved_blank) is ATTENDED_NO_LOG
    partial = rec(att(status="PRESENT", approval_status="APPROVED"), daily={"state": "DRAFT", **COMPLETE})
    assert resolve(partial) is PARTIAL


def test_hari_izin_tidak_ditagih_uraian():
    """ON_LEAVE yang di-ACC tidak punya uraian yang perlu ditagih."""
    record = rec(att(status="ON_LEAVE", approval_status="APPROVED"), daily=None)
    assert resolve(record) is APPROVED_LEAVE


def test_draf_tanpa_attendance_tetap_terlihat_kelengkapannya():
    record = rec(attendance=None, daily={"state": "DRAFT", **FULL})
    assert resolve(record) is FILLED_NOT_SUBMITTED


def test_pending_detail_sebut_isian_yang_kosong():
    record = rec(att(status="PRESENT"), daily={"state": "DRAFT", **COMPLETE})
    assert pending_detail(record) == "Masih kosong: kendala"


def test_pending_detail_bawa_catatan_mentor_saat_revisi():
    record = rec(
        att(status="PRESENT"),
        daily={"state": "NEEDS_REVISION", "reviewer_notes": "Perjelas bagian kendala", **FULL},
    )
    assert pending_detail(record) == "Catatan mentor: Perjelas bagian kendala"


def test_pending_detail_kosong_saat_semua_rapi():
    assert pending_detail(rec(att(status="PRESENT"), daily={"state": "DRAFT", **FULL})) == ""


def test_status_yang_ditagih_reminder():
    assert NAGGABLE_KEYS == frozenset({"EMPTY", "ATTENDED_NO_LOG", "PARTIAL", "FILLED_NOT_SUBMITTED"})
    assert FILLED_NOT_SUBMITTED.is_filled is False
    assert PARTIAL.is_filled is False
    assert APPROVED.is_filled is True
    assert PENDING_REVIEW.is_filled is True
