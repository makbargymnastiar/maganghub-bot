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
from functools import partial
from typing import Any, Callable, Coroutine

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


async def _fetch_reports(client: MonevClient, participant_id: str, *, start: str = "", end: str = "") -> Any:
    """Daftar laporan bulanan peserta, dengan atau tanpa jendela tanggal.

    Path utama dipakai frontend untuk daftar per peserta; `participants-period` dipakai
    halaman yang mengelompokkan laporan per periode. Yang kedua cuma dicoba kalau yang
    pertama benar-benar menolak (404/403), bukan kalau jawabannya kosong.
    """
    params = {"participant_id": participant_id, "limit": REPORTS_LIMIT}
    if start:
        params["start_date"], params["end_date"] = start, end
    try:
        return await client.get(MONTHLY_REPORTS_PATH, params)
    except MonevError as exc:
        log.debug("%s gagal (%s); coba %s", MONTHLY_REPORTS_PATH, exc, MONTHLY_REPORTS_BY_PERIOD_PATH)
    return await client.get(MONTHLY_REPORTS_BY_PERIOD_PATH, params)


def _shape(node: Any) -> str:
    """Ringkasan bentuk respons: kunci dan jumlah saja, tanpa nilai.

    Cuma dipakai untuk log diagnostik. Nilai laporan tidak pernah ditulis ke log —
    di situ ada nama mentor dan catatan yang seharusnya hanya dibaca peserta.
    """
    if isinstance(node, list):
        return f"list[{len(node)}]"
    if isinstance(node, dict):
        dalam = ",".join(
            f"{key}={_shape(value)}"
            for key, value in list(node.items())[:12]
            if isinstance(value, (dict, list))
        )
        return f"dict[{dalam or ' '.join(str(key) for key in list(node)[:12])}]"
    return type(node).__name__


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
    """Hasil evaluasi satu periode ('YYYY-MM'); kosong kalau portal tidak punya laporan bulan itu.

    Pertanyaannya satu, tapi bentuk permintaannya belum diuji ke response asli: laporan
    bisa saja tercatat di tanggal luar bulan kalender periodenya (mentor mengisi
    November pada 9 Desember), dan `start_date`/`end_date` belum pasti dipakai portal
    sebagai penyaring periode. Jadi diusahakan lewat sumber yang semuanya ada di bundle
    frontend: jendela tanggal periode dulu, lalu seluruh laporan peserta. Endpoint
    laporan terbaru cuma cadangan kalau dua-duanya tidak menjawab sama sekali.
    Semuanya dibaca parser yang sama — tidak ada nilai yang dikarang, yang berubah
    hanya peluang laporan mentor benar-benar ketemu.

    `MonevError` dibiarkan naik ke pemanggil HANYA kalau semua sumber menolak (token
    mati / server bermasalah). Laporan yang memang belum ada bukan error: `EvaluationResult`
    tanpa isi.
    """
    start, end = evaluation.period_window(period)
    attempts: list[tuple[str, Callable[[], Coroutine[Any, Any, Any]]]] = []
    if start:
        # Yang paling sempit lebih dulu: kalau portal memang menyaring dengan jendela
        # tanggal, permintaan pertama ini saja sudah cukup.
        attempts.append(
            (f"jendela {start}..{end}", partial(_fetch_reports, client, participant_id, start=start, end=end))
        )
    attempts.append(("semua laporan", partial(_fetch_reports, client, participant_id)))

    errors: list[MonevError] = []
    shapes: list[str] = []

    async def _gali(label: str, attempt: Callable[[], Coroutine[Any, Any, Any]]) -> EvaluationResult | None:
        """Satu permintaan; None kalau ditolak atau jawabannya tidak bernilai."""
        try:
            payload = await attempt()
        except MonevError as exc:
            errors.append(exc)
            return None
        shapes.append(f"{label} -> {_shape(payload)}")
        if not evaluation.parse_evaluation(payload, period).has_content:
            return None
        # Kurikulum ditanya setelah ada isinya saja: sebagian besar siklus evaluasi
        # bertanya ke periode yang laporannya memang belum ada.
        curriculum = await fetch_period_curriculum(client, participant_id, start)
        return evaluation.parse_evaluation(payload, period, curriculum)

    for label, attempt in attempts:
        hasil = await _gali(label, attempt)
        if hasil is not None:
            return hasil

    # `latest` hanya ditanya kalau daftar laporan tidak menjawab sama sekali: jawaban
    # "semua laporan" itu sudah superhimpunan dari laporan terbaru, jadi bertanya lagi
    # cuma menambah permintaan tiap 30 menit tanpa peluang menemukan sesuatu.
    if not shapes:
        hasil = await _gali(
            "laporan terbaru",
            partial(client.get, MONTHLY_REPORTS_LATEST_PATH, {"participant_id": participant_id}),
        )
        if hasil is not None:
            return hasil

    if not shapes:
        # Tidak ada satu pun sumber yang menjawab: bukan evaluasinya kosong, tapi
        # tokennya atau servernya. Biar pemanggil yang memutuskan (dan menandai token).
        raise errors[0]
    # Semua sumber menjawab tapi tidak ada yang cocok: yang belum diketahui biasanya
    # pembungkus responsnya, jadi bentuknya dicatat (kunci saja, tanpa nilai).
    log.debug("evaluasi %s tidak ketemu; respons portal: %s", period, " | ".join(shapes))
    return EvaluationResult(period=period)


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


# Pengajuan uang saku = `/payment/claims` — path + parameter digali dari bundle
# SPA (frontend: `fetchClaims({participant_id, schedule_id, period_start})`,
# respon `{data: [...], total}`). `schedule_id` di halaman peserta dibaca dari
# profil sebagai `internship_batch_id` (varian: `schedule_id`).
PAYMENT_CLAIMS_PATH = "/api/v1/payment/claims"

_SCHEDULE_KEYS = {"internshipbatchid", "scheduleid"}


def walk_schedule_ids(node: Any) -> list[str]:
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if isinstance(key, str) and key.lower().replace("_", "") in _SCHEDULE_KEYS:
                if isinstance(value, (str, int)) and str(value).strip():
                    found.append(str(value).strip())
            found.extend(walk_schedule_ids(value))
        return found
    if isinstance(node, list):
        nested: list[str] = []
        for item in node:
            nested.extend(walk_schedule_ids(item))
        return nested
    if isinstance(node, str) and node.strip().startswith(("{", "[")):
        try:
            return walk_schedule_ids(json.loads(node))
        except ValueError:
            return []
    return []


async def discover_schedule_id(client: MonevClient) -> str:
    """ID batch/schedule aktif peserta dari profil — kosong kalau tidak menyebut.

    Dipakai sebagai parameter /payment/claims; endpoint tetap ditanya tanpa itu
    (filter participant_id + period_start seharusnya sudah cukup di sisi server).
    """
    for path in (USER_PROFILE_PATH, HOME_PATH):
        try:
            payload = await client.get(path)
        except MonevError:
            continue
        candidates = walk_schedule_ids(payload)
        if candidates:
            return candidates[0]
    return ""


async def fetch_stipend_claims(
    client: MonevClient, participant_id: str, period_start: str, schedule_id: str = ""
) -> Any:
    """Daftar pengajuan uang saku satu periode (`period_start` = 'YYYY-MM-01')."""
    params: dict[str, str] = {"participant_id": participant_id, "period_start": period_start}
    if schedule_id:
        params["schedule_id"] = schedule_id
    return await client.get(PAYMENT_CLAIMS_PATH, params)
