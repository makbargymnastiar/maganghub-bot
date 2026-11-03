"""Konfigurasi runtime: baca `config.py` (rahasia, gitignored) + default.

Semua akses kredensial terpusat di sini supaya modul lain tidak perlu
mengimpor `config` langsung, dan token selalu lewat `mask_token()` saat
akan ditampilkan/dilog.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time as dtime
from zoneinfo import ZoneInfo

DEFAULT_BASE_URL = "https://monev-api.maganghub.kemnaker.go.id"
DEFAULT_WEB_URL = "https://monev.maganghub.kemnaker.go.id"
DAILY_LOGS_PATH = "/api/v1/daily-logs"


def _load_raw_config() -> dict[str, object]:
    """Import `config.py` bila ada; kalau tidak ada, kembalikan dict kosong."""
    try:
        import config  # type: ignore
    except ImportError:
        return {}
    return {k: v for k, v in vars(config).items() if k.isupper()}


def _derive_base_url(api_url: str) -> str:
    """config.MONEV_API_URL bisa berupa URL endpoint lengkap; ambil origin+/api/v1."""
    api_url = (api_url or "").strip()
    if not api_url:
        return DEFAULT_BASE_URL
    marker = "/api/v1"
    idx = api_url.find(marker)
    if idx != -1:
        return api_url[:idx]
    # fallback: scheme + host saja
    scheme, _, rest = api_url.partition("://")
    if not rest:
        return DEFAULT_BASE_URL
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}"


def _normalize_bearer(value: str) -> str:
    """Terima 'Bearer xxx' maupun 'xxx', kembalikan tanpa prefiks."""
    value = (value or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value


def normalize_cookie(value: str) -> str:
    """Ambil isi cookie; buang atribut di belakangnya kalau ikut tersalin."""
    value = (value or "").strip()
    if not value:
        return ""
    if "=" in value:
        name, _, rest = value.partition("=")
        return f"{name.strip()}={rest.split(';')[0].strip()}"
    return f"monev_refresh_token={value.split(';')[0].strip()}"


def _parse_hhmm(value: object, default: dtime) -> dtime:
    if isinstance(value, str):
        try:
            hh, mm = value.strip().split(":")
            return dtime(int(hh), int(mm))
        except ValueError:
            pass
    return default


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    base_url: str
    participant_id: str
    bearer_token: str
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Jakarta"))
    tz_name: str = "Asia/Jakarta"
    poll_minutes: int = 15
    reminder_evening: dtime = dtime(18, 0)
    reminder_followup: dtime = dtime(21, 0)
    reminder_emergency: dtime = dtime(23, 0)
    db_path: str = "data/bot.db"
    log_level: str = "INFO"
    # Origin SPA (bukan API): dipakai mengambil `version.json` untuk header
    # X-Frontend-Build-ID saat memanggil /auth/refresh.
    web_url: str = DEFAULT_WEB_URL
    # Seed cookie `monev_refresh_token`; setelah pernah dikirim lewat /cookie,
    # yang dipakai adalah nilai di DB (sama seperti BEARER_TOKEN).
    refresh_cookie: str = ""
    # Selang waktu bot mencoba refresh sebelum token akses benar-benar mati.
    token_refresh_lead_minutes: int = 90

    def now(self):
        from datetime import datetime

        return datetime.now(self.tz)


def load_settings(raw: dict[str, object] | None = None) -> Settings:
    cfg = raw if raw is not None else _load_raw_config()

    def get(key: str, default=None):
        value = cfg.get(key)
        if isinstance(value, str):
            value = value.strip()
        return value if value not in (None, "") else default

    api_url = get("MONEV_API_URL", DEFAULT_BASE_URL + DAILY_LOGS_PATH)
    tz_name = get("TZ_NAME", "Asia/Jakarta")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # zona waktu tidak tersedia di sistem
        tz = ZoneInfo("UTC")

    return Settings(
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=str(get("TELEGRAM_CHAT_ID", "")),
        base_url=_derive_base_url(api_url),
        participant_id=get("PARTICIPANT_ID", ""),
        bearer_token=_normalize_bearer(get("BEARER_TOKEN", "")),
        tz=tz,
        tz_name=tz_name,
        poll_minutes=max(5, int(get("POLL_MINUTES", 15))),
        reminder_evening=_parse_hhmm(get("REMINDER_EVENING"), dtime(18, 0)),
        reminder_followup=_parse_hhmm(get("REMINDER_FOLLOWUP"), dtime(21, 0)),
        reminder_emergency=_parse_hhmm(get("REMINDER_EMERGENCY"), dtime(23, 0)),
        db_path=get("DB_PATH", os.path.join("data", "bot.db")),
        log_level=get("LOG_LEVEL", "INFO"),
        web_url=(get("MONEV_WEB_URL", DEFAULT_WEB_URL) or "").rstrip("/"),
        refresh_cookie=normalize_cookie(get("MONEV_REFRESH_TOKEN", "")),
        token_refresh_lead_minutes=max(5, int(get("TOKEN_REFRESH_LEAD_MINUTES", 90))),
    )


def mask_token(token: str) -> str:
    """eyJ0eXAi…<6 karakter terakhir> — aman untuk log dan balasan chat."""
    token = (token or "").strip()
    if not token:
        return "(kosong)"
    if len(token) <= 20:
        return token[:4] + "…"
    return f"{token[:10]}…{token[-6:]}"
