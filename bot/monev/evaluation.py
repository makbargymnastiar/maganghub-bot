"""Pembaca hasil evaluasi bulanan mentor (laporan bulanan portal).

Nama endpoint dan kunci field sudah dikunci dari kode frontend monev (bundle SPA yang
memang dipublikasikan sebagai aset statis), jadi tidak ada lagi penebakan bentuk
payload:

- `/api/v1/monthly-reports` -> satu laporan per periode, berisi delapan aspek
  penilaian sikap (`attendance_discipline_grade` dst.), `mentor_feedback`, dan
  `curriculum_achievement_grade`.
- `/api/v1/participants/{id}/periods/{start_date}/curriculum` -> daftar materi
  kurikulum yang jadi latar capaian kurikulum.

Frontend sendiri membaca `laporan[key] ?? laporan[key.replace("_grade", "_score")]`,
jadi API boleh menjawab dengan `*_grade` maupun `*_score`; keduanya diterima di sini.

Aturan yang tidak berubah: jangan mengarang nilai. Angka yang dikirim portal adalah
PERINGKAT 1..4 (1 = terbaik), bukan jumlah bintang, jadi `3` berarti "Cukup" (3 bintang
karena kebetulan searah) dan `1` berarti Sangat Baik (5 bintang). Nilai yang tidak
dikenal menjadi `stars = 0` dan hanya teks aslinya yang ditampilkan di kartu.
"""

from __future__ import annotations

import calendar
from typing import Any

from ..models import (
    EvalItem,
    EvaluationResult,
    PREDIKAT_BY_GRADE,
    PREDIKAT_BY_STARS,
    STARS_BY_GRADE_ENUM,
    STARS_BY_GRADE_RANK,
    STARS_BY_GRADE_SHORT,
    STARS_BY_PREDIKAT,
)
from .b64 import maybe_b64_json

# Delapan aspek penilaian: kunci API + label resmi. Urutan tuple ini = urutan tampil
# di kartu hasil, jadi daftar ini yang jadi satu-satunya sumber urutan.
GRADE_FIELDS: tuple[tuple[str, str], ...] = (
    ("attendance_discipline_grade", "Kehadiran dan disiplin"),
    ("attitude_grade", "Sikap dan perilaku"),
    ("communication_grade", "Kemampuan komunikasi"),
    ("initiative_responsibility_grade", "Inisiatif dan tanggung jawab"),
    ("adaptability_grade", "Kemampuan adaptasi"),
    ("technical_knowledge_grade", "Pengetahuan teknis"),
    ("productivity_grade", "Produktivitas dan ketepatan waktu"),
    ("teamwork_grade", "Kerja sama tim"),
)
CURRICULUM_GRADE_FIELD = "curriculum_achievement_grade"
CURRICULUM_ASSESSMENTS_FIELD = "curriculum_assessments"
# Label satu materi kurikulum, dipakai untuk kedua sumber datanya.
_CURRICULUM_NAME_KEYS = ("name", "title", "topic", "material", "subject", "indikator")
_MATERIAL_SCORE_KEYS = ("grade", "score", "value", "predikat", "nilai")
_COMMENT_FIELDS = ("mentor_feedback", "mentor_comment", "catatan_mentor", "catatan", "comment", "feedback", "notes")
# Hanya field yang memang menyatakan bulan sebuah laporan. Tanggal submit sengaja
# TIDAK ikut dihitung: laporan November yang ditulis mentor tanggal 9 Desember tetap
# laporan November, bukan laporan Desember.
_PERIOD_FIELDS = ("period", "periode", "month", "bulan", "for_month", "reference_month",
                  "month_of", "month_year", "start_date")
_STATUS_FIELDS = ("status", "state")
_FILLED_FIELDS = ("is_filled", "filled", "is_submitted", "submitted", "is_done", "done",
                  "completed", "is_completed")
