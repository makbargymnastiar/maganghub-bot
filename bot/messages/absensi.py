"""Notif terkait status absensi: perubahan status, revisi, pengumuman portal."""

from __future__ import annotations

from ..monev.status import Label
from .base import esc, pretty_date


def status_change_message(label: Label, log_date: str, mentor_notes: str = "") -> str:
    """Satu format untuk SEMUA status: judul, tanggal, catatan mentor ('-' bila kosong).

    Template kalimat fix (review user 2026-11): "📊 PERUBAHAN STATUS ABSENSI" +
    baris status/tanggal/catatan; hanya revisi yang dapat petunjuk tindakan.
    """
    notes = (mentor_notes or "").strip()
    lines = [
        "📊 <b>PERUBAHAN STATUS ABSENSI</b>",
        "",
        f"{label.emoji} Status: <b>{esc(label.title)}</b>",
        f"📅 Tanggal: {pretty_date(log_date)}",
        f"💬 Catatan Mentor: <i>{esc(notes) if notes else '-'}</i>",
    ]
    if label.key == "NEEDS_REVISION":
        lines.append("👉 Perbaiki logbook lalu kirim ulang di portal.")
    return "\n".join(lines)


def revision_followup_message(log_date: str, hours: int) -> str:
    return (
        "🟠 <b>REVISI BELUM DIKERJAKAN</b>\n\n"
        f"📅 Tanggal: {pretty_date(log_date)}\n"
        f"⏳ Logbook diminta revisi oleh mentor dan sudah berlalu {hours} jam "
        "tanpa perubahan.\n\n"
        "Segera perbaiki dan kirim ulang di portal."
    )


def unknown_status_message(label_raw: str, log_date: str) -> str:
    return (
        "❔ <b>Status belum dikenal bot</b>\n"
        f"Tanggal: {pretty_date(log_date)}\n"
        f"Klaim API: <code>{esc(label_raw)}</code>\n"
        "Silakan cek portal; mapping bot perlu dikalibrasi bila ini status baru."
    )


def announcement_message(title: str, content: str, kind: str) -> str:
    badge = "📢" if kind.upper() == "INFO" else "⚠️"
    body = content if len(content) <= 1200 else content[:1200] + "…"
    return f"{badge} <b>{esc(title)}</b>\n\n{esc(body)}"


def warning_message(text: str) -> str:
    return f"🚩 <b>Warning keterlambatan absensi</b>\n{esc(text)}"
