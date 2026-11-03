"""HTTP client untuk API monev Maganghub.

Perbaikan dari versi lama: tidak hanya membedakan 200/401, tapi juga
retry+backoff untuk network error dan 5xx, pemisahan 401/403 (token mati),
429 (rate limit), serta error non-JSON yang selama ini hilang tanpa jejak.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("bot.client")

RETRYABLE_STATUS = {500, 502, 503, 504}
BACKOFF_SECONDS = (1, 2, 4)


class MonevError(Exception):
    """Gagal memanggil API monev (setelah retry)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TokenRejected(MonevError):
    """401/403: sesi token mati atau diganti login lain."""


class RateLimited(MonevError):
    """429: terlalu sering memanggil API."""


class MonevClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        refresh_cookie: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip().removeprefix("Bearer ").strip()
        # Cookie httpOnly `monev_refresh_token` dari domain API. Satu-satunya
        # kredensial yang bisa memperbarui access_token (POST /api/v1/auth/refresh).
        self.refresh_cookie = refresh_cookie.strip()
        # Diisi bila server mengirim Set-Cookie saat refresh (rotasi cookie).
        self.rotated_cookie = ""
        # Nama cookie yang dipakai portal; dibaca dari Set-Cookie respons.
        self.cookie_name = "monev_refresh_token"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "*/*",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) maganghub-reminder-bot",
            },
        )

    def set_token(self, token: str) -> None:
        self.token = token.strip().removeprefix("Bearer ").strip()

    def set_refresh_cookie(self, cookie: str) -> None:
        """Terima nilai mentah dari DevTools: boleh hanya nilainya, atau `nama=nilai`."""
        cookie = (cookie or "").strip()
        if "=" in cookie:
            name, _, value = cookie.partition("=")
            self.cookie_name = name.strip() or self.cookie_name
            cookie = value.strip().partition(";")[0].strip()
        self.refresh_cookie = cookie

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.refresh_cookie:
            headers["Cookie"] = f"{self.cookie_name}={self.refresh_cookie}"
        if extra:
            headers.update({k: v for k, v in extra.items() if v})
        return headers

    async def close(self) -> None:
        await self._client.aclose()

    async def external_json(self, url: str) -> Any:
        """Ambil JSON di luar API monev (mis. `version.json` milik SPA), tanpa kredensial."""
        try:
            resp = await self._client.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MonevError(f"gagal mengambil {url}: {exc.__class__.__name__}") from exc

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST tunggal (tanpa retry) — dipanggil hanya untuk /auth/refresh."""
        return await self._request("POST", path, params=params, json_body=payload, headers=headers, retries=0)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retries: int = len(BACKOFF_SECONDS),
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        sent_headers = self._auth_headers(headers)
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                resp = await self._client.request(
                    method, url, params=params, json=json_body, headers=sent_headers
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(BACKOFF_SECONDS[attempt])
                    continue
                raise MonevError(f"gagal koneksi ke monev API: {exc.__class__.__name__}") from exc

            self._capture_cookie(resp)

            if resp.status_code in (401, 403):
                raise TokenRejected(f"token ditolak server (HTTP {resp.status_code})", resp.status_code)

            if resp.status_code == 429:
                raise RateLimited("API membatasi laju request (HTTP 429)", 429)

            if resp.status_code in RETRYABLE_STATUS:
                last_error = MonevError(f"HTTP {resp.status_code} dari {path}", resp.status_code)
                if attempt < retries:
                    await asyncio.sleep(BACKOFF_SECONDS[attempt])
                    continue
                raise last_error

            if resp.status_code >= 400:
                raise MonevError(f"HTTP {resp.status_code} dari {path}: {resp.text[:200]}", resp.status_code)

            try:
                return resp.json()
            except ValueError as exc:
                raise MonevError(f"response {path} bukan JSON", resp.status_code) from exc

        raise MonevError(str(last_error or "unknown error"))

    def _capture_cookie(self, resp: httpx.Response) -> None:
        """Simpan cookie refresh bila server merotasinya saat respons."""
        raw = resp.headers.get("set-cookie", "")
        if not raw or "Max-Age=0" in raw:
            return  # penghapusan cookie (logout) bukan rotasi
        for part in raw.split(","):
            name_value = part.split(";")[0].strip()
            if "=" not in name_value:
                continue
            name, _, value = name_value.partition("=")
            if "refresh" in name.lower():
                self.cookie_name = name.strip()
                if value.strip():
                    self.rotated_cookie = value.strip()
                return
