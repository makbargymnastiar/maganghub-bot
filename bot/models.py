"""Model data murni (tanpa dependensi DB/HTTP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Status registrasi pengguna (kolom `users.status`).
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"


@dataclass
class User:
    tg_chat_id: str
    participant_id: str
    name: str = ""
    bearer_token: str = ""
    token_fingerprint: str = ""
    token_saved_at: datetime | None = None
    token_ttl_seconds: int = 21600
    token_invalid: bool = False
    active: bool = True
    reminders_on: bool = True
    # Cookie httpOnly `monev_refresh_token`: kunci untuk mendapatkan access_token
    # baru tanpa bertanya lagi ke user. Bentuk "nama=nilai" atau nilai saja.
    refresh_cookie: str = ""
    refresh_saved_at: datetime | None = None
    # Status registrasi multi-user: 'pending' (belum diverifikasi) | 'active'.
    status: str = STATUS_PENDING

    @property
    def can_auto_refresh(self) -> bool:
        return bool(self.refresh_cookie)

    @property
    def is_active_user(self) -> bool:
        """Boleh dipoll/direminder: sudah terverifikasi DAN tidak dinonaktifkan."""
        return self.status == STATUS_ACTIVE and self.active


# Tiga kolom yang diisi peserta di portal monev. Terkunci dari data nyata (HAR):
# daily-log punya keys activity_log, lesson_learned, obstacles, state, reviewer_notes.
LOG_FIELDS: tuple[tuple[str, str], ...] = (
    ("activity_log", "aktivitas"),
    ("lesson_learned", "pembelajaran"),
    ("obstacles", "kendala"),
)


@dataclass
class DayRecord:
    """Kondisi satu tanggal, gabungan `home` + `attendances` + `daily-logs`."""

    date: str
    attendance: dict[str, Any] | None = None
    log: dict[str, Any] | None = None
    is_holiday: bool = False
    is_off_day: bool = False

    @property
    def attendance_status(self) -> str | None:
        return (self.attendance or {}).get("status")

    @property
    def approval_status(self) -> str | None:
        return (self.attendance or {}).get("approval_status")

    @property
    def log_state(self) -> str | None:
        return (self.log or {}).get("state")

    @property
    def has_attendance(self) -> bool:
        return self.attendance is not None

    @property
    def has_log(self) -> bool:
        return self.log is not None

    @property
    def filled_count(self) -> int:
        """Berapa dari tiga isian yang sudah ada teksnya (spasi saja dianggap kosong)."""
        if not self.log:
            return 0
        return sum(1 for key, _ in LOG_FIELDS if str(self.log.get(key) or "").strip())

    @property
    def missing_fields(self) -> list[str]:
        """Nama isian yang masih kosong, dalam urutan portal: aktivitas, pembelajaran, kendala."""
        if not self.log:
            return [label for _, label in LOG_FIELDS]
        return [label for key, label in LOG_FIELDS if not str(self.log.get(key) or "").strip()]

    @property
    def content_complete(self) -> bool:
        return self.log is not None and self.filled_count == len(LOG_FIELDS)

    @property
    def reviewer_notes(self) -> str:
        """Catatan mentor; ikut terkirim saat status minta revisi."""
        return str((self.log or {}).get("reviewer_notes") or "").strip()


# --- evaluasi bulanan mentor -------------------------------------------------

# Delapan aspek penilaian + capaian kurikulum dinilai portal dengan enum, bukan
# angka bintang. Nama enum & singkatannya dikunci dari kode frontend monev:
# {title:`Sangat Baik`, short:`SB`, value:`EXCELLENT`} ... {`Kurang`, `K`, `POOR`}.
PREDIKAT_BY_GRADE: dict[str, str] = {
    "EXCELLENT": "Sangat Baik (SB)",
    "GOOD": "Baik (B)",
    "SUFFICIENT": "Cukup (C)",
    "POOR": "Kurang (K)",
}
STARS_BY_GRADE_ENUM: dict[str, int] = {"EXCELLENT": 5, "GOOD": 4, "SUFFICIENT": 3, "POOR": 2}
# Singkatan yang tampil di layar portal. Hanya diterima kalau seluruh teks memang
# persis singkatan itu, supaya kata lain tidak salah tafsir.
STARS_BY_GRADE_SHORT: dict[str, int] = {"SB": 5, "B": 4, "C": 3, "K": 2}
# ANGKA DI PORTAL ADALAH PERINGKAT, BUKAN JUMLAH BINTANG: frontend menormalkan
# 1 -> EXCELLENT ... 4 -> POOR. Membaca angka ini sebagai bintang akan membalik
# seluruh nilai, jadi pemetaannya ditulis di sini supaya sengaja.
STARS_BY_GRADE_RANK: dict[int, int] = {1: 5, 2: 4, 3: 3, 4: 2}

STARS_BY_PREDIKAT: dict[str, int] = {"sangat baik": 5, "baik": 4, "cukup": 3, "kurang": 2}
# Ditulis eksplisit, bukan turunan dari dict di atas: inisial predikatnya tidak bisa
# dibentuk dari huruf depan kata pertama saja ("sangat baik" harus jadi SB, bukan SA).
PREDIKAT_BY_STARS: dict[int, str] = {
    5: "Sangat Baik (SB)",
    4: "Baik (B)",
    3: "Cukup (C)",
    2: "Kurang (K)",
}


@dataclass
class EvalItem:
    """Satu baris penilaian — satu dari 8 poin sikap, atau satu capaian kurikulum."""

    name: str
    stars: int = 0  # 0 = nilainya tidak terbaca dari payload
    raw: str = ""   # teks predikat asli portal, ditampilkan kalau bintang tidak jelas

    @property
    def predikat(self) -> str:
        return PREDIKAT_BY_STARS.get(self.stars, self.raw)


@dataclass
class EvaluationResult:
    """Evaluasi bulanan satu periode (`period` = 'YYYY-MM'), hasil sapuan payload."""

    period: str
    items: list[EvalItem] = field(default_factory=list)
    curriculum: list[EvalItem] = field(default_factory=list)
    comment: str = ""
    reviewer: str = ""
    updated_at: str = ""
    # None = portal tidak menyebut status; True/False dari field status/score.
    filled: bool | None = None

    @property
    def scored(self) -> list[EvalItem]:
        return [item for item in (*self.items, *self.curriculum) if item.stars]

    @property
    def has_content(self) -> bool:
        """Ada isinya: minimal satu nilai terbaca atau ada komentar mentor."""
        return bool(self.scored) or bool(self.comment)

    @property
    def is_filled(self) -> bool:
        if self.filled is None:
            return self.has_content
        return self.filled and self.has_content

    @property
    def average(self) -> float:
        scores = [item.stars for item in self.scored]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def all_top(self) -> bool:
        scores = [item.stars for item in self.scored]
        return bool(scores) and all(s >= 5 for s in scores)

    @property
    def needs_improvement(self) -> list[EvalItem]:
        """Poin dengan bintang ≤3 — bahan kalimat motivasi yang tidak menghakimi."""
        return [item for item in self.scored if item.stars <= 3]

    @property
    def strengths(self) -> list[EvalItem]:
        return [item for item in self.scored if item.stars >= 5]


@dataclass
class Event:
    log_date: str
    from_label: str
    to_label: str
    kind: str
    message: str
    created_at: datetime
    id: int | None = None


@dataclass
class ReminderPlan:
    """Keputusan reminder yang bisa diuji tanpa menyentuh jam dinding."""

    actions: list[tuple[str, str]] = field(default_factory=list)
