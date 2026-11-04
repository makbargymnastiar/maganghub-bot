"""Uji kalimat paket pagi: libur, tagihan ACC, evaluasi, uang saku."""

from bot.messages import harian, tagihan


def test_libur_akhir_pekan():
    msg = harian.day_off_message("2026-11-07")
    assert "🗓️" in msg and "HARI INI LIBUR" in msg
    assert "Sabtu, 07 November 2026 — tidak ada pengisian monev harian hari ini." in msg
    assert "Selamat beristirahat!" in msg and "Pengingat aktif lagi Senin pagi." in msg


def test_libur_dari_portal():
    msg = harian.day_off_message("2026-11-10", from_portal=True)
    assert "Selasa, 10 November 2026 — tidak ada pengisian monev harian hari ini." in msg
    # kalimat Senin pagi hanya untuk akhir pekan
    assert "Senin pagi" not in msg


def test_eval_hitung_mundur():
    msg = harian.eval_reminder_message(4)
    assert "📊" in msg and "EVALUASI MAGANG — H-4 (TANGGAL 9)" in msg
    assert "evaluasi magang" in msg


def test_uang_saku():
    msg = harian.stipend_message()
    assert "💸" in msg and "HARI-H PENGAJUAN UANG SAKU (TANGGAL 9)" in msg
    assert "Sudah pukul 08:00 WIB. Ajukan uang saku magangmu di portal SEKARANG" in msg


def test_tagihan_acc_daftar_tanggal():
    dates = ["2026-10-27", "2026-10-28", "2026-11-01"]
    msg = tagihan.pending_mentor_message(dates, 7)
    assert "⏳" in msg and "MENUNGGU ACC MENTOR (7 HARI)" in msg
    assert "📅 Daftar Tanggal:" in msg
    assert "27 Okt, 28 Okt, 01 Nov" in msg
    assert "(Pengingat otomatis berhenti begitu status di-ACC)." in msg
    # regression: kalimat sapaan mentor memang dihapus user
    assert "Pak/Bu" not in msg
