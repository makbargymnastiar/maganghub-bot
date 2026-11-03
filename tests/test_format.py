"""Uji alur notifikasi pengisian + perenderan pesan Telegram."""

import base64
import json
from datetime import datetime

import pytest

from bot import store
from bot.api import endpoints
from bot.api.client import MonevError, TokenRejected
from bot.jobs.poll import run_poll
from bot.status import APPROVED, PARTIAL
from bot.tg import format as fmt
from bot.tg.commands import BotContext, apply_token, dispatch
from tests.test_poll_diff import FakeMonevClient, FakeTelegram, make_payloads

TODAY = datetime.fromisoformat("2026-11-03T10:00:00+07:00")

BODY = base64.urlsafe_b64encode(json.dumps({"ttl": 21600, "user": "u1"}).encode()).decode().rstrip("=")
JWT_SAMPLE = f"eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.{BODY}.rantai-sinyur-panjang"

ATT = {"date": "2026-11-03", "status": "PRESENT", "approval_status": None}
FULL = {"activity_log": "Merakit PC", "lesson_learned": "Belajar subnetting", "obstacles": "Kabel kurang"}


def daily(**content) -> dict:
    """Satu baris daily-log dengan tiga isian kosong, lalu ditimpa `content`."""
    base = {"date": "2026-11-03", "state": "DRAFT", "id": 7, "activity_log": "", "lesson_learned": "", "obstacles": ""}
    base.update(content)
    return base


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


async def poll_once(database, settings, user, log, notify):
    await run_poll(
        database, settings, user, FakeMonevClient(make_payloads(attendance=ATT, log=log)), now=TODAY, notify=notify
    )


async def test_alur_pengisian_mengirim_pesan_berjenjang(ctx):
    """Konsep inti user: diingatkan selama belum lengkap, dilapor saat sudah diisi."""
    settings, database, user = ctx
    tg = FakeTelegram()

    await poll_once(database, settings, user, daily(), tg)
    assert tg.messages == []  # siklus pertama = seed, tidak spam

    await poll_once(database, settings, user, daily(activity_log="Merakit PC"), tg)
    assert "Sebagian Terisi" in tg.messages[-1]
    assert "Masih kosong: pembelajaran, kendala" in tg.messages[-1]

    # tambah satu kolom lagi: label tetap PARTIAL -> sengaja tidak pesan baru (anti spam)
    count = len(tg.messages)
    await poll_once(database, settings, user, daily(activity_log="Merakit PC", lesson_learned="Belajar subnetting"), tg)
    assert len(tg.messages) == count

    await poll_once(database, settings, user, daily(**FULL), tg)
    assert "Sudah Diisi, Belum Dikirim" in tg.messages[-1]

    await poll_once(database, settings, user, daily(state="COMPLETED", **FULL), tg)
    assert "Menunggu Persetujuan" in tg.messages[-1]

    # isian yang sama tidak diulang
    before = len(tg.messages)
    await poll_once(database, settings, user, daily(state="COMPLETED", **FULL), tg)
    assert len(tg.messages) == before


async def test_pesan_revisi_membawa_catatan_mentor(ctx):
    settings, database, user = ctx
    tg = FakeTelegram()
    await poll_once(database, settings, user, daily(state="COMPLETED", **FULL), tg)

    revision = daily(state="NEEDS_REVISION", reviewer_notes="Bagian jaringan belum rinci", **FULL)
    await poll_once(database, settings, user, revision, tg)

    assert "Perlu Tindakan Anda" in tg.messages[-1]
    assert "Catatan mentor: Bagian jaringan belum rinci" in tg.messages[-1]


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


def test_pesan_reminder_jujur_untuk_status_yang_tidak_ditagih():
    nag = fmt.reminder_message("evening", "2026-11-03", PARTIAL)
    assert "belum lengkap" in nag
    assert "Lengkapi isian yang masih kosong" in nag

    calm = fmt.reminder_message("evening", "2026-11-03", APPROVED)
    assert "belum lengkap" not in calm
    assert "status tercatat" in calm


def test_help_text_menyebut_tiga_isian():
    text = fmt.help_text("monev_bot")
    for word in ("aktivitas", "pembelajaran", "kendala", "Sudah Diisi"):
        assert word in text
    assert "/panduan" in text


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
    rendered = fmt.token_expired_message("token ditolak server (HTTP <401>)")
    assert "&lt;401&gt;" in rendered
    assert "F12" in rendered and "Console" in rendered
    assert "/token" in rendered
