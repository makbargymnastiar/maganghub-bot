"""Notifikasi pengajuan uang saku mentor: alert instan + rincian estimasi."""

from __future__ import annotations

from ..models import StipendEstimate
from .base import month_year_id, rupiah


def claim_pending_message(period: str) -> str:
    """Cek manual saat mentor belum mengajukan — status jujur, tanpa angka karangan."""
    return (
        "⏳ <b>UANG SAKU BELUM DIAJUKAN</b>\n\n"
        f"📅 Periode: {month_year_id(period)}\n"
        "📌 Status: ⚠️ Belum diajukan mentor"
    )


def claim_alert_message() -> str:
    """Pesan 1 — saat klaim mentor pertama kali terdeteksi."""
    return (
        "💸 <b>NOTIFIKASI UANG SAKU MAGANGHUB</b>\n\n"
        "✨ Mentor kamu baru saja mengajukan klaim uang saku bulanan di portal monev!\n\n"
        "Sedang menghitung estimasi pencairan... ⏳"
    )


def claim_detail_message(est: StipendEstimate) -> str:
    """Pesan 2 — 2A kalau kehadiran 100%, 2B kalau ada hari mangkir."""
    head = (
        "💰 <b>RINCIAN PERHITUNGAN UANG SAKU</b>\n\n"
        f"📅 Periode: {month_year_id(est.period)}\n"
        f"📍 Wilayah Acuan: {est.region} (UMP {rupiah(est.ump)})\n\n"
        "📊 Rincian Kehadiran:\n"
    )
    if est.perfect:
        return head + (
            "• Persentase Kehadiran: 100% (Sempurna)\n"
            "• Pengurangan Uang Saku: Rp0\n\n"
            "💵 Uang Saku Diterima:\n"
            f"{rupiah(est.diterima)} (Utuh)\n\n"
            "📌 Catatan: Uang saku dikirim langsung oleh Kemnaker ke rekening terdaftar. "
            "Pastikan data rekening di portal sudah valid!"
        )
    return head + (
        f"• Persentase Kehadiran: {est.percent}% ({est.payable_days}/{est.total_days} Hari)\n"
        f"• Ketidakhadiran (Mangkir): {est.absent_days} Hari\n"
        f"• Pengurangan Uang Saku: -{rupiah(est.potongan)}\n\n"
        "💵 Uang Saku Diterima:\n"
        f"{rupiah(est.diterima)}\n\n"
        "📌 Catatan: Izin sakit dengan surat dokter dan libur resmi tidak memotong nominal uang saku."
    )
