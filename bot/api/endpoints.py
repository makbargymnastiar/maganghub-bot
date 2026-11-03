"""Endpoint monev: bentuk query + pemetaan response, sesuai yang dipakai frontend.

Semua bentuk response dikunci dari HAR:
- daily-logs -> `{data: [...], page, page_size, total}` (query `date=<YYYY-MM-01>&limit=100`)
- attendances -> array langsung di root
- home / absence-warnings -> `{data: {...}}`
- announcements -> teks base64 dari JSON (lihat `b64.py`)
"""

from __future__ import annotations

from typing import Any

from .client import MonevClient, MonevError

HOME_PATH = "/api/v1/users/me/home"
ATTENDANCES_PATH = "/api/v1/attendances"
DAILY_LOGS_PATH = "/api/v1/daily-logs"
ABSENCE_WARNINGS_PATH = "/api/v1/participant-absence-warnings/me"
ANNOUNCEMENTS_PATH = "/api/v1/users/announcements"
SYSTEM_TIME_PATH = "/api/v1/system/time"
USER_PROFILE_PATH = "/api/v1/users/me"
REFRESH_PATH = "/api/v1/auth/refresh"


def parse_token_response(payload: Any) -> tuple[str, str]:
    """Ambil (access_token, refresh_cookie_rotasi) dari respons login/refresh."""
    data = _as_dict(payload)
    token = str(data.get("access_token") or data.get("token") or "")
    rotated = str(data.get("refresh_token") or data.get("refresh_cookie") or "")
    return token, rotated


async def fetch_frontend_build_id(client: MonevClient, web_url: str) -> str:
    """ID build SPA, dikirim frontend sebagai `X-Frontend-Build-ID`; gagal = string kosong."""
    try:
        data = await client.external_json(f"{web_url.rstrip('/')}/version.json")
    except MonevError:
        return ""
    return str(data.get("build_id", "")) if isinstance(data, dict) else ""


async def refresh_access_token(client: MonevClient, *, build_id: str = "") -> tuple[str, str]:
    """Perpanjang sesi lewat cookie refresh: `POST /api/v1/auth/refresh`.

    Dibuktikan dari bundle SPA (`_nuxt/Bdl9iSbj.js`) dan tes live: endpoint ini
    TIDAK membaca Bearer token, tapi cookie httpOnly `monev_refresh_token`; tanpa
    cookie itu server balas 401 + `Set-Cookie: monev_refresh_token=; Max-Age=0`.
    Return (access_token baru, cookie hasil rotasi bila ada).
    """
    if not client.refresh_cookie:
        raise MonevError("belum ada cookie refresh — kirim /cookie lebih dulu")
    headers = {"X-Frontend-Build-ID": build_id} if build_id else None
    payload = await client.post(REFRESH_PATH, {}, headers=headers)
    token, rotated = parse_token_response(payload)
    if not token:
        raise MonevError("respons /auth/refresh tidak berisi access_token")
    return token, rotated or client.rotated_cookie


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """Terima array di root maupun di dalam `data`; selain itu kembalikan []."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                for inner in ("content", "items", "results"):
                    nested = value.get(inner)
                    if isinstance(nested, list):
                        return [i for i in nested if isinstance(i, dict)]
    return []


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        inner = payload.get("data")
        if isinstance(inner, dict):
            return inner
        return payload
    return {}


async def fetch_home(client: MonevClient) -> dict[str, Any]:
    """`{date, active_internship, is_holiday, is_scheduled_off_day, has_attendance}`."""
    return _as_dict(await client.get(HOME_PATH))


async def fetch_attendances(client: MonevClient, participant_id: str) -> dict[str, dict[str, Any]]:
    """Indeks attendance per tanggal (API mengembalikan array)."""
    rows = _as_list(await client.get(ATTENDANCES_PATH, {"participant_id": participant_id}))
    return {row["date"]: row for row in rows if row.get("date")}


async def fetch_daily_logs(
    client: MonevClient, participant_id: str, month_first_day: str
) -> dict[str, dict[str, Any]]:
    """Indeks daily-log per tanggal untuk bulan `month_first_day` (YYYY-MM-01)."""
    rows = _as_list(
        await client.get(DAILY_LOGS_PATH, {"participant_id": participant_id, "date": month_first_day, "limit": 100})
    )
    return {row["date"]: row for row in rows if row.get("date")}


async def fetch_absence_warnings(client: MonevClient) -> list[dict[str, Any]]:
    return _as_list(await client.get(ABSENCE_WARNINGS_PATH))


async def fetch_user_profile(client: MonevClient) -> dict[str, Any]:
    return _as_dict(await client.get(USER_PROFILE_PATH))


async def fetch_system_time(client: MonevClient) -> dict[str, Any]:
    return _as_dict(await client.get(SYSTEM_TIME_PATH))


async def fetch_announcements(client: MonevClient) -> list[dict[str, Any]]:
    from .b64 import maybe_b64_json

    raw = await client.get(ANNOUNCEMENTS_PATH)
    decoded = maybe_b64_json(raw)
    return _as_list(decoded)
