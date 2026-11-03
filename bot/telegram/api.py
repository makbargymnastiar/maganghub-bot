"""Transport Telegram (Bot API) murni httpx — tanpa python-telegram-bot.

Alasan: `.venv` memakai Python 3.14 yang belum didukung PTB v21, dan yang
dibutuhkan bot ini hanya sendMessage + getUpdates long polling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("bot.telegram")

TEXT_LIMIT = 4096
SAFE_LIMIT = 3800


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 40.0,
    ) -> None:
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(f"{self._base}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            description = data.get("description", "")
            if data.get("error_code") == 429:
                retry_after = data.get("parameters", {}).get("retry_after", 5)
                await asyncio.sleep(min(int(retry_after), 30))
                return await self._call(method, payload)
            raise TelegramError(f"{method} gagal: {data.get('error_code')} {description}")
        return data["result"]

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe", {})

    async def send_message(self, chat_id: str, text: str, *, disable_preview: bool = True) -> None:
        for chunk in self._chunk(text):
            try:
                await self._call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": disable_preview,
                    },
                )
            except TelegramError as exc:
                # HTML rusak (mis. karakter < dari data API) -> kirim ulang sebagai teks polos
                log.warning("sendMessage HTML gagal (%s), ulangi sebagai teks", exc)
                await self._call(
                    "sendMessage",
                    {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": disable_preview},
                )

    @staticmethod
    def _chunk(text: str) -> list[str]:
        if len(text) <= SAFE_LIMIT:
            return [text]
        parts, current = [], ""
        for line in text.splitlines(keepends=True):
            if len(current) + len(line) > SAFE_LIMIT:
                parts.append(current)
                current = ""
            current += line
        if current:
            parts.append(current)
        return parts

    async def get_updates(self, offset: int | None, timeout: float = 25.0) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": int(timeout),
            "allowed_updates": ["message"],
        }
        if offset:
            payload["offset"] = offset + 1
        try:
            resp = await self._client.post(
                f"{self._base}/getUpdates", json=payload, timeout=timeout + 10
            )
            data = resp.json()
        except httpx.HTTPError as exc:
            log.warning("getUpdates error: %s", exc.__class__.__name__)
            return []
        if not data.get("ok"):
            log.warning("getUpdates tidak ok: %s", data.get("description"))
            await asyncio.sleep(3)
            return []
        return data["result"]
