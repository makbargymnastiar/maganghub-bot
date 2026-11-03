"""Tagihan ACC mentor: absensi yang menggantung 'Menunggu' terlalu lama."""

from __future__ import annotations

from .base import short_date


def pending_mentor_message(dates: list[str], days: int) -> str:
    """Daftar tanggal yang sudah ≥5 hari menunggu persetujuan mentor (dipantau
    tiap pagi oleh jobs/reminders.py; berhenti sendiri begitu di-ACC)."""
    shown = ", ".join(short_date(d) for d in dates[:10])
    if len(dates) > 10:
        shown += f", … ({len(dates) - 10} lainnya)"
    return (
        f"⏳ <b>Menunggu ACC Mentor — sudah {days} hari</b>\n"
        f"📅 Tanggal: {shown}\n"
        f"Sudah {days}+ hari tanpa persetujuan mentor.\n"
        "Bot cek ulang otomatis tiap siklus polling — pengingat berhenti sendiri begitu di-ACC."
    )
