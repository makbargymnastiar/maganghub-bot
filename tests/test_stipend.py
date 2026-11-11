"""Uji pemantauan pengajuan uang saku: parser klaim, kalkulasi, pesan, dan job."""

from datetime import datetime

import pytest

from bot import db as dbmod
from bot import store
from bot.jobs.stipend import run_stipend_job
from bot.messages import chat as chat_messages
from bot.messages.base import rupiah
from bot.messages.stipend import claim_alert_message, claim_detail_message, claim_pending_message
from bot.monev import endpoints, stipend
from bot.monev.client import MonevError
from bot.models import StipendClaim
from bot.settings import load_settings
from bot.telegram import commands

TZ = "+07:00"
PARTICIPANT = "p-123"
PERIOD = "2026-10"
NOW = datetime.fromisoformat(f"2026-11-10T08:00:00{TZ}")
UMP = 5_729_876


def claim(**overrides) -> StipendClaim:
    base = dict(period=PERIOD, total_working_days=22, payable_days=22)
    base.update(overrides)
    return StipendClaim(**base)


# --- format rupiah -------------------------------------------------------------


def test_rupiah_pemisah_titik():
    assert rupiah(UMP) == "Rp5.729.876"
    assert rupiah(0) == "Rp0"
    assert rupiah(5208978) == "Rp5.208.978"


# --- parser klaim --------------------------------------------------------------

CLAIM_ROW = {
    "id": "claim-1",
    "period_start": "2026-10-01",
    "period_end": "2026-10-31",
    "submitted_at": "2026-11-09T08:12:00+07:00",
    "amount": "5208978",
    "total_working_days": 22,
    "payable_days": 20,
    "present_days": 19,
    "paid_leave_days": 1,
    "calculation_snapshot": {"working_day_calendar": [{"date": "2026-10-01"}] * 22},
}


def test_parse_claims_membaca_baris_klaim():
    claims = stipend.parse_claims({"data": [CLAIM_ROW], "total": 1}, PERIOD)
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_id == "claim-1"
    assert c.total_working_days == 22 and c.payable_days == 20
    assert c.absent_days == 2
    assert c.amount == 5208978
    assert c.submitted_at.startswith("2026-11-09")


def test_parse_claims_membuang_periode_lain():
    other = dict(CLAIM_ROW, id="claim-2", period_start="2026-09-01")
    payload = {"data": [other, CLAIM_ROW]}
    claims = stipend.parse_claims(payload, PERIOD)
    assert [c.claim_id for c in claims] == ["claim-1"]


def test_parse_claims_toleran_uang_format_rupiah():
    row = {"id": "c9", "period_start": "2026-10-01", "amount": "Rp5.729.876"}
    claims = stipend.parse_claims([row], PERIOD)
    assert claims[0].amount == UMP


def test_parse_claims_kosong_berarti_belum_diajukan():
    assert stipend.parse_claims({"data": []}, PERIOD) == []
    assert stipend.parse_claims({"message": "tidak ada data"}, PERIOD) == []


def test_claim_from_attendances_izin_tidak_memotong():
    rows = [
        {"date": f"2026-10-{day:02d}", "status": "PRESENT"} for day in range(1, 21)
    ] + [
        {"date": "2026-10-21", "status": "ON_LEAVE"},   # izin sakit berbayar
        {"date": "2026-10-22", "status": "ABSENT"},     # mangkir
        {"date": "2026-10-23", "status": "DAYOFF"},     # bukan hari kerja
        {"date": "2026-11-05", "status": "ABSENT"},     # di luar periode
    ]
    c = stipend.claim_from_attendances(PERIOD, rows)
    assert c.total_working_days == 22
    assert c.payable_days == 21
    assert c.absent_days == 1


# --- kalkulasi -----------------------------------------------------------------


def test_estimate_sempurna_ump_utuh():
    est = stipend.estimate(claim())
    assert est.perfect is True
    assert est.diterima == UMP and est.potongan == 0 and est.percent == 100


def test_estimate_mangkir_dua_dari_dua_puluh_dua():
    est = stipend.estimate(claim(payable_days=20))
    assert est.absent_days == 2
    assert est.percent == 90  # 20/22 dibulatkan ke bawah, sama seperti portal
    assert est.potongan == 520_898  # round(5.729.876 * 2 / 22)
    assert est.diterima == 5_208_978


