"""Uji kalimat paket pagi: libur, tagihan ACC, evaluasi, uang saku."""

from bot.messages import harian, tagihan


def test_libur_akhir_pekan():
    msg = harian.day_off_message("2026-11-07")
    assert "🗓️" in msg and "Hari Ini Libur" in msg
    assert "Sabtu, 07 November 2026" in msg
    assert "akhir pekan" in msg


def test_libur_dari_portal():
    msg = harian.day_off_message("2026-11-10", from_portal=True)
    assert "portal menandai hari ini sebagai libur" in msg
    assert "akhir pekan" not in msg


def test_eval_hitung_mundur():
    msg = harian.eval_reminder_message(4)
    assert "📊" in msg and "H-4 sebelum tanggal 9" in msg
    assert "evaluasi magang" in msg


def test_uang_saku():
    msg = harian.stipend_message()
    assert "💸" in msg and "Pengajuan Uang Saku" in msg
    assert "tanggal 9" in msg


def test_tagihan_acc_daftar_tanggal():
    dates = ["2026-10-27", "2026-10-28", "2026-11-01"]
    msg = tagihan.pending_mentor_message(dates, 7)
    assert "⏳" in msg and "sudah 7 hari" in msg
    assert "27 Okt, 28 Okt, 01 Nov" in msg
    # regression: kalimat sapaan mentor memang dihapus user
    assert "Pak/Bu" not in msg
