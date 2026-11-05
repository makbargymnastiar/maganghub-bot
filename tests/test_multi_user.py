"""Multi-user: pendaftaran otomatis, pembacaan participant_id, routing per chat.

Yang dijaga di sini:
- teman bisa langsung pakai bot tanpa pemilik harus menyentuh config/restart;
- token/cookie dari chat baru otomatis membuat akun (dibatasi MAX_USERS);
- status `pending` = belum dipoll/direminder, tapi perintah panduan tetap jalan;
- setiap chat hanya pernah melihat datanya sendiri;
- kalau INVITE_CODE diisi, pendaftaran kembali butuh kode.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from bot import db as dbmod
from bot import store
from bot.models import STATUS_ACTIVE, STATUS_PENDING
from bot.monev.client import MonevError
from bot.settings import load_settings
from bot.telegram import commands
from bot.telegram.commands import BotContext, dispatch
from tests.test_auto_refresh import make_jwt
from tests.test_poll_diff import FakeMonevClient

TZ = "+07:00"
TODAY = datetime.fromisoformat(f"2026-11-03T10:00:00{TZ}")
OWNER = "111"
FRIEND = "222"
INVITE = "KODE-AJAR-BERSAMA"
HOME_PAYLOAD = {"/api/v1/users/me/home": {"data": {"date": "2026-11-03", "active_internship": True}}}


class RecordingTelegram:
    """FakeTelegram yang juga mencatat chat_id tujuannya (routing itu intinya)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((str(chat_id), text))

    def texts_for(self, chat_id: str) -> list[str]:
        return [text for chat, text in self.sent if chat == str(chat_id)]

    @property
    def last_text(self) -> str:
        return self.sent[-1][1]


def portal_client(participant_id: str | None = None):
    """Fake MonevClient: `/users/me` menjawab profil (dengan/tanpa participant_id).

    Path yang tidak dikenal dilempar sebagai MonevError — persis seperti client
    nyata — supaya discovery tidak bocor ke KeyError.
    """

    class _Client(FakeMonevClient):
        async def get(self, path, params=None):
            if path == "/api/v1/users/me":
                profile = {"name": "Teman"}
                if participant_id is not None:
                    profile["participant_id"] = participant_id
                else:
                    profile["id"] = "user-kemnaker-1"
                return {"data": profile}
            if path in self.payloads:
                return await super().get(path, params)
            raise MonevError(f"tidak ada fixture untuk {path}", 500)

        def set_token(self, token):
            pass

        def set_refresh_cookie(self, cookie):
            pass

    return _Client(dict(HOME_PAYLOAD))


def settings_dengan(invite_code: str = "", **extra):
    raw = {
        "TELEGRAM_BOT_TOKEN": "x",
        "TELEGRAM_CHAT_ID": OWNER,
        "PARTICIPANT_ID": "pid-owner",
        "DB_PATH": extra.pop("db_path"),
        "INVITE_CODE": invite_code,
    }
    raw.update(extra)
    return load_settings(raw)


async def buat_env(tmp_path, invite_code: str = "", **extra):
    settings = settings_dengan(invite_code, db_path=str(tmp_path / "bot.db"), **extra)
    database = await dbmod.connect(settings.db_path)
    await store.ensure_user(database, settings, name="Owner")
    tg = RecordingTelegram()
    ctx = BotContext(db=database, settings=settings, tg=tg, poll_minutes=15)
    return settings, database, ctx, tg


@pytest.fixture
async def env_terbuka(tmp_path):
    """Keadaan default: pendaftaran terbuka, tanpa kode undangan."""
    result = await buat_env(tmp_path)
    yield result
    await result[1].close()


@pytest.fixture
async def env_berkode(tmp_path):
    result = await buat_env(tmp_path, invite_code=INVITE)
    yield result
    await result[1].close()


# --- pendaftaran otomatis (tanpa pemilik harus standby) ---


async def test_tempel_token_langsung_menjadi_pengguna_aktif(env_terbuka, monkeypatch):
    """Satu langkah: chat baru + tempel token = terdaftar, ID terbaca dari portal."""
    _settings, database, ctx, tg = env_terbuka
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: portal_client("pid-temen"))

    await dispatch(ctx, FRIEND, make_jwt(), name="Teman")

    user = await store.get_user(database, FRIEND)
    assert user is not None
    assert user.participant_id == "pid-temen"  # tidak perlu mengetik ID
    assert user.status == STATUS_ACTIVE
    assert "Akun terhubung" in tg.last_text
    assert any("Pengguna baru" in text for text in tg.texts_for(OWNER))
    assert FRIEND in [u.tg_chat_id for u in await ctx.active_users()]


