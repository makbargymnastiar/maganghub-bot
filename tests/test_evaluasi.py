"""Tes evaluasi bulanan: pembaca laporan portal, template pesan, motivasi, job, /evaluasi."""

from datetime import datetime

import pytest

from bot import db as dbmod
from bot import store
from bot.jobs.evaluations import run_evaluation_job
from bot.monev import endpoints, evaluation
from bot.monev.client import MonevError
from bot.messages import evaluasi
from bot.models import EvalItem, EvaluationResult
from bot.settings import load_settings
from bot.telegram import commands
from tests.test_poll_diff import FakeMonevClient as PollFake, make_payloads

TZ = "+07:00"
NOW = datetime.fromisoformat(f"2026-11-05T10:00:00{TZ}")
PERIOD = "2026-11"
PREV = "2026-10"
PARTICIPANT = "pid-1"

# Laporan bulan ini: kedelapan aspek terisi, campuran enum (`EXCELLENT`), predikat
# panjang ("Sangat Baik"), singkatan ("SB"), dan angka peringkat (`1`, `3`).
# `adaptability` sengaja lewat alias `*_score` — frontend membaca keduanya.
REPORT = {
    "data": [
        {
            "id": "rpt-112",
            "participant_id": PARTICIPANT,
            "period": PERIOD,
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
            "status": "SUBMITTED",
            "mentor_name": "Bu Ratna",
            "submitted_at": "2026-12-09T08:12:00" + TZ,
            "mentor_feedback": "Pengetahuan teknis perlu ditingkatkan lagi.",
            "attendance_discipline_grade": "EXCELLENT",
            "attitude_grade": "Sangat Baik",
            "communication_grade": "GOOD",
            "initiative_responsibility_grade": "SUFFICIENT",
            "adaptability_score": 1,
            "technical_knowledge_grade": 3,
            "productivity_grade": "POOR",
            "teamwork_grade": "SB",
            "curriculum_achievement_grade": "GOOD",
            "curriculum_assessments": [
                {"name": "Frontend Development", "grade": "GOOD"},
                {"name": "Version Control", "grade": "EXCELLENT"},
            ],
        }
    ],
    "pagination": {"page": 1, "limit": 100, "total": 1},
}

# Form laporan yang belum disentuh mentor: kedelapan key ADA tapi null (default
# frontend), status masih draft. Ini bukan "sudah diisi".
EMPTY_REPORT = {
    "data": [
        {
            "participant_id": PARTICIPANT,
            "period": PERIOD,
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
            "status": "DRAFT",
            "mentor_feedback": "",
            "attendance_discipline_grade": None,
            "attitude_grade": None,
            "communication_grade": None,
            "initiative_responsibility_grade": None,
            "adaptability_grade": None,
            "technical_knowledge_grade": None,
            "productivity_grade": None,
            "teamwork_grade": None,
            "curriculum_achievement_grade": None,
        }
    ]
}

OTHER_MONTH = {
    "data": [
        {
            "period": PREV,
            "start_date": "2026-10-01",
            "status": "SUBMITTED",
            "mentor_name": "Bu Ratna",
            "mentor_feedback": "Catatan bulan Oktober",
            "attendance_discipline_grade": "EXCELLENT",
        }
    ]
}

CURRICULUM = {
    "data": {
        "period": {"start_date": "2026-11-01"},
        "curriculum": {
            "id": "cur-9",
            "materials": [
                {"id": "m-1", "name": "Mentoring & pairing"},
                {"id": "m-2", "name": "Code review"},
            ],
        },
    }
}


def test_period_window_mencakup_terakhir_hari_bulan():
    assert evaluation.period_window("2026-11") == ("2026-11-01", "2026-11-30")
    assert evaluation.period_window("2026-02") == ("2026-02-01", "2026-02-28")
    assert evaluation.period_window("2028-02") == ("2028-02-01", "2028-02-29")  # kabisat
    assert evaluation.period_window("2026-13") == ("", "")
    assert evaluation.period_window("bukan-bulan") == ("", "")


