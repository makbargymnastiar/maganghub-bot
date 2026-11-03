"""Auto-refresh token: syarat jalan, sukses, cookie mati, dan pemulihan saat 401.

Inti dari permintaan "tanpa input token lagi": selama cookie `monev_refresh_token`
masih hidup, bot mencetak access_token baru sendiri lewat POST /api/v1/auth/refresh.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

import pytest

from bot import db as dbmod
from bot import store
from bot.api.client import MonevError, TokenRejected
from bot.jobs import token_health
from bot.models import User
from bot.settings import load_settings, normalize_cookie
from bot.tg import commands
from bot.tg.commands import BotContext, apply_cookie, dispatch, poll_auto
from tests.test_poll_diff import FakeMonevClient, FakeTelegram, make_payloads

TZ = "+07:00"
TODAY = datetime.fromisoformat(f"2026-11-03T10:00:00{TZ}")


def make_jwt(ttl: int = 21600, fp: str = "fp-baru") -> str:
    """JWT tiruan (hanya payload yang dibaca bot: ttl, fingerprint, user)."""

    def seg(obj: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")

    return f"{seg({'typ': 'JWT', 'alg': 'RS256'})}.{seg({'ttl': ttl, 'fingerprint': fp, 'user': 'u1'})}.zz"


class RefreshClient(FakeMonevClient):
    """Fake MonevClient yang juga melayani /auth/refresh dan bisa menolak token."""

    def __init__(self, payloads, *, reject_get_times=0, new_token=None, refresh_error=None) -> None:
        super().__init__(payloads)
        self.reject_get_times = reject_get_times
        self.refresh_error = refresh_error
        self.new_token = new_token or make_jwt()
        self.refresh_posts = 0
        self.refresh_cookie = ""
        self.cookie_name = "monev_refresh_token"
        self.rotated_cookie = ""
        self.token = "lama"

    async def get(self, path, params=None):
        if self.reject_get_times > 0:
            self.reject_get_times -= 1
            raise TokenRejected("token ditolak server (HTTP 401)", 401)
        return await super().get(path, params)

    async def external_json(self, url):
        return {"build_id": "b4acc98"}

    async def post(self, path, payload=None, *, params=None, headers=None):
        self.refresh_posts += 1
        self.last_headers = headers or {}
        if self.refresh_error is not None:
            raise self.refresh_error
        return {"access_token": self.new_token}

    def set_token(self, token):
        self.token = token

    def set_refresh_cookie(self, cookie):
        name, _, value = (cookie or "").partition("=")
        if value:
            self.cookie_name, self.refresh_cookie = name, value
        else:
            self.refresh_cookie = cookie or ""


@pytest.fixture
async def ctx(tmp_path):
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "x",
            "TELEGRAM_CHAT_ID": "111",
            "PARTICIPANT_ID": "pid-1",
            "BEARER_TOKEN": f"Bearer {make_jwt(fp='fp-lama')}",
            "DB_PATH": str(tmp_path / "bot.db"),
            "TOKEN_REFRESH_LEAD_MINUTES": "90",
        }
    )
    database = await dbmod.connect(settings.db_path)
    await store.ensure_user(database, settings, name="Tester")
    await store.update_token(
        database, "111", make_jwt(fp="fp-lama"), "fp-lama", 21600, TODAY
    )
    user = await store.get_user(database, "111")
    yield settings, database, user
    await database.close()


async def save_cookie_and_age_token(database, *, age_minutes):
    """Tanpa fungsi ini tiap test harus mengulang tulis cookie + geser umur token."""
    await store.update_refresh_cookie(database, "111", "monev_refresh_token=abc123", TODAY)
    saved_at = TODAY - timedelta(minutes=age_minutes)
    await store.update_token(
        database, "111", make_jwt(fp="fp-lama"), "fp-lama", 21600, saved_at
    )
    return await store.get_user(database, "111")


# --- syarat kapan keepalive boleh memanggil API ---


async def test_tanpa_cookie_keepalive_diam(ctx):
    settings, database, user = ctx
    client = RefreshClient(make_payloads())
    tg = FakeTelegram()

    fresh = await token_health.keepalive(database, settings, user, client, tg, now=TODAY, force=True)

    assert fresh is None
    assert client.refresh_posts == 0
    assert tg.messages == []


async def test_token_masih_panjang_keepalive_tidak_dipaksa(ctx):
    settings, database, _user = ctx
    user = await save_cookie_and_age_token(database, age_minutes=10)
    client = RefreshClient(make_payloads())
    tg = FakeTelegram()

    fresh = await token_health.keepalive(database, settings, user, client, tg, now=TODAY)

    assert fresh is None
    assert client.refresh_posts == 0


async def test_keepalive_menyimpan_token_baru_saat_mendekati_habis(ctx):
    settings, database, _user = ctx
    user = await save_cookie_and_age_token(database, age_minutes=300)
    await store.set_token_invalid(database, "111", True)
    client = RefreshClient(make_payloads(), new_token=make_jwt(fp="fp-baru"))

    fresh = await token_health.keepalive(database, settings, user, client, FakeTelegram(), now=TODAY)

    assert fresh is not None
    assert fresh.token_fingerprint == "fp-baru"
    assert fresh.token_invalid is False
    assert client.token == fresh.bearer_token
    assert client.last_headers["X-Frontend-Build-ID"] == "b4acc98"
    events = await store.recent_events(database, "111", 5)
    assert any("diperbarui otomatis" in e["message"] for e in events)


async def test_cookie_mati_dilapor_sekali_saja(ctx):
    settings, database, _user = ctx
    user = await save_cookie_and_age_token(database, age_minutes=300)
    error = TokenRejected("token ditolak server (HTTP 401)", 401)
    tg = FakeTelegram()

    for _ in range(3):
        fresh = await token_health.keepalive(
            database, settings, user, RefreshClient(make_payloads(), refresh_error=error), tg, now=TODAY
        )
        user = await store.get_user(database, "111")

    assert fresh is None
    assert len(tg.messages) == 1
    assert "/cookie" in tg.messages[0]


# --- perintah /cookie ---


async def test_cookie_kosong_mengirim_panduan(ctx, monkeypatch):
    settings, database, _user = ctx
    bot_ctx = BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: RefreshClient({}))

    message = await apply_cookie(bot_ctx, "")

    assert "Application" in message and "/cookie" in message


async def test_cookie_divalidasi_ke_api_lalu_tersimpan(ctx, monkeypatch):
    settings, database, _user = ctx
    bot_ctx = BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)
    fake = RefreshClient(make_payloads(), new_token=make_jwt(fp="fp-baru"))
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: fake)

    message = await apply_cookie(bot_ctx, "abc123")

    assert "Cookie refresh tersimpan" in message
    stored = await store.get_user(database, "111")
    assert stored.refresh_cookie == normalize_cookie("abc123")
    assert stored.token_fingerprint == "fp-baru"
    assert fake.refresh_posts == 1


async def test_cookie_ditolak_server_tidak_disimpan(ctx, monkeypatch):
    settings, database, _user = ctx
    bot_ctx = BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)
    fake = RefreshClient(make_payloads(), refresh_error=TokenRejected("Sesi masuk tidak tersedia", 401))
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: fake)

    message = await apply_cookie(bot_ctx, "cookie-buruk")

    assert "Sesi monev benar-benar berakhir" in message
    stored = await store.get_user(database, "111")
    assert stored.refresh_cookie == ""


async def test_dispatch_melayani_perintah_cookie(ctx, monkeypatch):
    settings, database, _user = ctx
    tg = FakeTelegram()
    bot_ctx = BotContext(db=database, settings=settings, tg=tg, poll_minutes=15)
    monkeypatch.setattr(
        commands, "MonevClient", lambda base, token, **kw: RefreshClient(make_payloads())
    )

    await dispatch(bot_ctx, "111", "/cookie monev_refresh_token=abc123")

    assert "Cookie refresh tersimpan" in tg.messages[0]


# --- pemulihan otomatis di jalur polling ---


async def test_poll_auto_memulihkan_token_lalu_mengulang(ctx, monkeypatch):
    settings, database, _user = ctx
    user = await save_cookie_and_age_token(database, age_minutes=300)
    tg = FakeTelegram()
    bot_ctx = BotContext(
        db=database, settings=settings, tg=tg, poll_minutes=15, _user=user
    )
    client = RefreshClient(make_payloads(), reject_get_times=1, new_token=make_jwt(fp="fp-baru"))
    monkeypatch.setattr(bot_ctx, "client_for", lambda _user: client)

    result = await poll_auto(bot_ctx)

    assert client.refresh_posts == 1
    assert result is not None
    assert (await store.get_user(database, "111")).token_fingerprint == "fp-baru"
    assert tg.messages == []  # user tidak sempat dimintai token manual


async def test_poll_auto_tetap_mengeluh_kalau_cookie_tidak_ada(ctx):
    settings, database, user = ctx
    tg = FakeTelegram()
    bot_ctx = BotContext(
        db=database, settings=settings, tg=tg, poll_minutes=15, _user=user
    )
    client = RefreshClient(make_payloads(), reject_get_times=99)
    bot_ctx.client_for = lambda _user: client  # type: ignore[method-assign]

    with pytest.raises(TokenRejected):
        await poll_auto(bot_ctx)

    assert client.refresh_posts == 0


async def test_poll_auto_tanpa_token_memberi_pesan_bukan_crash(ctx):
    settings, database, user = ctx
    user = User(tg_chat_id=user.tg_chat_id, participant_id=user.participant_id, bearer_token="")
    bot_ctx = BotContext(
        db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15, _user=user
    )

    with pytest.raises(MonevError) as excinfo:
        await poll_auto(bot_ctx)

    assert "/cookie" in str(excinfo.value)
