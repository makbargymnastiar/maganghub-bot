"""Pengingat pengisian laporan harian — berjenjang menuju tenggat 00:00.

Jadwal (terkunci, logikanya di jobs/reminders.py):
- 16:55        cek sore, 1x: menagih yang belum / menandai yang sudah
- 18:00–20:59  tiap 30 menit selama belum lengkap
- 21:00–23:29  tiap 15 menit (jam rawan ngantuk)
- 23:30–23:45  tiap 5 menit
- 23:46–23:59  tiap 3 menit
- 00:01        1x: laporan kemarin terlewat
"""

from __future__ import annotations

from .base import esc, pretty_date


def missing_clause(missing: str = "") -> str:
    """Pelengkap inline: ' (kendala masih kosong).' — titik dalam kurawal template user."""
    tail = f" ({esc(missing)} masih kosong)" if missing else ""
    return f"{tail}."


def morning_message(log_date: str) -> str:
    """08:00, sekali per hari kerja: pengingat pagi sebelum mulai kerja."""
    return (
        "🌅 <b>PENGINGAT PAGI ABSENSI</b>\n\n"
        f"📅 {pretty_date(log_date)}\n"
        "Yuk catat logbook hari ini:\n"
        "• Uraian Aktivitas\n"
        "• Pembelajaran\n"
        "• Kendala\n\n"
        "Isi lebih awal biar malam santai! 👍\n"
        "Bismillah, selamat beraktivitas! 💪"
    )


def reminder_soft(log_date: str, missing: str = "") -> str:
    """Level 1 (18:00–20:59) — masih santai."""
    return (
        "🔔 <b>PENGINGAT ABSENSI</b>\n\n"
        f"📅 {pretty_date(log_date)}\n"
        f"⚠️ Kamu belum melengkapi absensi magang hari ini{missing_clause(missing)}\n\n"
        "Yuk luangkan waktu sebentar untuk isi absensi di portal monev, "
        "jangan menunda sampai malam."
    )


def reminder_rawan(log_date: str) -> str:
    """Level 2 (21:00–23:29) — jam rawan."""
    return (
        "⏰ <b>JAM RAWAN LUPA ABSENSI</b>\n\n"
        f"📅 {pretty_date(log_date)}\n"
        "⚠️ Pengingat Malam: Absensi magang kamu hari ini masih KOSONG!\n\n"
        "Sudah larut malam, segera buka portal dan lengkapi absensimu sekarang "
        "agar tidak ketiduran."
    )


def reminder_dekat(log_date: str) -> str:
    """Level 3 (23:30–23:45) — tenggat dekat."""
    return (
        "🚨 <b>TENGGAT ABSENSI SEMAKIN DEKAT</b>\n\n"
        f"📅 {pretty_date(log_date)}\n"
        "⚠️ Peringatan Darurat: Absensi harian belum juga diisi!\n\n"
        "Sisa waktu tinggal sedikit lagi sebelum batas pergantian hari. Isi SEKARANG!"
    )


def reminder_mepet(log_date: str) -> str:
    """Level 4 (23:46–23:59) — hitungan menit."""
    return (
        "🆘 <b>TENGGAT ABSENSI CRITICAL!</b>\n\n"
        f"📅 {pretty_date(log_date)}\n"
        "🚨 Absensi hari ini belum tercatat! Lewat pukul 00:00 WIB, sistem akan "
        "otomatis mengunci portal dan absensi hangus!"
    )


def sore_check(log_date: str, complete: bool, missing: str = "") -> str:
    """Cek sore 16:55 — dua kondisi, tetap dikirim walau sudah terisi (penanda)."""
    if complete:
        return (
            "🕠 <b>CEK MONEV SORE</b>\n\n"
            f"📅 {pretty_date(log_date)}\n"
            "Status: ✅ Logbook Harian Lengkap\n\n"
            "📌 Uraian Aktivitas, Pembelajaran, dan Kendala sudah terisi.\n"
            "Sudah aman — tinggal menunggu ACC mentor. Tidak perlu isi apa-apa "
            "lagi hari ini."
        )
    return (
        "🕠 <b>CEK MONEV SORE</b>\n\n"
        f"📅 {pretty_date(log_date)}\n"
        "Status: ⚠️ Absensi Hari Ini Belum Diisi\n\n"
        f"📌 Catatan harian belum tercatat{missing_clause(missing)}\n"
        "Isi dulu sekarang sebelum sibuk! Nanti malam tinggal menunggu ACC mentor."
    )


def missed_yesterday(yesterday: str, today: str) -> str:
    """00:01, sekali: kemarin lolos tanpa terisi."""
    return (
        "❗ <b>ABSENSI TERLEWAT</b>\n\n"
        f"📅 {pretty_date(yesterday)}\n"
        "❌ Absensi kemarin terlewat tanpa pengisian dan tidak bisa disusulkan lagi.\n\n"
        f"💡 Jadikan evaluasi untuk hari ini ({pretty_date(today)}): usahakan isi "
        "absensi sebelum jam 18:00 sore supaya tidak terulang. 🙏"
    )
