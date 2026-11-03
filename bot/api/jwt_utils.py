"""Dekoder JWT (tanpa verifikasi tanda tangan) + util token.

Penting: JWT monev TIDAK punya klaim `exp`/`iat` — hanya
`{client, user, ttl, sub, aud, fingerprint}`. Jadi masa berlaku token tidak
bisa dibaca dari tokennya, dan harus diperkirakan dari kapan token itu
disimpan (`ttl` detik). Sesi lama juga dibatalkan saat login baru, sehingga
`fingerprint` dipakai untuk mendeteksi pergantian sesi.
"""

from __future__ import annotations

import base64
import json
from typing import Any

DEFAULT_TTL_SECONDS = 21600  # 6 jam, nilai `ttl` pada token monev


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_claims(token: str) -> dict[str, Any]:
    """Ambil payload JWT. Return dict kosong bila format tidak valid."""
    token = (token or "").strip().removeprefix("Bearer ").strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = _b64url_decode(parts[1])
        data = json.loads(payload)
    except (ValueError, base64.binascii.Error):
        return {}
    return data if isinstance(data, dict) else {}


def looks_like_jwt(text: str) -> bool:
    token = (text or "").strip().removeprefix("Bearer ").strip()
    return token.startswith("eyJ") and token.count(".") >= 2


def fingerprint(token: str) -> str:
    return str(decode_claims(token).get("fingerprint", ""))


def ttl_seconds(token: str) -> int:
    claims = decode_claims(token)
    try:
        return int(claims.get("ttl") or DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS


def session_user(token: str) -> str:
    return str(decode_claims(token).get("user", ""))
