"""Uji decoder response base64 (announcements) + util settings/JWT."""

import base64
import json

from bot.monev.b64 import maybe_b64_json
from bot.monev.jwt_utils import decode_claims, fingerprint, looks_like_jwt, ttl_seconds
from bot.settings import _derive_base_url, load_settings, mask_token

ANNOUNCEMENTS_B64 = base64.b64encode(
    json.dumps({"data": [{"id": 23, "title": "Channel WhatsApp", "type": "INFO", "enabled": True}]}).encode()
).decode()


def test_maybe_b64_json_mendekode_teks_base64():
    decoded = maybe_b64_json(ANNOUNCEMENTS_B64)
    assert decoded["data"][0]["id"] == 23


def test_maybe_b64_json_lewati_yang_sudah_objek():
    assert maybe_b64_json({"data": [1]}) == {"data": [1]}
    assert maybe_b64_json('{"a": 1}') == {"a": 1}
    assert maybe_b64_json("bukan-base64-!!!") == {}
    assert maybe_b64_json(None) == {}


def test_derive_base_url_dari_url_endpoint_lengkap():
    assert (
        _derive_base_url("https://monev-api.maganghub.kemnaker.go.id/api/v1/daily-logs")
        == "https://monev-api.maganghub.kemnaker.go.id"
    )
    assert _derive_base_url("") == "https://monev-api.maganghub.kemnaker.go.id"


def test_settings_menormalkan_bearer_dan_masking():
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_CHAT_ID": 12345,
            "MONEV_API_URL": "https://monev-api.maganghub.kemnaker.go.id/api/v1/daily-logs",
            "PARTICIPANT_ID": "p",
            "BEARER_TOKEN": "Bearer  abc.def.ghi ",
        }
    )
    assert settings.bearer_token == "abc.def.ghi"
    assert settings.telegram_chat_id == "12345"
    assert settings.poll_minutes >= 5
    masked = mask_token("abcdefghijklmnopqrstuvwxyz")
    assert masked.startswith("abcdefghij")
    assert masked.endswith("uvwxyz")
    assert "klmnopqrst" not in masked
    assert mask_token("") == "(kosong)"


def test_jwt_utils_pada_token_monev_sejati():
    """Token monev tidak punya exp/iat; hanya ttl dan fingerprint yang bisa dibaca."""
    payload = {"client": "c1", "user": "u1", "ttl": 21600, "fingerprint": "fp1234567890"}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.{body}.signature"

    assert looks_like_jwt(token) is True
    assert looks_like_jwt("bukan token") is False
    assert decode_claims(token)["ttl"] == 21600
    assert ttl_seconds(token) == 21600
    assert fingerprint(token) == "fp1234567890"
    assert decode_claims("xxx") == {}