def test_estimate_amount_portal_menang_atas_rumus():
    est = stipend.estimate(claim(payable_days=20, amount=5_000_000))
    assert est.diterima == 5_000_000
    assert est.potongan == 729_876


def test_estimate_tanpa_data_hari_anggap_utuh():
    est = stipend.estimate(claim(total_working_days=0, payable_days=0, amount=None))
    assert est.perfect is True and est.diterima == UMP


# --- pesan ---------------------------------------------------------------------


def test_pesan_alert_tanpa_angka():
    text = claim_alert_message()
    assert "NOTIFIKASI UANG SAKU MAGANGHUB" in text
    assert "Mentor kamu baru saja mengajukan klaim uang saku bulanan" in text
    assert "Rp" not in text  # rincian menyusul di pesan berikutnya


def test_pesan_rincian_kehadiran_seratus():
    text = claim_detail_message(stipend.estimate(claim()))
    assert "RINCIAN PERHITUNGAN UANG SAKU" in text
    assert "📅 Periode: Oktober 2026" in text
    assert "📍 Wilayah Acuan: DKI Jakarta (UMP Rp5.729.876)" in text
    assert "• Persentase Kehadiran: 100% (Sempurna)" in text
    assert "• Pengurangan Uang Saku: Rp0" in text
    assert "Rp5.729.876 (Utuh)" in text
    assert "rekening terdaftar" in text


def test_pesan_rincian_ada_mangkir():
    text = claim_detail_message(stipend.estimate(claim(payable_days=20)))
    assert "• Persentase Kehadiran: 90% (20/22 Hari)" in text
    assert "• Ketidakhadiran (Mangkir): 2 Hari" in text
    assert "• Pengurangan Uang Saku: -Rp520.898" in text
    assert "Rp5.208.978" in text
    assert "Izin sakit dengan surat dokter dan libur resmi tidak memotong" in text
    assert "(Utuh)" not in text


def test_tag_html_seimbang():
    for text in (claim_alert_message(), claim_detail_message(stipend.estimate(claim()))):
        assert text.count("<b>") == text.count("</b>")


# --- job -----------------------------------------------------------------------


class FakeMonevClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        value = self.responses.get(path)
        if value is None:
            raise MonevError("tidak ada fixture", status=404)
        return value

    async def close(self):
        pass


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


@pytest.fixture
async def ctx(tmp_path):
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "x",
            "TELEGRAM_CHAT_ID": "111",
            "PARTICIPANT_ID": PARTICIPANT,
            "BEARER_TOKEN": "Bearer abc",
            "DB_PATH": str(tmp_path / "bot.db"),
        }
    )
    database = await dbmod.connect(settings.db_path)
    user = await store.ensure_user(database, settings, name="Tester")
    yield settings, database, user
    await database.close()


