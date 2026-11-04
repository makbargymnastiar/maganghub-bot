"""Pengingat pagi 08:00 yang tidak terkait kelengkapan isian: hari libur,
evaluasi magang H-7, dan pengajuan uang saku tanggal 9."""

from __future__ import annotations

from .base import pretty_date


def day_off_message(log_date: str, from_portal: bool = False) -> str:
    """Hari ini tidak perlu mengisi monev (akhir pekan atau libur dari portal)."""
    day = pretty_date(log_date)
    if from_portal:
        return (
            "🗓️ <b>HARI INI LIBUR</b>\n\n"
            f"📅 {day} — tidak ada pengisian monev harian hari ini.\n"
            "Selamat beristirahat! 💤"
        )
    return (
        "🗓️ <b>HARI INI LIBUR</b>\n\n"
        f"📅 {day} — tidak ada pengisian monev harian hari ini.\n"
        "Selamat beristirahat! 💤 Pengingat aktif lagi Senin pagi."
    )


def eval_reminder_message(days_left: int) -> str:
    """Tanggal 2–8: mengingatkan evaluasi mentor menjelang pengajuan tanggal 9."""
    return (
        f"📊 <b>EVALUASI MAGANG — H-{days_left} (TANGGAL 9)</b>\n\n"
        "Mentor harus mengisi evaluasi magang sebelum periode pengajuan "
        "uang saku (Tanggal 9).\n"
        "Ingatkan mentor hari ini kalau evaluasinya belum terisi."
    )


def stipend_message() -> str:
    """Tanggal 9: hari-H pengajuan uang saku."""
    return (
        "💸 <b>HARI-H PENGAJUAN UANG SAKU (TANGGAL 9)</b>\n\n"
        "Sudah pukul 08:00 WIB. Ajukan uang saku magangmu di portal SEKARANG — "
        "biasanya ada batas waktu pengisian di tanggal 9.\n"
        "Pastikan evaluasi mentor sudah terisi dulu, baru submit pengajuan."
    )