_REVIEWER_FIELDS = ("mentor_name", "reviewer_name", "mentor", "reviewer", "assessor_name", "created_by")
_UPDATED_FIELDS = ("updated_at", "submitted_at", "created_at", "date")
_FILLED_WORDS = ("filled", "submitted", "approved", "done", "completed", "verified", "acc",
                 "selesai", "terisi", "terkirim")
_EMPTY_WORDS = ("belum", "draft", "pending", "empty", "open", "process", "in_progress", "kosong")
# Pembungkus respons yang mungkin dipakai portal.
_ENVELOPE_KEYS = ("data", "result", "results", "report", "records", "items")
# Kunci pembungkus tempat daftar materi kurikulum berada.
_CURRICULUM_KEYS = ("curriculum", "kurikulum")


def period_window(period: str) -> tuple[str, str]:
    """'2026-11' -> ('2026-11-01', '2026-11-30'): jendela query laporan bulanan."""
    try:
        year, month = int(period[:4]), int(period[5:7])
    except (IndexError, TypeError, ValueError):
        return "", ""
    if not 1 <= month <= 12:
        return "", ""
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def grade_rank_of(value: Any) -> int:
    """Jumlah bintang (5..2) dari nilai portal apa pun; 0 kalau tidak dikenali."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        # Peringkat 1..4, BUKAN jumlah bintang: 1 = Sangat Baik.
        return STARS_BY_GRADE_RANK.get(int(value), 0)

    text = str(value).strip()
    if not text:
        return 0
    upper = text.upper()
    if upper in STARS_BY_GRADE_ENUM:
        return STARS_BY_GRADE_ENUM[upper]
    if upper in STARS_BY_GRADE_SHORT:
        return STARS_BY_GRADE_SHORT[upper]
    lowered = text.lower().replace("_", " ").strip()
    if lowered in STARS_BY_PREDIKAT:
        return STARS_BY_PREDIKAT[lowered]
    if text.isdigit() and len(text) == 1:
        return STARS_BY_GRADE_RANK.get(int(text), 0)
    # Teks panjang ("Sangat Baik (SB)"): predikat terpanjang dicek lebih dulu,
    # kalau tidak "baik" menang di dalam "sangat baik".
    for word, stars in sorted(STARS_BY_PREDIKAT.items(), key=lambda kv: -len(kv[0])):
        if word in lowered:
            return stars
    return 0


def predikat_of(value: Any) -> str:
    """Label resmi untuk nilai portal; teks asli kalau tidak terbaca sama sekali."""
    text = str(value if value is not None else "").strip()
    if not text or isinstance(value, bool):
        return ""
    upper = text.upper()
    if upper in PREDIKAT_BY_GRADE:
        return PREDIKAT_BY_GRADE[upper]
    stars = grade_rank_of(value)
    return PREDIKAT_BY_STARS[stars] if stars else text


def _norm(key: Any) -> str:
    return str(key or "").lower().replace("_", "").replace("-", "").strip()


def _lookup(row: dict[str, Any]) -> dict[str, Any]:
    """Dict dengan kunci ternormalisasi, jadi `start_date` = `startDate` = `start-date`."""
    return {_norm(key): value for key, value in row.items()}


def _pick(lookup: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for want in keys:
        value = lookup.get(_norm(want))
        if value not in (None, "", [], {}):
            return value
    return None


def _grade_value(lookup: dict[str, Any], field: str) -> Any:
    """Nilai satu aspek. Portal mengirim `*_grade` ATAU `*_score` — dua-duanya sah."""
    return _pick(lookup, (field, field.replace("_grade", "_score")))


def _unwrap(payload: Any) -> Any:
    """Bongkar pembungkus portal: base64 -> JSON -> `data`/`results`."""
    data = maybe_b64_json(payload)
    if isinstance(data, dict):
        for key in _ENVELOPE_KEYS:
            inner = data.get(key)
            if isinstance(inner, (dict, list)):
                return inner
    return data


def _rows(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    if isinstance(node, dict):
        return [node]
    return []


def _report_period(lookup: dict[str, Any]) -> str:
    """Bulan yang dinyatakan sebuah laporan ('YYYY-MM'), atau "" kalau tidak ada."""
    text = str(_pick(lookup, _PERIOD_FIELDS) or "")
    if len(text) >= 7 and text[4] == "-":
        return text[:7]
    return ""


def _has_grade(lookup: dict[str, Any]) -> bool:
    """Laporan yang isinya apa pun: ada nilai aspek, komentar, atau capaian kurikulum.

    Form kosong punya kedelapan kunci dengan nilai null (default frontend), jadi
    keberadaan kunci saja bukan tanda sudah diisi.
    """
    if any(_grade_value(lookup, field) is not None for field, _ in GRADE_FIELDS):
        return True
    return bool(_pick(lookup, _COMMENT_FIELDS)) or _grade_value(lookup, CURRICULUM_GRADE_FIELD) is not None


def _report_rows(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Semua dict yang merupakan laporan, termasuk kalau portal membungkusnya satu level.

    Bentuk utamanya sudah diketahui: laporan di dalam `data`. Tapi varian
    `participants-period` bisa menempatkan laporan di bawah objek induk, jadi
    pencariannya dibatasi kedalaman (maks 2 level). Baris `curriculum_assessments`
    tidak ikut dianggap laporan — dia tidak punya salah satu kunci di atas.
    """
    if depth > 2:
        return []
    if isinstance(node, list):
        found: list[dict[str, Any]] = []
        for item in node:
            found.extend(_report_rows(item, depth + 1))
        return found
    if not isinstance(node, dict):
        return []
    lookup = _lookup(node)
    if _has_grade(lookup):
        return [node]
    nested: list[dict[str, Any]] = []
    for value in node.values():
        if isinstance(value, (dict, list)):
            nested.extend(_report_rows(value, depth + 1))
    return nested


