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