def test_grade_rank_of_menerima_enum_predikat_singkatan_dan_peringkat():
    assert evaluation.grade_rank_of("EXCELLENT") == 5
    assert evaluation.grade_rank_of("GOOD") == 4
    assert evaluation.grade_rank_of("SUFFICIENT") == 3
    assert evaluation.grade_rank_of("POOR") == 2
    assert evaluation.grade_rank_of("Sangat Baik") == 5
    assert evaluation.grade_rank_of("Baik") == 4
    assert evaluation.grade_rank_of("Cukup") == 3
    assert evaluation.grade_rank_of("Kurang") == 2
    assert evaluation.grade_rank_of("SB") == 5
    assert evaluation.grade_rank_of("K") == 2
    assert evaluation.grade_rank_of("Sangat Baik (SB)") == 5


def test_angka_adalah_peringkat_bukan_jumlah_bintang():
    # Regresi penting: 1 = terbaik, 4 = terburuk. Kalau dibaca sebagai bintang,
    # seluruh nilai peserta terbalik.
    assert evaluation.grade_rank_of(1) == 5
    assert evaluation.grade_rank_of(2) == 4
    assert evaluation.grade_rank_of(3) == 3
    assert evaluation.grade_rank_of(4) == 2
    assert evaluation.grade_rank_of("1") == 5
    assert evaluation.grade_rank_of("4") == 2


def test_grade_rank_of_tidak_mengarang_nilai():
    assert evaluation.grade_rank_of("Perlu Pembinaan") == 0
    assert evaluation.grade_rank_of(None) == 0
    assert evaluation.grade_rank_of(True) == 0
    assert evaluation.grade_rank_of("") == 0
    assert evaluation.grade_rank_of(7) == 0  # di luar peringkat 1..4
    assert evaluation.grade_rank_of(0) == 0
    assert evaluation.predikat_of("Perlu Pembinaan") == "Perlu Pembinaan"
    assert evaluation.predikat_of("EXCELLENT") == "Sangat Baik (SB)"


def test_parse_memetakan_delapan_aspek_dengan_urutan_resmi():
    result = evaluation.parse_evaluation(REPORT, PERIOD)
    assert [item.name for item in result.items] == [
        "Kehadiran dan disiplin",
        "Sikap dan perilaku",
        "Kemampuan komunikasi",
        "Inisiatif dan tanggung jawab",
        "Kemampuan adaptasi",
        "Pengetahuan teknis",
        "Produktivitas dan ketepatan waktu",
        "Kerja sama tim",
    ]
    assert [item.stars for item in result.items] == [5, 5, 4, 3, 5, 3, 2, 5]
    assert result.reviewer == "Bu Ratna"
    assert result.comment.startswith("Pengetahuan teknis")
    assert result.is_filled is True


def test_alias_score_diterima_sama_seperti_grade():
    payload = {"data": [{"period": PERIOD, "attitude_score": "EXCELLENT"}]}
    result = evaluation.parse_evaluation(payload, PERIOD)
    assert [item.stars for item in result.items] == [5]
    assert result.items[0].name == "Sikap dan perilaku"


def test_capaian_kurikulum_per_materi():
    result = evaluation.parse_evaluation(REPORT, PERIOD)
    assert [(item.name, item.stars) for item in result.curriculum] == [
        ("Frontend Development", 4),
        ("Version Control", 5),
    ]


def test_capaian_kurikulum_agregat_dan_daftar_materi_tidak_dikarang():
    # Laporan cuma punya satu grade agregat: materi ditampilkan tanpa bintang,
    # bukan ditempeli nilai agregat (itu baru mengarang).
    payload = {"data": [{"period": PERIOD, "curriculum_achievement_grade": "GOOD"}]}
    result = evaluation.parse_evaluation(payload, PERIOD, CURRICULUM)
    assert result.curriculum[0].name == "Capaian kurikulum"
    assert result.curriculum[0].stars == 4
    assert [(item.name, item.stars) for item in result.curriculum[1:]] == [
        ("Mentoring & pairing", 0),
        ("Code review", 0),
    ]
    assert evaluation.parse_evaluation(payload, PERIOD).curriculum[0].stars == 4


