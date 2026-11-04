"""Uji kalimat pengingat pengisian (messages/pengisian.py)."""

from bot.messages import pengisian


def test_pengingat_pagi_08_00():
    msg = pengisian.morning_message("2026-11-05")
    assert "🌅" in msg and "PENGINGAT PAGI ABSENSI" in msg
    assert "Kamis, 05 November 2026" in msg
    assert "Yuk catat logbook hari ini:" in msg
    assert "• Uraian Aktivitas" in msg and "• Kendala" in msg
    assert "Isi lebih awal biar malam santai! 👍" in msg
    assert "Bismillah, selamat beraktivitas! 💪" in msg


def test_soft_18_20():
    msg = pengisian.reminder_soft("2026-11-03")
    assert "🔔" in msg and "PENGINGAT ABSENSI" in msg
    assert "Selasa, 03 November 2026" in msg
    assert "jangan menunda sampai malam" in msg


def test_rawan_21_2329_tanpa_hitungan_sisa_waktu():
    msg = pengisian.reminder_rawan("2026-11-03")
    assert "⏰" in msg and "JAM RAWAN LUPA ABSENSI" in msg
    assert "masih KOSONG!" in msg
    # regression: user minta hitungan "tinggal X menit menuju 00:00" dihapus
    assert "tinggal" not in msg and "menuju tenggat 00:00" not in msg


def test_dekat_2330_2345():
    msg = pengisian.reminder_dekat("2026-11-03")
    assert "🚨" in msg and "TENGGAT ABSENSI SEMAKIN DEKAT" in msg
    assert "Isi SEKARANG!" in msg


def test_mepet_2346_2359():
    msg = pengisian.reminder_mepet("2026-11-03")
    assert "🆘" in msg and "TENGGAT ABSENSI CRITICAL!" in msg
    assert "Lewat pukul 00:00 WIB" in msg and "absensi hangus!" in msg


def test_sore_check_belum_diisi():
    msg = pengisian.sore_check("2026-11-03", complete=False, missing="kendala")
    assert "🕠" in msg and "CEK MONEV SORE" in msg
    assert "⚠️ Absensi Hari Ini Belum Diisi" in msg
    assert "(kendala masih kosong)." in msg
    assert "sebelum sibuk" in msg


def test_sore_check_sudah_diisi_sebagai_penanda():
    msg = pengisian.sore_check("2026-11-03", complete=True)
    assert "✅ Logbook Harian Lengkap" in msg
    assert "tinggal menunggu ACC mentor" in msg


def test_lewat_tenggat_sekali():
    msg = pengisian.missed_yesterday("2026-11-02", "2026-11-03")
    assert "❗" in msg and "ABSENSI TERLEWAT" in msg
    assert "Senin, 02 November 2026" in msg  # kemarin
    assert "Selasa, 03 November 2026" in msg  # hari ini
