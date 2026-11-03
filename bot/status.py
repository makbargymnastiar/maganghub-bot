"""Logika status harian — port langsung dari fungsi `i(e, t, r)` frontend monev.

Frontend menggabungkan DUA objek: `e` = attendance, `t` = daily-log.
Itu sebabnya bot harus membaca `/attendances` dan `/daily-logs` sekaligus;
kode lama hanya membaca daily-log sehingga aturan yang bergantung pada
`attendance.status` / `attendance.approval_status` tidak pernah aktif.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import DayRecord

# --- Klaim enum yang terkonfirmasi dari JS frontend + data API -----------------
ATT_DAYOFF = "DAYOFF"
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
    title: str  # label resmi frontend (perspektif "peserta")
    action: str  # apa yang perlu dilakukan pemagang

    @property
    def is_filled(self) -> bool:
        """Sudah tidak layak ditagih reminder pengisian."""
        return self.key not in (EMPTY.key, ATTENDED_NO_LOG.key)


APPROVED = Label("APPROVED", "✅", "Hadir disetujui", "Tidak ada tindakan.")
APPROVED_LEAVE = Label("APPROVED_LEAVE", "🟢", "Izin disetujui", "Tidak ada tindakan.")
ABSENT = Label("ABSENT", "⛔", "Tidak Hadir", "Catatan: hari ini tercatat tidak hadir.")
REJECTED = Label("REJECTED", "❌", "Kehadiran Ditolak", "Periksa portal monev, ajukan ulang bila perlu.")
NEEDS_REVISION = Label(
    "NEEDS_REVISION", "🟠", "Perlu Tindakan Anda", "Uraian aktivitas diminta diperbaiki mentor."
)
REVISION_SUBMITTED = Label(
    "REVISION_SUBMITTED", "⏳", "Perbaikan Menunggu Peninjauan", "Tunggu mentor meninjau perbaikan."
)
PENDING_REVIEW = Label(
    "PENDING_REVIEW", "📝", "Menunggu Persetujuan", "Laporan terkirim, tunggu ACC mentor."
)
EMPTY = Label("EMPTY", "⚪", "Belum Diisi", "Isi absensi dan uraian aktivitas hari ini.")
HOLIDAY = Label("HOLIDAY", "🗓️", "Hari Libur", "Tidak ada pengisian pada hari libur.")
ATTENDED_NO_LOG = Label(
    "ATTENDED_NO_LOG",
    "🔵",
    "Sudah Absen, Uraian Belum Diisi",
    "Kehadiran sudah tercatat, tinggal isi uraian aktivitas.",
)
UNKNOWN = Label("UNKNOWN", "❔", "Status Tidak Dikenal", "Periksa portal monev.")

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
        EMPTY,
        HOLIDAY,
        ATTENDED_NO_LOG,
        UNKNOWN,
    )
}


def resolve(record: DayRecord) -> Label:
    """Urutan cek sama persis dengan frontend (baris 1 paling tinggi prioritasnya)."""
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
    # 7. attendance.approval_status == APPROVED
    if record.approval_status == ATT_APPROVED:
        return APPROVED_LEAVE if record.attendance_status == ATT_ON_LEAVE else APPROVED
    # 7b. laporan sudah di-ACC tapi attendance belum/tdk ada status approval
    if state == ATT_APPROVED:
        return APPROVED_LEAVE if record.attendance_status == ATT_ON_LEAVE else APPROVED
    # 8. attendance.approval_status == SUBMITTED
    if record.approval_status == ATT_SUBMITTED:
        return PENDING_REVIEW

    # 9. fallback frontend = "Belum Diisi", dengan pengecualian informatif:
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