def test_laporan_bulan_lain_diam_saja():
    result = evaluation.parse_evaluation(OTHER_MONTH, PERIOD)
    assert result.items == []
    assert result.has_content is False
    assert result.is_filled is False
    # Bulan yang sama tetap terbaca normal.
    assert evaluation.parse_evaluation(OTHER_MONTH, PREV).is_filled is True


def test_daftar_laporan_diambil_yang_cocok_dengan_periode():
    payload = {"data": [OTHER_MONTH["data"][0], REPORT["data"][0]]}
    result = evaluation.parse_evaluation(payload, PERIOD)
    assert len(result.items) == 8
    assert result.comment.startswith("Pengetahuan teknis")
    assert evaluation.parse_evaluation(payload, PREV).comment.startswith("Catatan bulan Oktober")
    # Daftar laporan orang lain / bulan lain -> tidak ada yang boleh dipinjam.
    assert evaluation.parse_evaluation({"data": [OTHER_MONTH["data"][0]]}, PERIOD).items == []


@pytest.mark.parametrize(
    "nilai",
    ["2026-11", "2026-11-01", "2026/11", "202611", "11/2026", "November 2026", "november-2026"],
)
def test_format_bulan_laporan_yang_diterima(nilai):
    # Nama field-nya sudah diketahui, format nilainya belum tentu. Semua bentuk yang
    # masuk akal diterima, dan bulan lain tetap tidak dipinjam.
    payload = {"data": [{"period": nilai, "attitude_grade": "EXCELLENT"}]}
    assert evaluation.parse_evaluation(payload, PERIOD).is_filled is True
    assert evaluation.parse_evaluation(payload, PREV).is_filled is False


def test_bulan_dari_field_month_dan_year_terpisah():
    payload = {"data": [{"month": 11, "year": 2026, "attitude_grade": "EXCELLENT"}]}
    assert evaluation.parse_evaluation(payload, PERIOD).is_filled is True
    assert evaluation.parse_evaluation(payload, PREV).is_filled is False


def test_banyak_laporan_tanpa_bulan_tidak_ada_yang_dipinjam():
    # Job menanyakan dua periode sekaligus. Kalau tidak satu pun laporan menyebut
    # bulannya, kartu yang sama bisa terkirim dua kali untuk bulan yang berbeda.
    payload = {"data": [{"attitude_grade": "EXCELLENT"}, {"attitude_grade": "GOOD"}]}
    assert evaluation.parse_evaluation(payload, PERIOD).has_content is False
    # Satu laporan saja: memang cuma itu yang ada, jadi wajar dipakai.
    assert evaluation.parse_evaluation({"data": [{"attitude_grade": "EXCELLENT"}]}, PERIOD).is_filled is True


def test_declared_periods_hanya_untuk_diagnosis():
    # Alat probe memakainya untuk menunjukkan bulan apa yang sebenarnya dikirim portal.
    payload = {"data": [OTHER_MONTH["data"][0], REPORT["data"][0]]}
    assert evaluation.declared_periods(payload) == [PREV, PERIOD]
    assert evaluation.declared_periods({"data": [{"attitude_grade": "GOOD"}]}) == [""]
    assert evaluation.declared_periods({"data": []}) == []


def test_laporan_terbungkus_satu_level_ikut_dibaca():
    # Varian `participants-period`: laporan menempel di bawah objek induk, bukan
    # langsung di `data`.
    payload = {"data": [{"participant": {"id": PARTICIPANT}, "report": REPORT["data"][0]}]}
    result = evaluation.parse_evaluation(payload, PERIOD)
    assert len(result.items) == 8
    assert result.is_filled is True