async def test_job_kirim_alert_lalu_rincian_satu_kali_per_periode(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({endpoints.PAYMENT_CLAIMS_PATH: {"data": [CLAIM_ROW], "total": 1}})
    tg = FakeTelegram()

    sent = await run_stipend_job(database, settings, user, client, tg, now=NOW)
    assert sent == 2
    assert "NOTIFIKASI UANG SAKU MAGANGHUB" in tg.messages[0][1]
    assert "RINCIAN PERHITUNGAN UANG SAKU" in tg.messages[1][1]
    # angka klaim portal terpakai: 20/22, potongan terlihat
    assert "90% (20/22 Hari)" in tg.messages[1][1]
    assert await store.flag_exists(database, user.tg_chat_id, "", "stipend:2026-10:claimed")

    assert await run_stipend_job(database, settings, user, client, tg, now=NOW) == 0
    assert len(tg.messages) == 2


async def test_job_diam_saat_claim_list_kosong(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({endpoints.PAYMENT_CLAIMS_PATH: {"data": [], "total": 0}})
    tg = FakeTelegram()

    assert await run_stipend_job(database, settings, user, client, tg, now=NOW) == 0
    assert tg.messages == []


async def test_job_tidak_crash_saat_endpoint_ditolak(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({})
    tg = FakeTelegram()

    assert await run_stipend_job(database, settings, user, client, tg, now=NOW) == 0
    assert tg.messages == []
    # siklus berikutnya tetap mencoba (tidak ada backoff untuk endpoint yang dikenal)
    await run_stipend_job(database, settings, user, client, tg, now=NOW)
    assert len(client.calls) > 1


async def test_job_fallback_hitung_hari_dari_attendances(ctx):
    settings, database, user = ctx
    # Klaim tanpa angka apa pun — hanya keberadaannya yang tahu portal mengisi.
    bare = {"id": "c1", "period_start": "2026-10-01", "submitted_at": "2026-11-09T08:00:00+07:00"}
    attendances = {
        "data": [
            {"date": f"2026-10-{day:02d}", "status": "PRESENT"} for day in range(1, 22)
        ] + [{"date": "2026-10-22", "status": "ABSENT"}]
    }
    client = FakeMonevClient({
        endpoints.PAYMENT_CLAIMS_PATH: {"data": [bare], "total": 1},
        endpoints.ATTENDANCES_PATH: attendances,
    })
    tg = FakeTelegram()

    sent = await run_stipend_job(database, settings, user, client, tg, now=NOW)
    assert sent == 2
    detail = tg.messages[1][1]
    assert "• Persentase Kehadiran: 95% (21/22 Hari)" in detail
    assert "• Ketidakhadiran (Mangkir): 1 Hari" in detail


async def test_job_cache_schedule_id_dari_profil(ctx):
    settings, database, user = ctx
    profile = {"data": {"id": "u1", "internship_batch_id": "batch-9"}}
    client = FakeMonevClient({
        endpoints.USER_PROFILE_PATH: profile,
        endpoints.PAYMENT_CLAIMS_PATH: {"data": [], "total": 0},
    })
    tg = FakeTelegram()

    await run_stipend_job(database, settings, user, client, tg, now=NOW)
    assert await store.get_setting(database, f"schedule_id:{PARTICIPANT}") == "batch-9"

    client.calls.clear()
    await run_stipend_job(database, settings, user, client, tg, now=NOW)
    # cache dipakai: profil tidak ditanya lagi, klaim dikirim dengan schedule_id
    assert not any(path == endpoints.USER_PROFILE_PATH for path, _ in client.calls)
    claim_call = next(p for p, params in client.calls if p == endpoints.PAYMENT_CLAIMS_PATH)
    params = dict(next(params for path, params in client.calls if path == claim_call))
    assert params["schedule_id"] == "batch-9"


# --- /klaim -------------------------------------------------------------------


def _bot_ctx(settings, database, client):
    bot_ctx = commands.BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)
    bot_ctx.client_for = lambda _user: client  # type: ignore[method-assign]
    return bot_ctx


def test_bantuan_menyebutkan_klaim():
    assert "/klaim [YYYY-MM]" in chat_messages.help_text("")


async def test_klaim_tanpa_argumen_ambil_periode_terbaru_yang_ada_klaim(ctx, monkeypatch):
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    client = FakeMonevClient({endpoints.PAYMENT_CLAIMS_PATH: {"data": [CLAIM_ROW], "total": 1}})

    reply = await commands.cmd_klaim(_bot_ctx(settings, database, client), user, "")
    assert "RINCIAN PERHITUNGAN UANG SAKU" in reply
    assert "Periode: Oktober 2026" in reply
    assert "Rp5.208.978" in reply  # amount portal menang atas rumus


async def test_klaim_belum_ada_klaim_pakai_respons_belum_diajukan(ctx, monkeypatch):
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    client = FakeMonevClient({endpoints.PAYMENT_CLAIMS_PATH: {"data": [], "total": 0}})

    reply = await commands.cmd_klaim(_bot_ctx(settings, database, client), user, "")
    assert "UANG SAKU BELUM DIAJUKAN" in reply
    # yang sedang ditunggu adalah klaim bulan lalu (jatuh tempo tanggal 9)
    assert "Periode: Oktober 2026" in reply
    assert "Belum diajukan mentor" in reply
    assert reply == claim_pending_message("2026-10")


async def test_klaim_periode_ekspisit_tidak_meminjam_bulan_lain(ctx, monkeypatch):
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    client = FakeMonevClient({endpoints.PAYMENT_CLAIMS_PATH: {"data": [CLAIM_ROW], "total": 1}})

    reply = await commands.cmd_klaim(_bot_ctx(settings, database, client), user, "2026-11")
    assert "UANG SAKU BELUM DIAJUKAN" in reply
    assert "Periode: November 2026" in reply


async def test_klaim_argumen_salah_dapat_petunjuk_format(ctx, monkeypatch):
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    client = FakeMonevClient({})

    reply = await commands.cmd_klaim(_bot_ctx(settings, database, client), user, "202611")
    assert "/klaim 2026-10" in reply
    assert client.calls == []
