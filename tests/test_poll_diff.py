"""Uji deteksi perubahan status: 1 notifikasi per perubahan, idempoten setelah restart."""

from datetime import datetime

import pytest

from bot import db as dbmod
from bot import store
from bot.jobs.poll import months_for_window, run_poll
from bot.settings import load_settings

TZ = "+07:00"
TODAY = datetime.fromisoformat(f"2026-11-03T10:00:00{TZ}")


class FakeMonevClient:
    """Menggantikan MonevClient; `get` membalik payload sesuai path."""

    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        return self.payloads[path]

    async def close(self):
        pass


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)


def make_payloads(home_holiday=False, attendance=None, log=None):
    attendances = [] if attendance is None else [attendance]
    logs = [] if log is None else [log]
    return {
        "/api/v1/users/me/home": {
            "data": {
                "date": "2026-11-03",
                "active_internship": True,
                "is_holiday": home_holiday,
                "is_scheduled_off_day": False,
                "has_attendance": bool(attendances),
            }
        },
        "/api/v1/attendances": attendances,
        "/api/v1/daily-logs": {"data": logs, "page": 1, "page_size": 100, "total": len(logs)},
    }


@pytest.fixture
async def ctx(tmp_path):
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


async def test_run_pertama_seed_tanpa_notifikasi(ctx):
    settings, database, user = ctx
    client = FakeMonevClient(
        make_payloads(
            attendance={"date": "2026-11-03", "status": "PRESENT", "approval_status": "SUBMITTED"},
            log={"date": "2026-11-03", "state": "COMPLETED", "id": 1},
        )
    )
    tg = FakeTelegram()

    result = await run_poll(database, settings, user, client, now=TODAY, notify=tg)

    assert result.seeded is True
    assert tg.messages == []
    assert result.today_label.key == "PENDING_REVIEW"


async def test_perubahan_status_hanya_mengirim_satu_pesan(ctx):
    settings, database, user = ctx
    submitted = make_payloads(
        attendance={"date": "2026-11-03", "status": "PRESENT", "approval_status": "SUBMITTED"},
        log={"date": "2026-11-03", "state": "COMPLETED", "id": 1},
    )
    tg = FakeTelegram()
    await run_poll(database, settings, user, FakeMonevClient(submitted), now=TODAY, notify=tg)

    approved = make_payloads(
        attendance={"date": "2026-11-03", "status": "PRESENT", "approval_status": "APPROVED"},
        log={"date": "2026-11-03", "state": "APPROVED", "id": 1},
    )
    result = await run_poll(database, settings, user, FakeMonevClient(approved), now=TODAY, notify=tg)

    assert result.changes == [("2026-11-03", "APPROVED")]
    assert len(tg.messages) == 1
    assert "Hadir disetujui" in tg.messages[0]

    # siklus berikutnya tanpa perubahan -> tidak ada pesan baru
    await run_poll(database, settings, user, FakeMonevClient(approved), now=TODAY, notify=tg)
    assert len(tg.messages) == 1


async def test_restart_tidak_mengulang_notifikasi(ctx):
    """State dibaca dari DB, jadi proses yang di-restart tidak spam ulang."""
    settings, database, user = ctx
    payloads = make_payloads(
        attendance={"date": "2026-11-03", "status": "PRESENT", "approval_status": "APPROVED"},
        log={"date": "2026-11-03", "state": "APPROVED", "id": 1},
    )
    tg = FakeTelegram()
    await run_poll(database, settings, user, FakeMonevClient(payloads), now=TODAY, notify=tg)
    assert tg.messages == []  # seed run

    second = FakeTelegram()
    result = await run_poll(database, settings, user, FakeMonevClient(payloads), now=TODAY, notify=second)
    assert result.changed is False
    assert second.messages == []


async def test_hari_libur_tidak_ditagih_dan_tersimpan(ctx):
    settings, database, user = ctx
    client = FakeMonevClient(make_payloads(home_holiday=True))
    tg = FakeTelegram()
    result = await run_poll(database, settings, user, client, now=TODAY, notify=tg)
    assert result.today_label.key == "HOLIDAY"
    row = await store.get_day_state(database, user.tg_chat_id, "2026-11-03")
    assert row["label"] == "HOLIDAY"


async def test_query_daily_logs_memakai_awal_bulan(ctx):
    settings, database, user = ctx
    client = FakeMonevClient(make_payloads())
    await run_poll(database, settings, user, client, now=TODAY, notify=FakeTelegram())
    log_dates = {params["date"] for path, params in client.calls if path == "/api/v1/daily-logs"}
    # tanggal 3 dengan lookback 7 hari -> bulan berjalan + bulan sebelumnya
    assert log_dates == {"2026-11-01", "2026-10-01"}
    params = next(p for path, p in client.calls if path == "/api/v1/daily-logs")
    assert params["limit"] == 100
    assert params["participant_id"] == "pid-1"


def test_months_for_window_menutup_seluruh_jendela():
    """Jendela yang melewati lebih dari satu batas bulan tidak boleh bolong."""
    today = datetime(2026, 11, 3).date()
    assert months_for_window(today, 7) == ["2026-10-01", "2026-11-01"]
    # lookback 3 dari tgl 3 = 31 Okt masih masuk jendela -> bulan Okt ikut diminta
    assert months_for_window(today, 3) == ["2026-10-01", "2026-11-01"]
    assert months_for_window(today, 1) == ["2026-11-01"]
    assert months_for_window(today, 60) == ["2026-09-01", "2026-10-01", "2026-11-01"]
    # melewati batas tahun juga tidak boleh bolong
    assert months_for_window(datetime(2026, 1, 5).date(), 40) == [
        "2025-11-01",
        "2025-12-01",
        "2026-01-01",
    ]