def test_form_kosong_bukan_isi():
    result = evaluation.parse_evaluation(EMPTY_REPORT, PERIOD)
    assert result.items == []
    assert result.curriculum == []
    assert result.has_content is False
    assert result.is_filled is False


def test_status_draft_menahan_kartu_walau_nilai_sudah_ada():
    payload = {"data": [{"period": PERIOD, "status": "draft", "attitude_grade": "EXCELLENT"}]}
    result = evaluation.parse_evaluation(payload, PERIOD)
    assert result.has_content is True
    assert result.filled is False
    assert result.is_filled is False


def test_tanggal_submit_bulan_berikutnya_bukan_penanda_bulan_lain():
    # Laporan November ditulis mentor tanggal 9 Desember: `submitted_at` Desember,
    # tapi kartunya tetap milik November.
    result = evaluation.parse_evaluation(REPORT, PERIOD)
    assert result.filled is True
    assert len(result.items) == 8
    assert result.updated_at.startswith("2026-12")


def test_nilai_asing_tampil_apa_adanya():
    payload = {"data": [{"period": PERIOD, "attitude_grade": "Perlu Pembinaan"}]}
    result = evaluation.parse_evaluation(payload, PERIOD)
    assert result.items[0].stars == 0
    assert result.items[0].predikat == "Perlu Pembinaan"
    assert "• Sikap dan perilaku\n  — Perlu Pembinaan" in evaluasi.eval_result_message(result)


def test_response_base64ikut_dibaca():
    import base64
    import json

    encoded = base64.b64encode(json.dumps(REPORT).encode("utf-8")).decode("ascii")
    assert evaluation.parse_evaluation(encoded, PERIOD).is_filled is True


def test_periods_to_watch_mencakup_bulan_berjalan_dan_sebelumnya():
    assert evaluation.periods_to_watch("2026-11-05") == ["2026-11", "2026-10"]
    assert evaluation.periods_to_watch("2026-01-15") == ["2026-01", "2025-12"]


def test_kartu_hasil_memuat_semua_bagian():
    result = evaluation.parse_evaluation(REPORT, PERIOD)
    text = evaluasi.eval_result_message(result)
    assert "🎉 <b>HASIL EVALUASI MAGANG KELUAR!</b>" in text
    assert "Status: ✅ Evaluasi Diisi Mentor" in text
    assert "November 2026" in text
    assert "⭐⭐⭐⭐⭐ Sangat Baik (SB)" in text
    assert "⭐⭐ Kurang (K)" in text
    assert "🎓 <b>Capaian Kurikulum:</b>" in text
    assert "💬 <b>Komentar Mentor:</b>" in text
    assert "Bu Ratna" in text
    assert text.count("<b>") == text.count("</b>")


def test_kartu_melepas_html_dengan_benar():
    payload = {"data": [{"period": PERIOD, "mentor_feedback": "Kerja <bagus> & rapi"}]}
    text = evaluasi.eval_result_message(evaluation.parse_evaluation(payload, PERIOD))
    assert "Kerja &lt;bagus&gt; &amp; rapi" in text


def test_kartu_tanpa_kurikulum_dan_komentar_lewati_bagian():
    result = EvaluationResult(period=PERIOD, items=evaluation.parse_evaluation(REPORT, PERIOD).items)
    text = evaluasi.eval_result_message(result)
    assert "🎓" not in text
    assert "💬" not in text


def test_motivasi_dipilih_dari_distribusi_nilai():
    top = EvaluationResult(period=PERIOD, items=[EvalItem(name="Kehadiran dan disiplin", stars=5)])
    assert evaluasi.motivation_sentence(top) in evaluasi._MOTIVATION_ALL_TOP

    growth = evaluation.parse_evaluation(REPORT, PERIOD)
    sentence = evaluasi.motivation_sentence(growth)
    # Ada poin <=3 dan ada masukan mentor -> kasus pertumbuhan, bukan pujian sempurna.
    assert any(sentence.endswith(variant) for variant in evaluasi._MOTIVATION_GROWTH)
    # Tetap menyebut poin yang sudah bagus sebagai fondasi, dan tidak menghakimi.
    assert "Kehadiran dan disiplin" in sentence
    for kata_vonis in ("gagal", "buruk", "jelek", "kurang ajar"):
        assert kata_vonis not in sentence.lower()

    solid = EvaluationResult(period=PERIOD, items=[EvalItem(name="Kehadiran dan disiplin", stars=4)])
    assert evaluasi.motivation_sentence(solid) in evaluasi._MOTIVATION_SOLID


