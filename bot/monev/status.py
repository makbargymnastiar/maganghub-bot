"""Logika status harian — port langsung dari fungsi `i(e, t, r)` frontend monev.

Frontend menggabungkan DUA objek: `e` = attendance, `t` = daily-log.
Itu sebabnya bot harus membaca `/attendances` dan `/daily-logs` sekaligus;
kode lama hanya membaca daily-log sehingga aturan yang bergantung pada
`attendance.status` / `attendance.approval_status` tidak pernah aktif.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import LOG_FIELDS, DayRecord

# --- Klaim enum yang terkonfirmasi dari JS frontend + data API -----------------
ATT_DAYOFF = "DAYOFF"
ATT_PRESENT = "PRESENT"
ATT_ABSENT = "ABSENT"
ATT_ON_LEAVE = "ON_LEAVE"
ATT_APPROVED = "APPROVED"
ATT_REJECTED = "REJECTED"
ATT_SUBMITTED = "SUBMITTED"

LOG_NEEDS_REVISION = "NEEDS_REVISION"
LOG_REVISION_SUBMITTED = "REVISION_SUBMITTED"
LOG_COMPLETED = "COMPLETED"
LOG_DRAFT = "DRAFT"


@dataclass(frozen=True)
class Label:
    key: str
    emoji: str
    title: str  # judul pendek untuk chat (bukan teks persis portal)

    @property
    def is_filled(self) -> bool:
        """Sudah tidak layak ditagih reminder pengisian."""
        return self.key not in NAGGABLE_KEYS


# Status yang masih boleh ditagih reminder: isian belum lengkap.
# Catatan: dulu ada FILLED_NOT_SUBMITTED ("sudah diisi tapi belum dikirim") —
# di portal sebenarnya tidak ada tahap itu: lengkap = terkirim, jadi label dihapus.
NAGGABLE_KEYS = frozenset({"EMPTY", "ATTENDED_NO_LOG", "PARTIAL"})


APPROVED = Label("APPROVED", "✅", "Hadir Disetujui")
APPROVED_LEAVE = Label("APPROVED_LEAVE", "🟢", "Izin Disetujui")
ABSENT = Label("ABSENT", "⛔", "Tidak Hadir")
REJECTED = Label("REJECTED", "❌", "Ditolak")
NEEDS_REVISION = Label("NEEDS_REVISION", "🟠", "Perlu Revisi")
REVISION_SUBMITTED = Label("REVISION_SUBMITTED", "⏳", "Perbaikan Menunggu Peninjauan")
PENDING_REVIEW = Label("PENDING_REVIEW", "📝", "Menunggu ACC Mentor")
PARTIAL = Label("PARTIAL", "🟡", "Sebagian Terisi")
EMPTY = Label("EMPTY", "⚪", "Belum Diisi")
HOLIDAY = Label("HOLIDAY", "🗓️", "Hari Libur")
ATTENDED_NO_LOG = Label("ATTENDED_NO_LOG", "🔵", "Sudah Absen, Uraian Belum Diisi")
UNKNOWN = Label("UNKNOWN", "❔", "Status Tidak Dikenal")

ALL_LABELS: dict[str, Label] = {
    lab.key: lab
    for lab in (
        APPROVED,
        APPROVED_LEAVE,
        ABSENT,
        REJECTED,
        NEEDS_REVISION,
        REVISION_SUBMITTED,
        PENDING_REVIEW,
        PARTIAL,
        EMPTY,
        HOLIDAY,
        ATTENDED_NO_LOG,
        UNKNOWN,
    )
}


def resolve(record: DayRecord) -> Label:
    """Urutan cek mengikuti frontend; baris 1 paling tinggi prioritasnya.

    Satu deviasi sadar (nomor 7): frontend melabeli "Hadir disetujui" begitu
    `attendance.approval_status == APPROVED`, padahal uraian harian masih boleh
    kosong. Untuk bot pengingat, selama aktivitas/pembelajaran/kendala belum
    lengkap, pekerjaan peserta belum selesai — jadi label kelengkapan menang,
    kecuali hari izin (uraian tidak ditagih).
    """
    att = record.attendance
    state = record.log_state

    # 1. attendance.status == DAYOFF
    if record.attendance_status == ATT_DAYOFF:
        return HOLIDAY
    # 1b. perluasan: tidak ada data attendance tapi portal menandai libur/tidak terjadwal
    if att is None and (record.is_holiday or record.is_off_day):
        return HOLIDAY
    # 2. attendance.status == ABSENT
    if record.attendance_status == ATT_ABSENT:
        return ABSENT
    # 3. log.state == NEEDS_REVISION
    if state == LOG_NEEDS_REVISION:
        return NEEDS_REVISION
    # 4. attendance.approval_status == REJECTED
    if record.approval_status == ATT_REJECTED:
        return REJECTED
    # 5. log.state == REVISION_SUBMITTED
    if state == LOG_REVISION_SUBMITTED:
        return REVISION_SUBMITTED
    # 6. log.state == COMPLETED  (sudah final, menunggu ACC)
    if state == LOG_COMPLETED:
        return PENDING_REVIEW
    # 7. isian harian belum final — dahulukan daripada "hadir disetujui" (lihat docstring)
    if state in (None, LOG_DRAFT) and record.attendance_status != ATT_ON_LEAVE:
        if record.content_complete:
            # Portal tidak punya tahap "belum dikirim": lengkap = terkirim.
            return PENDING_REVIEW
        if record.filled_count:
            return PARTIAL
        # Hadir di hari kerja (atau draf tanpa isi) tapi laporan belum ada isinya.
        if record.has_log or record.attendance_status == ATT_PRESENT:
            return ATTENDED_NO_LOG
    # 8. attendance.approval_status == APPROVED
    if record.approval_status == ATT_APPROVED:
        return APPROVED_LEAVE if record.attendance_status == ATT_ON_LEAVE else APPROVED
    # 8b. laporan sudah di-ACC tapi attendance belum/tdk ada status approval
    if state == ATT_APPROVED:
        return APPROVED_LEAVE if record.attendance_status == ATT_ON_LEAVE else APPROVED
    # 9. attendance.approval_status == SUBMITTED
    if record.approval_status == ATT_SUBMITTED:
        return PENDING_REVIEW

    # 10. fallback frontend = "Belum Diisi", dengan pengecualian informatif:
    #    kehadiran sudah ada tapi uraian masih DRAFT/kosong.
    if att is not None and state in (None, LOG_DRAFT):
        return ATTENDED_NO_LOG
    if state is None and att is None:
        return EMPTY
    if state not in (
        LOG_DRAFT,
        LOG_COMPLETED,
        LOG_NEEDS_REVISION,
        LOG_REVISION_SUBMITTED,
        ATT_APPROVED,
    ):
        return UNKNOWN
    return EMPTY


def unknown_detail(record: DayRecord) -> str:
    """Klaim mentah yang memicu UNKNOWN, untuk kalibrasi mapping."""
    parts = []
    if record.attendance_status:
        parts.append(f"attendance.status={record.attendance_status}")
    if record.approval_status:
        parts.append(f"attendance.approval_status={record.approval_status}")
    if record.log_state:
        parts.append(f"log.state={record.log_state}")
    return ", ".join(parts) or "(semua kosong)"


def pending_detail(record: DayRecord) -> str:
    """Baris pelengkap notifikasi: catatan mentor atau isian yang masih kosong."""
    if record.log_state == LOG_NEEDS_REVISION and record.reviewer_notes:
        return f"Catatan mentor: {record.reviewer_notes}"
    missing = record.missing_fields
    if record.log is not None and missing and len(missing) < len(LOG_FIELDS):
        return "Masih kosong: " + ", ".join(missing)
    return ""


def mentor_action_pending(attendance: dict[str, Any] | None) -> bool:
    """Port helper `a(e)` frontend: menunggu tindakan mentor."""
    if not attendance:
        return False
    status = attendance.get("status")
    return attendance.get("approval_status") == ATT_SUBMITTED and status not in (
        ATT_ABSENT,
        ATT_DAYOFF,
    )


def log_badge(state: str | None) -> str:
    """Port helper `o(state)` untuk badge uraian aktivitas."""
    if state == LOG_COMPLETED:
        return "Selesai"
    if state == LOG_DRAFT:
        return "Draf"
    return "Belum Diisi"
