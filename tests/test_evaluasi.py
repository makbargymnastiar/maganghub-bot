"""Tes evaluasi bulanan: pembaca laporan portal, kartu hasil, motivasi, dan job pemantau."""

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


async def test_job_mengirim_kartu_sekali_per_periode(ctx):
    settings, database, user = ctx
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: REPORT})
    tg = FakeTelegram()

    sent = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent == 1
    assert "HASIL EVALUASI MAGANG KELUAR" in tg.messages[0][1]
    assert await store.flag_exists(database, user.tg_chat_id, "", f"eval:{PERIOD}:filled")

    sent_again = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent_again == 0
    assert len(tg.messages) == 1


async def test_job_ikut_memantau_bulan_sebelumnya(ctx):
    settings, database, user = ctx
    # Portal menjawab daftar laporan: hanya periode yang diminta yang dipakai, dan
    # bulan lalu yang sudah terisi tetap dikirim kartunya.
    payload = {"data": [OTHER_MONTH["data"][0], REPORT["data"][0]]}
    client = FakeMonevClient({endpoints.MONTHLY_REPORTS_PATH: payload})
    tg = FakeTelegram()

    sent = await run_evaluation_job(database, settings, user, client, tg, now=NOW)
    assert sent == 2
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
    assert not any(call[0] == endpoints.MONTHLY_REPORTS_PATH and
                   call[1]["start_date"] == "2026-11-01" for call in client.calls)