async def test_token_tanpa_participant_id_terbaca_meminta_daftar(env_terbuka, monkeypatch):
    """Kalau portal tak menyebutkan ID, user diminta /daftar — bukan didiamkan."""
    _settings, database, ctx, tg = env_terbuka
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: portal_client(None))

    await dispatch(ctx, FRIEND, make_jwt())

    user = await store.get_user(database, FRIEND)
    assert user is not None and user.status == STATUS_PENDING
    assert "/daftar" in tg.last_text


async def test_perintah_lain_dari_chat_baru_mengirim_petunjuk_daftar(env_terbuka):
    _settings, database, ctx, tg = env_terbuka

    await dispatch(ctx, FRIEND, "/status")

    # urutan pengiriman: sambutan+petunjuk ke chat baru, lalu laporan ke owner
    assert "Selamat Datang di Bot Absensi MagangHub" in tg.texts_for(FRIEND)[-1]
    assert await store.get_user(database, FRIEND) is None  # belum ada yang dibuat
    assert any("chat asing" in text for text in tg.texts_for(OWNER))


async def test_start_dari_chat_baru_hanya_disambut(env_terbuka):
    """Menekan "Mulai" bukan pelanggaran: sambut, jangan laporkan sebagai chat asing."""
    _settings, database, ctx, tg = env_terbuka

    await dispatch(ctx, FRIEND, "/start", name="Teman")

    sambutan = tg.texts_for(FRIEND)[-1]
    assert "Selamat Datang di Bot Absensi MagangHub" in sambutan
    assert "/token &lt;Bearer" in sambutan and "Fitur Utama Bot" in sambutan
    assert tg.sent == [(FRIEND, sambutan)]  # tidak ada laporan ke owner, tidak ada baris DB
    assert await store.get_user(database, FRIEND) is None


async def test_panduan_dari_chat_baru_menjelaskan_cara_ambil_token(env_terbuka):
    _settings, database, ctx, tg = env_terbuka

    await dispatch(ctx, FRIEND, "/panduan")

    assert "Cara mengambil" in tg.texts_for(FRIEND)[-1]
    assert not tg.texts_for(OWNER)


async def test_daftar_tanpa_kode_saat_terbuka_langsung_dicatat(env_terbuka):
    _settings, database, ctx, tg = env_terbuka

    await dispatch(ctx, FRIEND, "/daftar pid-temen")

    user = await store.get_user(database, FRIEND)
    assert user.participant_id == "pid-temen" and user.status == STATUS_PENDING
    assert "Pendaftaran dicatat" in tg.last_text


async def test_kota_pengguna_dibatasi_max_users(tmp_path):
    _settings, database, ctx, tg = await buat_env(tmp_path, MAX_USERS="1")
    try:
        await dispatch(ctx, FRIEND, "/daftar pid-temen")

        assert "Kuota pengguna bot sudah penuh" in tg.last_text
        assert await store.get_user(database, FRIEND) is None
    finally:
        # koneksi yang tertinggal sampai di-GC setelah loop tertutup = warning aiosqlite
        await database.close()


# --- kalau owner mengunci dengan kode undangan ---


async def test_daftar_kode_salah_ditolak_tanpa_mencatat(env_berkode):
    _settings, database, ctx, tg = env_berkode

    await dispatch(ctx, FRIEND, "/daftar kode-ngasal pid-temen")

    assert "Kode undangan tidak cocok" in tg.last_text
    assert await store.get_user(database, FRIEND) is None


async def test_daftar_format_kurang_mengirim_panduan_kode(env_berkode):
    _settings, database, ctx, tg = env_berkode

    await dispatch(ctx, FRIEND, f"/daftar {INVITE}")

    assert "Format pendaftaran" in tg.last_text and "kode undangan" in tg.last_text
    assert await store.get_user(database, FRIEND) is None


async def test_daftar_kode_benar_dicatat(env_berkode):
    _settings, database, ctx, tg = env_berkode

    await dispatch(ctx, FRIEND, f"/daftar {INVITE} pid-temen")

    user = await store.get_user(database, FRIEND)
    assert user is not None and user.participant_id == "pid-temen"


async def test_tempel_token_ditolak_saat_pendaftaran_terkunci(env_berkode):
    _settings, database, ctx, tg = env_berkode

    await dispatch(ctx, FRIEND, make_jwt())

    assert "kode undangan" in tg.last_text
    assert await store.get_user(database, FRIEND) is None


# --- verifikasi klaim participant_id ---


async def test_token_cocok_mengaktifkan_pemantauan(env_terbuka, monkeypatch):
    _settings, database, ctx, tg = env_terbuka
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: portal_client("pid-temen"))
    await dispatch(ctx, FRIEND, "/daftar pid-temen")

    await dispatch(ctx, FRIEND, f"/token {make_jwt()}")

    assert (await store.get_user(database, FRIEND)).status == STATUS_ACTIVE
    assert "Akun terhubung" in tg.last_text


