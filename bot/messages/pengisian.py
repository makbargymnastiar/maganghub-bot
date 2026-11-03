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


def reminder_soft(log_date: str) -> str:
    """Level 1 (18:00–20:59) — masih santai."""
    return (
        "🔔 <b>Pengingat Absensi</b>\n"
        f"📅 {pretty_date(log_date)} — aktivitas, pembelajaran, kendala belum lengkap.\n"
        "Isi di portal monev ya, jangan menunggu malam."
    )


def reminder_rawan(log_date: str) -> str:
    """Level 2 (21:00–23:29) — jam rawan."""
    return (
        "⏰ <b>Jam Rawan — Pengingat Absensi</b>\n"
        f"📅 {pretty_date(log_date)} masih BELUM terisi lengkap.\n"
        "Isi sekarang biar tidak lewat tenggat."
    )


def reminder_dekat(log_date: str) -> str:
    """Level 3 (23:30–23:45) — tenggat dekat."""
    return (
        "🚨 <b>Pengingat Absensi — tenggat semakin dekat</b>\n"
        f"📅 {pretty_date(log_date)} masih belum lengkap.\n"
        "Isi SEKARANG ya!"
    )


def reminder_mepet(log_date: str) -> str:
    """Level 4 (23:46–23:59) — hitungan menit."""
    return (
        "🆘 <b>Tenggat mepet!</b>\n"
        f"Absensi {pretty_date(log_date)} belum terisi — "
        "lewat 00:00 tidak bisa diisi lagi!"
    )


def sore_check(log_date: str, complete: bool, missing: str = "") -> str:
    """Cek sore 16:55 — dua kondisi, tetap dikirim walau sudah terisi (penanda)."""
    if complete:
        return (
            "🕠 <b>Cek Monev Sore — 16:55</b>\n"
            f"📅 {pretty_date(log_date)}: ✅ sudah terisi lengkap "
            "(aktivitas, pembelajaran, kendala).\n"
            "Sudah aman — tinggal menunggu ACC mentor. Tidak perlu isi apa-apa lagi hari ini."
        )
    tail = f" ({esc(missing)} masih kosong)" if missing else ""
    return (
        "🕠 <b>Cek Monev Sore — 16:55</b>\n"
        f"📅 {pretty_date(log_date)}: belum lengkap{tail}.\n"
        "Isi dulu sekarang sebelum sibuk — nanti malam tinggal ACC mentor."
    )


def missed_yesterday(yesterday: str, today: str) -> str:
    """00:01, sekali: kemarin lolos tanpa terisi."""
    return (
        "❗ <b>Kemarin Terlewat</b>\n"
        f"Absensi {pretty_date(yesterday)} lewat tanpa terisi — tidak bisa disusulkan.\n"
        f"Fokus hari ini {pretty_date(today)}: isi sebelum jam 18:00 supaya tidak terulang. 🙏"
    )
