"""Response `/api/v1/users/announcements` dikirim sebagai teks base64 dari JSON.

Dilihat dari HAR, body-nya `eyJkYXRhIjpbeyJpZCI6...` (bukan JSON langsung),
jadi perlu didekode dulu sebelum bisa dibaca.
"""

from __future__ import annotations

import base64
import json
from typing import Any


def maybe_b64_json(payload: Any) -> Any:
    """Kembalikan objek JSON hasil dekode; kalau sudah ter-parse, lewati."""
    if isinstance(payload, (dict, list)):
        return payload
    if not isinstance(payload, str):
        return {}

    text = payload.strip()
    if not text:
        return {}
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    try:
        padding = "=" * (-len(text) % 4)
        decoded = base64.b64decode(text + padding)
        return json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return {}
