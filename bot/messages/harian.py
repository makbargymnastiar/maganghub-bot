"""Pengingat pagi 08:00 yang tidak terkait kelengkapan isian: hari libur,
evaluasi magang H-7..hari-H, dan pengajuan uang saku tanggal 9."""

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
    """(A) Tanggal 2–8: pengingat H-7..H-1 evaluasi bulanan mentor."""
    return (
        f"📊 <b>PENGINGAT EVALUASI MENTOR (H-{days_left})</b>\n\n"
        "📅 Tanggal 9 Semakin Dekat\n"
        "⚠️ Evaluasi bulanan wajib terisi oleh mentor.\n\n"
        "💡 Ingatkan mentor kamu secara santun hari ini jika belum diisi ya!"
    )


def eval_due_message() -> str:
    """(B) Tanggal 9 pagi: hari-H batas akhir pengisian evaluasi oleh mentor."""
    return (
        "📊 <b>HARI-H EVALUASI MENTOR (TANGGAL 9)</b>\n\n"
        "📅 Hari Ini Tanggal 9 (Batas Akhir)!\n"
        "🚨 Hari terakhir pengisian evaluasi bulanan oleh mentor.\n\n"
        "👉 Segera cek portal &amp; pastikan mentor kamu sudah mengisinya hari ini!"
    )


def stipend_message() -> str:
    """Tanggal 9: hari-H pengajuan uang saku."""
    return (
        "💸 <b>HARI-H PENGAJUAN UANG SAKU (TANGGAL 9)</b>\n\n"
        "Sudah pukul 08:00 WIB. Ajukan uang saku magangmu di portal SEKARANG — "
        "biasanya ada batas waktu pengisian di tanggal 9.\n"
        "Pastikan evaluasi mentor sudah terisi dulu, baru submit pengajuan."
    )