def _select_report(payload: Any, period: str) -> dict[str, Any]:
    """Satu dict laporan untuk periode ini; {} kalau portal belum punya untuk bulan itu.

    `_report_rows` sudah menyaring laporan bernilai, jadi yang tersisa hanya soal
    memilih bulan dan menolak meminjam laporan bulan lain.
    """
    candidates = [_lookup(row) for row in _report_rows(_unwrap(payload))]
    if not candidates:
        return {}
    dated = [lookup for lookup in candidates if _report_period(lookup) == period] if period else []
    if dated:
        return dated[-1]
    # Tidak ada yang cocok dengan bulan yang diminta. Kalau laporan yang ada jelas
    # menyebut bulan lain, jangan dipinjam: mengirim "✅ Evaluasi Diisi Mentor" untuk
    # bulan yang salah lebih buruk daripada diam.
    declared = [lookup for lookup in candidates if _report_period(lookup)]
    if period and declared:
        return {}
    return candidates[-1]


def _filled_of(lookup: dict[str, Any]) -> bool | None:
    status = _pick(lookup, _STATUS_FIELDS)
    if isinstance(status, bool):
        return status
    if isinstance(status, str):
        text = status.lower().replace("_", " ")
        if any(word in text for word in _EMPTY_WORDS):
            return False
        if any(word in text for word in _FILLED_WORDS):
            return True
    flag = _pick(lookup, _FILLED_FIELDS)
    return flag if isinstance(flag, bool) else None


def _item(label: str, value: Any) -> EvalItem:
    """Satu baris kartu: bintang kalau nilainya terbaca, kalau tidak teks aslinya."""
    stars = grade_rank_of(value)
    return EvalItem(name=label, stars=stars, raw="" if stars else predikat_of(value))


def _grade_items(lookup: dict[str, Any]) -> list[EvalItem]:
    items: list[EvalItem] = []
    for field, label in GRADE_FIELDS:
        value = _grade_value(lookup, field)
        if value is None:
            continue
        items.append(_item(label, value))
    return items


