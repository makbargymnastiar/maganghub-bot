"""Endpoint monev: bentuk query + pemetaan response, sesuai yang dipakai frontend.

Semua bentuk response dikunci dari data nyata (HAR atau hasil sapuan bundle SPA —
lihat `scripts/discover_endpoints.py`):
- daily-logs -> `{data: [...], page, page_size, total}` (query `date=<YYYY-MM-01>&limit=100`)
- attendances -> array langsung di root
- home / absence-warnings -> `{data: {...}}`
- announcements -> teks base64 dari JSON (lihat `b64.py`)
- monthly-reports -> daftar laporan bulanan (query `participant_id&start_date&end_date`)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .client import MonevClient, MonevError
from . import evaluation
from ..models import EvaluationResult

log = logging.getLogger("bot.monev.endpoints")

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


# Evaluasi bulanan = "laporan bulanan" di portal. Nama path, query, dan field-nya
# tidak ditebak: semuanya hasil sapuan bundle SPA monev (lihat
# `scripts/discover_endpoints.py`), tempat frontend memanggil
# `e(`/monthly-reports`, {participant_id, start_date, end_date})`.
MONTHLY_REPORTS_PATH = "/api/v1/monthly-reports"
MONTHLY_REPORTS_BY_PERIOD_PATH = "/api/v1/monthly-reports/participants-period"
MONTHLY_REPORTS_LATEST_PATH = "/api/v1/monthly-reports/latest"
PERIOD_CURRICULUM_PATH_FMT = "/api/v1/participants/{participant_id}/periods/{start_date}/curriculum"
# Batas halaman: laporan bulanan cuma 1 per bulan, jendela query sebulan penuh.
REPORTS_LIMIT = 100


async def _fetch_reports(client: MonevClient, participant_id: str, start: str, end: str) -> Any:
    """Daftar laporan bulanan peserta pada satu jendela tanggal.

    Path utama dipakai frontend untuk daftar per peserta; `participants-period`
    dipakai halaman yang mengelompokkan laporan per periode. Yang kedua cuma dicoba
    kalau yang pertama benar-benar menolak (404/403), bukan kalau jawabannya kosong.
    """
    params = {"participant_id": participant_id, "limit": REPORTS_LIMIT}
    if start:
        params["start_date"], params["end_date"] = start, end
    try:
        return await client.get(MONTHLY_REPORTS_PATH, params)
    except MonevError as exc:
        log.debug("%s gagal (%s); coba %s", MONTHLY_REPORTS_PATH, exc, MONTHLY_REPORTS_BY_PERIOD_PATH)
    return await client.get(MONTHLY_REPORTS_BY_PERIOD_PATH, params)


async def fetch_period_curriculum(client: MonevClient, participant_id: str, start_date: str) -> Any:
    """Materi kurikulum satu periode; None kalau endpoint-nya tidak menjawab.

    Ini pelengkap saja — daftar materi tidak menghambat kartu hasil, jadi gagal
    diambil biasa saja daripada memaksa satu siklus evaluasi jadi error.
    """
    if not participant_id or not start_date:
        return None
    path = PERIOD_CURRICULUM_PATH_FMT.format(participant_id=participant_id, start_date=start_date)
    try:
        return await client.get(path)
    except MonevError as exc:
        log.debug("kurikulum periode %s gagal: %s", start_date, exc)
        return None


async def fetch_evaluation(client: MonevClient, participant_id: str, period: str) -> EvaluationResult:
    """Hasil evaluasi satu periode ('YYYY-MM'); kosong kalau mentor belum mengisi.

    `MonevError` dibiarkan naik ke pemanggil (token mati / server bermasalah), tapi
    laporan yang memang belum ada bukan error: `EvaluationResult` tanpa isi.
    """
    start, end = evaluation.period_window(period)
    payload = await _fetch_reports(client, participant_id, start, end)
    curriculum = await fetch_period_curriculum(client, participant_id, start)
    return evaluation.parse_evaluation(payload, period, curriculum)


# Nama kunci yang isinya ID peserta. Sengaja hanya yang mengandung "participant":
# klaim `id` milik profil bisa jadi ID user Kemnaker, bukan ID peserta.
_PARTICIPANT_KEYS = {"participantid", "participant", "idparticipant"}


def walk_participant_ids(node: Any) -> list[str]:
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if isinstance(key, str) and key.lower().replace("_", "") in _PARTICIPANT_KEYS:
                if isinstance(value, (str, int)) and str(value).strip():
                    found.append(str(value).strip())
            found.extend(walk_participant_ids(value))
        return found
    if isinstance(node, list):
        nested: list[str] = []
        for item in node:
            nested.extend(walk_participant_ids(item))
        return nested
    if isinstance(node, str) and node.strip().startswith(("{", "[")):
        try:
            return walk_participant_ids(json.loads(node))
        except ValueError:
            return []
    return []


async def discover_participant_id(client: MonevClient) -> str:
    """Cari ID peserta milik pemanggil token — supaya user tidak perlu mengetiknya.

    Bentuk `/users/me` tidak dijamin, jadi endpoint milik sendiri ("me") dicoba
    berurutan dan semua simpul JSON-nya disapu; yang menolak/gagal dilewati saja.
    Endpoint daftar (mis. `/attendances`) sengaja tidak dipakai — di sana bisa ada
    participant_id orang lain dan ID yang salah jauh lebih berbahaya daripada kosong.
    Return string kosong bila memang tidak ketemu (user diminta kirim /daftar).
    """
    for path in (USER_PROFILE_PATH, HOME_PATH, ABSENCE_WARNINGS_PATH):
        try:
            payload = await client.get(path)
        except MonevError:
            continue
        candidates = walk_participant_ids(payload)
        if candidates:
            return candidates[0]
    return ""
