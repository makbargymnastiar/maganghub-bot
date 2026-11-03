"""Model data murni (tanpa dependensi DB/HTTP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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

    @property
    def can_auto_refresh(self) -> bool:
        return bool(self.refresh_cookie)


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