def _assessed_items(raw: Any) -> list[EvalItem]:
    """`curriculum_assessments`: satu baris per materi dengan nilainya sendiri."""
    items: list[EvalItem] = []
    for row in _rows(_unwrap(raw)):
        lookup = _lookup(row)
        name = str(_pick(lookup, _CURRICULUM_NAME_KEYS) or "").strip()
        value = _pick(lookup, _MATERIAL_SCORE_KEYS)
        if value is None:
            continue
        items.append(_item(name or "Capaian kurikulum", value))
    return items


def _material_names(payload: Any) -> list[str]:
    """Nama materi dari /participants/{id}/periods/{start}/curriculum."""
    node = _unwrap(payload)
    materials: Any = None
    if isinstance(node, dict):
        # /participants/{id}/periods/{start}/curriculum menjawab objek konteks periode,
        # dan daftar materinya menempel di dalam `curriculum`.
        for key in _CURRICULUM_KEYS:
            inner = node.get(key)
            if isinstance(inner, dict):
                node = inner
                break
        materials = node.get("materials") or node.get("materi") or node.get("topics")
    elif isinstance(node, list):
        materials = node
    names: list[str] = []
    for row in _rows(materials):
        name = str(_pick(_lookup(row), _CURRICULUM_NAME_KEYS) or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _curriculum_items(lookup: dict[str, Any], curriculum_payload: Any) -> list[EvalItem]:
    """Capaian kurikulum, dari dua sumber yang mungkin:

    1. `curriculum_assessments` di dalam laporan -> nilai per materi, itu yang dipakai.
    2. Tidak ada assessment -> satu baris agregat dari `curriculum_achievement_grade`,
       ditambah daftar materi tanpa bintang: lebih jujur menampilkan materi tanpa
       nilai daripada mengarang nilai per materi.
    """
    assessments = None
    for key in (CURRICULUM_ASSESSMENTS_FIELD, "curriculumAssessments"):
        if isinstance(lookup.get(_norm(key)), list):
            assessments = lookup[_norm(key)]
            break
    items = _assessed_items(assessments) if assessments is not None else []
    if items:
        return items

    aggregate = _grade_value(lookup, CURRICULUM_GRADE_FIELD)
    names = _material_names(curriculum_payload)
    if aggregate is not None:
        items.append(_item("Capaian kurikulum", aggregate))
    for name in names:
        items.append(EvalItem(name=name, stars=0, raw=""))
    return items


def parse_evaluation(payload: Any, period: str, curriculum_payload: Any = None) -> EvaluationResult:
    """Hasil evaluasi satu periode; kosong kalau portal tidak punya laporan bulan ini."""
    lookup = _select_report(payload, period)
    if not lookup:
        return EvaluationResult(period=period)

    comment = str(_pick(lookup, _COMMENT_FIELDS) or "").strip()
    reviewer = str(_pick(lookup, _REVIEWER_FIELDS) or "").strip()
    updated = str(_pick(lookup, _UPDATED_FIELDS) or "").strip()
    return EvaluationResult(
        period=period,
        items=_grade_items(lookup),
        curriculum=_curriculum_items(lookup, curriculum_payload),
        comment=comment,
        reviewer=reviewer,
        updated_at=updated,
        filled=_filled_of(lookup),
    )


def periods_to_watch(today_iso: str, months_back: int = 2) -> list[str]:
    """Periode yang worth dicek: bulan berjalan + beberapa bulan sebelumnya.

    Evaluasi bulan M biasanya baru diisi mentor pada tanggal 9 bulan M+1, jadi
    bulan lalu wajib ikut dipantau — kalau hanya bulan berjalan, notifikasinya
    tidak pernah muncul.
    """
    year, month = int(today_iso[:4]), int(today_iso[5:7])
    out: list[str] = []
    for _ in range(max(1, months_back)):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return out
