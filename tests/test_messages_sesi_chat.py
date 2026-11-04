"""Uji kalimat sesi (token/cookie) & chat (/help, panduan, balasan perintah)."""

import base64
import json
from datetime import datetime

import pytest

from bot import store
from bot.monev import endpoints
from bot.monev.client import MonevError, TokenRejected
from bot.messages import chat, sesi
from bot.telegram.commands import BotContext, apply_token, dispatch
from tests.test_poll_diff import FakeTelegram

TODAY = datetime.fromisoformat("2026-11-03T10:00:00+07:00")

BODY = base64.urlsafe_b64encode(json.dumps({"ttl": 21600, "user": "u1"}).encode()).decode().rstrip("=")
JWT_SAMPLE = f"eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.{BODY}.rantai-sinyur-panjang"


@pytest.fixture
async def ctx(tmp_path):
    from bot import db as dbmod
    from bot.settings import load_settings

    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "x",
            "TELEGRAM_CHAT_ID": "111",
            "PARTICIPANT_ID": "pid-1",
            "BEARER_TOKEN": "Bearer abc",
            "DB_PATH": str(tmp_path / "bot.db"),
        }
    )
    database = await dbmod.connect(settings.db_path)
    user = await store.ensure_user(database, settings, name="Tester")
    yield settings, database, user
    await database.close()


async def test_koneksi_gagal_tidak_dituduh_sebagai_token_salah(ctx, monkeypatch):
    settings, database, user = ctx
    bot_ctx = BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)

    async def unreachable(client):
        raise MonevError("gagal koneksi ke monev API: ConnectError")

    monkeypatch.setattr(endpoints, "fetch_home", unreachable)
    message = await apply_token(bot_ctx, JWT_SAMPLE)

    assert "belum bisa diverifikasi" in message
    assert "ditolak" not in message
    fresh = await store.get_user(database, user.tg_chat_id)
    assert fresh.bearer_token == "abc"  # token lama tidak tertimpa


async def test_token_ditolak_server_dilapor_ditolak(ctx, monkeypatch):
    settings, database, user = ctx
    bot_ctx = BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)

    async def rejected(client):
        raise TokenRejected("token ditolak server (HTTP 401)", 401)

    monkeypatch.setattr(endpoints, "fetch_home", rejected)
    message = await apply_token(bot_ctx, JWT_SAMPLE)

    assert "Token ditolak server" in message
    assert "tools/bookmarklet.md" in message


async def test_bukan_jwt_tidak_dicoba_ke_api(ctx, monkeypatch):
    """String asing ditolak sebelum ada panggilan jaringan."""
    settings, database, user = ctx
    bot_ctx = BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)
    calls = []

    async def spy(client):
        calls.append(client)
        return {"date": "2026-11-03"}

    monkeypatch.setattr(endpoints, "fetch_home", spy)
    message = await apply_token(bot_ctx, "saya-yakin-ini-bukan-token")

    assert "bukan token JWT" in message
    assert calls == []


async def test_token_kosong_mengirim_panduan_bukan_error(ctx):
    """/token tanpa tempelan token = user lupa; jawabnya panduan, bukan teguran."""
    settings, database, user = ctx
    bot_ctx = BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)
    message = await apply_token(bot_ctx, "")

    assert "Cara mengambil" in message
    assert "F12" in message and "allow pasting" in message
    assert "bukan token JWT" not in message


async def test_panduan_lewat_dispatch_dan_muat_di_telegram(ctx):
    settings, database, _user = ctx
    tg = FakeTelegram()
    bot_ctx = BotContext(db=database, settings=settings, tg=tg, poll_minutes=15)

    await dispatch(bot_ctx, "111", "/panduan")

    assert len(tg.messages) == 1
    guide = tg.messages[0]
    assert "token monev" in guide
    assert "/token" in guide
    assert len(guide) < 4096  # batas panjang pesan Telegram


def test_pesan_token_kedaluwarsa_mengandung_langkah_dan_escaping():
    rendered = sesi.token_expired_message("token ditolak server (HTTP <401>)")
    assert "&lt;401&gt;" in rendered
    assert "TOKEN MONEV TIDAK BERLAKU" in rendered
    assert "/token &lt;token&gt;" in rendered and "/cookie &lt;refresh_token&gt;" in rendered


def test_help_text_akhir_zaman():
    text = chat.help_text("monev_bot")
    for word in ("aktivitas", "pembelajaran", "kendala", "/panduan", "/cookie"):
        assert word in text
    # /reminder sudah dibuang — pengingat terkunci
    assert "/reminder" not in text
    assert "terkunci" in text
    assert "18:00–20:59" in text and "uang saku" in text
