"""Status pengajuan uang saku + estimasi perhitungannya.

Nama endpoint dan field TIDAK ditebak: digali dari bundle SPA portal
(`scripts/discover_endpoints.py --grep 'payment/...'`) — frontend memanggil
`GET /payment/claims` dengan `{participant_id, schedule_id, period_start}` dan
membaca `total_working_days`, `payable_days`, `amount`, `submitted_at` dari
baris klaim. Bentuk persis responsnya belum dikunci live (sama seperti
/monthly-reports dulu), jadi parsernya toleran beberapa varian — tapi tidak
pernah mengarang angka: hari yang tidak terbaca = 0, bukan tebakan.

Konvensi perhitungan (wilayah acuan DKI Jakarta):
- Potongan per hari mangkir   = UMP / total hari kerja
- Pengurangan uang saku       = hari mangkir * potongan per hari
- Uang saku diterima          = UMP - pengurangan
Kalau portal menyertakan `amount`, angka itu yang jadi pegangan — ia hasil
hitung server dan sudah pasti memakai UMK wilayah peserta.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..models import StipendClaim, StipendEstimate
from .b64 import maybe_b64_json
from .evaluation import period_window

WILAYAH = "DKI Jakarta"
UMP_DKI = 5_729_876

# Ada minimal satu dari ini baru sebuah dict dianggap baris klaim.
_CLAIM_FIELDS = ("payable_days", "total_working_days", "submitted_at", "amount")
_PERIOD_KEYS = ("period_start", "periodstart", "start_date", "month")
_PERIOD_RX = re.compile(r"^\d{4}-\d{2}")


def _as_int(value: Any) -> int | None:
    """Int dari int/float/str ('5.729.876', '5729876', 'Rp5.729.876'); selain itu None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


def _period_of(row: dict[str, Any]) -> str:
    """Periode yang dideklarasikan baris ('YYYY-MM') — '' kalau tidak menyebut."""
    for key in _PERIOD_KEYS:
        text = str(row.get(key) or "")[:7]
        if _PERIOD_RX.match(text):
            return text
    return ""


def _collect_rows(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, str) and node.strip().startswith(("{", "[")):
        try:
            node = json.loads(node)
        except ValueError:
            return
    if isinstance(node, dict):
        if any(key in node for key in _CLAIM_FIELDS):
            out.append(node)
        for value in node.values():
            _collect_rows(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_rows(item, out)


def parse_claims(payload: Any, period: str) -> list[StipendClaim]:
    """Baris klaim satu periode dari respons apa pun bentuknya. Kosong = belum diajukan.

    Baris yang DECLARE periode lain dibuang (konvensi yang sama dengan parser
    evaluasi: bulan lain tidak pernah dipinjam); baris tanpa deklarasi periode
    diterima karena endpoint sudah difilter `period_start` di query.
    """
    rows: list[dict[str, Any]] = []
    _collect_rows(maybe_b64_json(payload), rows)

    claims: list[StipendClaim] = []
    seen_ids: set[str] = set()
    for row in rows:
        declared = _period_of(row)
        if declared and declared != period:
            continue
        snap = row.get("calculation_snapshot") or row.get("calculation") or {}
        if not isinstance(snap, dict):
            snap = {}

        def _num(*names: str) -> int:
            for key in names:
                for source in (row, snap):
                    value = _as_int(source.get(key))
                    if value is not None:
                        return value
            return 0

        claim = StipendClaim(
            period=period,
            claim_id=str(row.get("id") or row.get("claim_id") or ""),
            submitted_at=str(row.get("submitted_at") or row.get("created_at") or ""),
            amount=_as_int(row.get("amount") or snap.get("amount")),
            total_working_days=_num("total_working_days"),
            payable_days=_num("payable_days"),
            present_days=_num("present_days"),
            paid_leave_days=_num("paid_leave_days"),
        )
        # Kalender working_day snapshot bisa jadi satu-satunya sumber jumlah hari.
        if not claim.has_days:
            calendar_days = snap.get("working_day_calendar")
            if isinstance(calendar_days, list) and calendar_days:
                claim.total_working_days = len(calendar_days)
        if not (claim.has_days or claim.amount or claim.submitted_at or claim.claim_id):
            continue
        if claim.claim_id and claim.claim_id in seen_ids:
            continue
        seen_ids.add(claim.claim_id)
        claims.append(claim)
    # Klaim terbaru paling depan kalau portal mengembalikan lebih dari satu.
    claims.sort(key=lambda c: c.submitted_at, reverse=True)
    return claims


def claim_from_attendances(period: str, rows: Iterable[dict[str, Any]]) -> StipendClaim:
    """Cadangan saat respons klaim tidak menyertakan jumlah hari: hitung dari absensi.

    PRESENT + ON_LEAVE dihitung dibayar (izin sakit berbayar tidak memotong),
    ABSENT jadi mangkir, DAYOFF/libur tidak masuk total hari kerja sama sekali.
    """
    start, end = period_window(period)
    present = leave = absent = 0
    for row in rows:
        tanggal = str(row.get("date") or "")
        if not start or not (start <= tanggal <= end):
            continue
        status = str(row.get("status") or "").upper()
        if status == "PRESENT":
            present += 1
        elif status == "ON_LEAVE":
            leave += 1
        elif status == "ABSENT":
            absent += 1
    return StipendClaim(
        period=period,
        total_working_days=present + leave + absent,
        payable_days=present + leave,
        present_days=present,
        paid_leave_days=leave,
    )


def estimate(claim: StipendClaim, ump: int = UMP_DKI, region: str = WILAYAH) -> StipendEstimate:
    """Angka final untuk pesan rincian. `amount` portal menang atas rumus UMP."""
    total = claim.total_working_days
    payable = min(claim.payable_days, total) if total else claim.payable_days
    absent = max(0, total - payable)
    if total:
        potongan = round(ump * absent / total)
        percent = int(payable * 100 // total)
    else:
        # Tidak ada data hari sama sekali (fallback attendances pun kosong):
        # jangan mengarang kekurangan — anggap utuh, `amount` tetap penentu.
        potongan, percent = 0, 100
    if claim.amount is not None and claim.amount >= 0:
        diterima = claim.amount
        potongan = max(0, ump - diterima) or potongan
    else:
        diterima = ump - potongan
    return StipendEstimate(
        period=claim.period,
        region=region,
        ump=ump,
        total_days=total,
        payable_days=payable,
        absent_days=absent,
        percent=percent,
        potongan=potongan,
        diterima=diterima,
    )