def test_motivasi_stabil_per_periode():
    result = evaluation.parse_evaluation(REPORT, PERIOD)
    assert evaluasi.motivation_sentence(result) == evaluasi.motivation_sentence(result)


def test_respons_a_status_belum_diisi():
    text = evaluasi.eval_pending_message(PERIOD)
    assert text == (
        "⏳ <b>EVALUASI MENTOR BELUM DIISI</b>\n\n"
        "📅 Periode: November 2026\n"
        "📌 Status: ⚠️ Belum ada nilai di portal"
    )


def test_pesan_alert_mendahului_kartu():
    text = evaluasi.eval_alert_message()
    assert text == (
        "🔔 <b>NOTIFIKASI EVALUASI MAGANG</b>\n\n"
        "✨ Mentor kamu baru saja selesai mengisi evaluasi bulanan di portal monev!\n\n"
        "Sedang memuat rincian nilai... ⏳"
    )
    # Alert tidak boleh mengandung angka: rinciannya ada di pesan berikutnya.
    assert "⭐" not in text


class CekClient(PollFake):
    """Fake polling yang membalik MonevError (bukan KeyError) untuk path asing."""

    async def get(self, path, params=None):
        if path not in self.payloads:
            raise MonevError(f"tidak ada fixture untuk {path}", 404)
        return await super().get(path, params)


