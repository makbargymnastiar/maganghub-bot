"""Util bersama untuk semua modul pesan: escape HTML + format tanggal Indonesia."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

WEEKDAY_ID = {
    0: "Senin",
    1: "Selasa",
    2: "Rabu",
    3: "Kamis",
    4: "Jumat",
    5: "Sabtu",
    6: "Minggu",
}

MONTHS_ID = [
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]

MONTHS_SHORT = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "Mei",
    "Jun",
    "Jul",
    "Agu",
    "Sep",
    "Okt",
    "Nov",
    "Des",
]


def esc(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=False)


def _parse(iso_date: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso_date)
    except (ValueError, TypeError):
        return None


def pretty_date(iso_date: str) -> str:
    """'2026-11-03' → 'Selasa, 03 November 2026' (nama hari/bulan Indonesia)."""
    dt = _parse(iso_date)
    if dt is None:
        return esc(iso_date)
    return f"{WEEKDAY_ID[dt.weekday()]}, {dt.day:02d} {MONTHS_ID[dt.month - 1]} {dt.year}"


def short_date(iso_date: str) -> str:
    """'2026-10-27' → '27 Okt' — untuk daftar tanggal panjang (mis. tagihan ACC)."""
    dt = _parse(iso_date)
    if dt is None:
        return esc(iso_date)
    return f"{dt.day:02d} {MONTHS_SHORT[dt.month - 1]}"


def numeric_date(iso_date: str) -> str:
    """'2026-11-04' → '04/11' — pasangan nama hari di daftar riwayat (/rekap)."""
    dt = _parse(iso_date)
    if dt is None:
        return esc(iso_date)
    return f"{dt.day:02d}/{dt.month:02d}"


def day_name(iso_date: str) -> str:
    """'2026-11-03' → 'Selasa' — nama hari Indonesia saja."""
    dt = _parse(iso_date)
    if dt is None:
        return ""
    return WEEKDAY_ID[dt.weekday()]
