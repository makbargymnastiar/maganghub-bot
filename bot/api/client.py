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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip().removeprefix("Bearer ").strip()
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

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        last_error: Exception | None = None

        for attempt in range(len(BACKOFF_SECONDS) + 1):
            try:
                resp = await self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < len(BACKOFF_SECONDS):
                    await asyncio.sleep(BACKOFF_SECONDS[attempt])
                    continue
                raise MonevError(f"gagal koneksi ke monev API: {exc.__class__.__name__}") from exc

            if resp.status_code in (401, 403):
                raise TokenRejected(f"token ditolak server (HTTP {resp.status_code})", resp.status_code)

            if resp.status_code == 429:
                raise RateLimited("API membatasi laju request (HTTP 429)", 429)

            if resp.status_code in RETRYABLE_STATUS:
                last_error = MonevError(f"HTTP {resp.status_code} dari {path}", resp.status_code)
                if attempt < len(BACKOFF_SECONDS):
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