class FakeMonevClient:
    """Balik payload per path; path yang tidak dikenal melempar MonevError."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        value = self.responses.get(path)
        if value is None:
            raise MonevError("404 not found", status=404)
        if isinstance(value, Exception):
            raise value
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


async def test_fetch_evaluation_memakai_jendela_tanggal_asli(ctx):
    _, _, user = ctx
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: REPORT,
                             endpoints.PERIOD_CURRICULUM_PATH_FMT.format(
                                 participant_id=PARTICIPANT, start_date="2026-11-01"): CURRICULUM})
    result = await endpoints.fetch_evaluation(client, user.participant_id, PERIOD)

    assert result.is_filled is True
    path, params = client.calls[0]
    assert path == endpoints.MONTHLY_REPORTS_PATH
    assert params["participant_id"] == PARTICIPANT
    assert params["start_date"] == "2026-11-01"
    assert params["end_date"] == "2026-11-30"
    # Materi kurikulum ikut ditanya ke endpoint periode.
    assert any("curriculum" in call[0] for call in client.calls)


async def test_fetch_evaluation_tetap_jalan_kalau_kurikulum_gagal(ctx):
    _, _, user = ctx
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: REPORT})
    result = await endpoints.fetch_evaluation(client, user.participant_id, PERIOD)
    assert [item.name for item in result.curriculum] == ["Frontend Development", "Version Control"]


async def test_fetch_evaluation_meneruskan_error_token(ctx):
    _, _, user = ctx
    client = FakeMonevClient({})
    with pytest.raises(MonevError):
        await endpoints.fetch_evaluation(client, user.participant_id, PERIOD)


async def test_job_mengirim_alert_lalu_kartu_cukup_satu_kali_per_periode(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: REPORT})
    tg = FakeTelegram()

    sent = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    # Dua pesan sekali jalan: alert (pesan 1) + rincian (pesan 2).
    assert sent == 2
    assert "NOTIFIKASI EVALUASI MAGANG" in tg.messages[0][1]
    assert "HASIL EVALUASI MAGANG KELUAR" in tg.messages[1][1]
    assert "⭐" not in tg.messages[0][1]
    assert await store.flag_exists(database, user.tg_chat_id, "", f"eval:{PERIOD}:filled")

    sent_again = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent_again == 0
    assert len(tg.messages) == 2


async def test_job_ikut_memantau_bulan_sebelumnya(ctx):
    settings, database, user = ctx
    # Portal menjawab daftar laporan: hanya periode yang diminta yang dipakai, dan
    # bulan lalu yang sudah terisi tetap dikirim kartunya.
    payload = {"data": [OTHER_MONTH["data"][0], REPORT["data"][0]]}
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: payload})
    tg = FakeTelegram()

    sent = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent == 4  # alert + kartu untuk masing-masing dari dua periode
    assert await store.flag_exists(database, user.tg_chat_id, "", f"eval:{PREV}:filled")


async def test_job_diam_saat_mentor_belum_isi(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: EMPTY_REPORT})
    tg = FakeTelegram()

    sent = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent == 0
    assert tg.messages == []


async def test_job_tidak_crash_saat_endpoint_ditolak(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({})
    tg = FakeTelegram()

    sent = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent == 0
    assert tg.messages == []
    # Tidak ada backoff lagi: siklus berikutnya tetap mencoba (endpointnya sudah dikenal).
    await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert len(client.calls) > 1


async def test_job_tidak_mengulang_periode_yang_sudah_terkirim(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: REPORT})
    tg = FakeTelegram()
    await store.set_flag(database, user.tg_chat_id, "", f"eval:{PERIOD}:filled", NOW)

    sent = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent == 0
    assert tg.messages == []
    # Periode yang sudah ada flag-nya tidak ditanya lagi, dengan params apa pun.
    assert not any(params and params.get("start_date") == "2026-11-01" for _, params in client.calls)


# --- /cek cukup status harian; evaluasi punya jalannya sendiri ---


def _bot_ctx(settings, database, client):
    ctx = commands.BotContext(db=database, settings=settings, tg=FakeTelegram(), poll_minutes=15)
    ctx.client_for = lambda _user: client  # type: ignore[method-assign]
    return ctx


async def test_cek_hanya_status_harian_tanpa_blok_evaluasi(ctx, monkeypatch):
    """Template /cek yang disetujui isinya absensi; evaluasi ranah /evaluasi."""
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    payloads = make_payloads()
    payloads[endpoints.MONTHLY_REPORTS_PATH] = EMPTY_REPORT
    client = CekClient(payloads)
    bot_ctx = _bot_ctx(settings, database, client)

    reply = await commands.cmd_cek(bot_ctx, user)
    assert "Hari ini" in reply
    assert "EVALUASI" not in reply.upper()
    # Bukan cuma teksnya yang hilang: endpoint laporan bulanan tidak ikut ditanya.
    assert not any(endpoints.MONTHLY_REPORTS_PATH in call[0] for call in client.calls)


class ScriptedClient:
    """Balik payload per (path, berjendela atau tidak) — untuk menguji urutan query."""

    def __init__(self, responses: dict[tuple[str, str], object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        key = (path, "window" if (params or {}).get("start_date") else "all")
        value = self.responses.get(key)
        if value is None:
            raise MonevError(f"tidak ada fixture untuk {key}", status=404)
        return value

    async def close(self):
        pass


async def test_fetch_menemuk_laporan_yang_tersimpan_di_luar_jendela_tanggal(ctx):
    _, _, user = ctx
    # Percobaan pertama (jendela 1-31 Oktober) jawab kosong — portal menyimpan
    # laporan di tanggal luar bulan kalender periodenya. Daftar seluruh laporan
    # masih punya jawabannya.
    client = ScriptedClient({
        (endpoints.MONTHLY_REPORTS_PATH, "window"): {"data": []},
        (endpoints.MONTHLY_REPORTS_PATH, "all"): {"data": [OTHER_MONTH["data"][0]]},
    })
    result = await endpoints.fetch_evaluation(client, user.participant_id, PREV)

    assert result.is_filled is True
    assert result.comment.startswith("Catatan bulan Oktober")
    assert client.calls[0][1]["start_date"] == "2026-10-01"  # yang sempit dicoba duluan
    assert len(client.calls) > 1                              # fallback baru jalan setelahnya


async def test_endpoint_terbaru_hanya_ditanya_kalau_daftar_tidak_menjawab(ctx):
    _, _, user = ctx
    # Kedua query /monthly-reports ditolak (path-nya beda dari dugaan portal), jadi
    # `/monthly-reports/latest` yang jadi penolong.
    client = ScriptedClient({(endpoints.MONTHLY_REPORTS_LATEST_PATH, "all"): REPORT})
    result = await endpoints.fetch_evaluation(client, user.participant_id, PERIOD)
    assert result.is_filled is True
    assert len(result.items) == 8
    assert any(path == endpoints.MONTHLY_REPORTS_LATEST_PATH for path, _ in client.calls)


async def test_daftar_laporan_yang_menjawab_tidak_ditambah_lagi(ctx):
    _, _, user = ctx
    # Jawaban "semua laporan" sudah superhimpunan dari "latest": kalau yang pertama
    # menjawab, tidak ada gunanya bertanya tiga kali tiap 30 menit.
    client = ScriptedClient({
        (endpoints.MONTHLY_REPORTS_PATH, "window"): {"data": []},
        (endpoints.MONTHLY_REPORTS_PATH, "all"): {"data": []},
    })
    result = await endpoints.fetch_evaluation(client, user.participant_id, PERIOD)
    assert result.is_filled is False
    assert not any(path == endpoints.MONTHLY_REPORTS_LATEST_PATH for path, _ in client.calls)
    assert len(client.calls) == 2


async def test_fetch_naikkan_error_saat_semua_sumber_menolak(ctx):
    _, _, user = ctx
    client = ScriptedClient({})
    with pytest.raises(MonevError):
        await endpoints.fetch_evaluation(client, user.participant_id, PERIOD)


async def test_evaluasi_tanpa_argumen_menampilkan_bulan_yang_sudah_dinilai(ctx, monkeypatch):
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    # November masih kosong, Oktober sudah dinilai mentor awal bulan ini.
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: {"data": [OTHER_MONTH["data"][0]]}})
    bot_ctx = _bot_ctx(settings, database, client)

    reply = await commands.cmd_evaluasi(bot_ctx, user)
    assert "HASIL EVALUASI MAGANG KELUAR" in reply
    assert "Oktober 2026" in reply      # kartu jujur menyebut bulan laporannya sendiri
    assert "November 2026" not in reply


async def test_evaluasi_tanpa_argumen_respons_a_saat_semua_periode_kosong(ctx, monkeypatch):
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: EMPTY_REPORT})
    bot_ctx = _bot_ctx(settings, database, client)

    reply = await commands.cmd_evaluasi(bot_ctx, user)
    assert reply == evaluasi.eval_pending_message(PERIOD)
    assert "📅 Periode: November 2026" in reply


async def test_evaluasi_eksplisit_hanya_bulan_yang_diminta(ctx, monkeypatch):
    settings, database, user = ctx
    monkeypatch.setattr(type(settings), "now", lambda self: NOW)
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: {"data": [OTHER_MONTH["data"][0]]}})
    bot_ctx = _bot_ctx(settings, database, client)

    # Diminta bulan yang belum ada: tetap Respons A, tidak meminjam bulan lain.
    assert await commands.cmd_evaluasi(bot_ctx, user, "2026-11") == evaluasi.eval_pending_message(PERIOD)
    # Diminta bulan yang sudah ada: kartunya keluar.
    assert "Oktober 2026" in await commands.cmd_evaluasi(bot_ctx, user, "2026-10")
    # Argumen yang bukan bulan ditolak sebelum ada permintaan ke portal.
    assert "Format:" in await commands.cmd_evaluasi(bot_ctx, user, "kemarin")