async def test_token_milik_akun_lain_tidak_mengaktifkan(env_terbuka, monkeypatch):
    _settings, database, ctx, tg = env_terbuka
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: portal_client("pid-asli"))
    await dispatch(ctx, FRIEND, "/daftar pid-klaim-orang-lain")

    await dispatch(ctx, FRIEND, f"/token {make_jwt()}")

    assert (await store.get_user(database, FRIEND)).status == STATUS_PENDING
    assert "Verifikasi gagal" in tg.last_text


# --- routing & gerbang status ---


async def test_user_pending_belum_boleh_minta_rekap(env_terbuka):
    _settings, database, ctx, tg = env_terbuka
    await dispatch(ctx, FRIEND, "/daftar pid-temen")

    await dispatch(ctx, FRIEND, "/rekap")

    assert "belum terverifikasi" in tg.last_text


async def test_user_pending_tetap_bisa_baca_panduan(env_terbuka):
    _settings, database, ctx, tg = env_terbuka
    await dispatch(ctx, FRIEND, "/daftar pid-temen")

    await dispatch(ctx, FRIEND, "/panduan")

    assert "Cara mengambil" in tg.last_text


async def test_data_user_satu_tidak_pernah_dibawa_ke_chat_lain(env_terbuka, monkeypatch):
    """Balasan /rekap hanya berisi data milik chat yang bertanya."""
    _settings, database, ctx, tg = env_terbuka
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: portal_client("pid-temen"))
    await dispatch(ctx, FRIEND, make_jwt())
    await store.upsert_day_state(database, FRIEND, "2026-11-03", "APPROVED", {}, TODAY)
    await store.upsert_day_state(database, OWNER, "2026-11-03", "NEEDS_REVISION", {}, TODAY)

    await dispatch(ctx, FRIEND, "/rekap")

    rekap_friend = tg.texts_for(FRIEND)[-1]
    assert "Disetujui" in rekap_friend and "Perlu Revisi" not in rekap_friend


# --- scheduler ---


async def test_active_users_hanya_user_terverifikasi(env_terbuka, monkeypatch):
    _settings, database, ctx, tg = env_terbuka
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: portal_client("pid-temen"))

    assert [u.tg_chat_id for u in await ctx.active_users()] == [OWNER]

    await dispatch(ctx, FRIEND, "/daftar pid-temen")
    assert [u.tg_chat_id for u in await ctx.active_users()] == [OWNER]  # masih pending

    await dispatch(ctx, FRIEND, f"/token {make_jwt()}")
    assert sorted(u.tg_chat_id for u in await ctx.active_users()) == sorted([OWNER, FRIEND])


async def test_stop_mengeluarkan_user_dari_scheduler(env_terbuka, monkeypatch):
    _settings, database, ctx, tg = env_terbuka
    monkeypatch.setattr(commands, "MonevClient", lambda base, token, **kw: portal_client("pid-temen"))
    await dispatch(ctx, FRIEND, make_jwt())

    await dispatch(ctx, FRIEND, "/stop")

    assert FRIEND not in [u.tg_chat_id for u in await ctx.active_users()]
    assert await store.get_user(database, FRIEND) is not None  # data tetap ada

    await dispatch(ctx, FRIEND, "/lanjut")
    assert FRIEND in [u.tg_chat_id for u in await ctx.active_users()]


# --- upgrade dari DB versi single-user ---


async def test_db_lama_tanpa_kolom_status_migrasi_dan_owner_tetap_aktif(tmp_path):
    """Pengguna lama tidak perlu hapus data/bot.db: kolom `status` ditambahkan,
    dan baris owner dari config langsung dianggap aktif."""
    import aiosqlite

    db_path = tmp_path / "old.db"
    async with aiosqlite.connect(db_path) as legacy:
        await legacy.executescript(
            """CREATE TABLE users (
                tg_chat_id TEXT PRIMARY KEY, participant_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '', bearer_token TEXT NOT NULL DEFAULT '',
                token_fingerprint TEXT NOT NULL DEFAULT '', token_saved_at TEXT NOT NULL DEFAULT '',
                token_ttl INTEGER NOT NULL DEFAULT 21600, token_invalid INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1, reminders_on INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL);
               INSERT INTO users (tg_chat_id, participant_id, created_at)
               VALUES ('111', 'pid-owner', '2026-01-01T00:00:00+07:00');"""
        )
        await legacy.commit()

    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "x",
            "TELEGRAM_CHAT_ID": OWNER,
            "PARTICIPANT_ID": "pid-owner",
            "DB_PATH": str(db_path),
        }
    )
    database = await dbmod.connect(settings.db_path)
    owner = await store.ensure_user(database, settings)
    await database.close()

    assert owner.status == STATUS_ACTIVE
    assert owner.is_active_user
