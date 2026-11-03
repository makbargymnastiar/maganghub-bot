"""Uji kalimat pengingat pengisian (messages/pengisian.py)."""

from bot.messages import pengisian


def test_soft_18_20():
    msg = pengisian.reminder_soft("2026-11-03")
    assert "🔔" in msg and "Pengingat Absensi" in msg
    assert "Selasa, 03 November 2026" in msg
    assert "jangan menunggu malam" in msg


def test_rawan_21_2329_tanpa_hitungan_sisa_waktu():
    msg = pengisian.reminder_rawan("2026-11-03")
    assert "⏰" in msg and "Jam Rawan" in msg
    assert "BELUM terisi lengkap" in msg
    # regression: user minta hitungan "tinggal X menit menuju 00:00" dihapus
    assert "tinggal" not in msg and "menuju tenggat 00:00" not in msg


def test_dekat_2330_2345():
    msg = pengisian.reminder_dekat("2026-11-03")
    assert "🚨" in msg and "tenggat semakin dekat" in msg
    assert "Isi SEKARANG ya!" in msg


def test_mepet_2346_2359():
    msg = pengisian.reminder_mepet("2026-11-03")
    assert "🆘" in msg
    assert "lewat 00:00 tidak bisa diisi lagi!" in msg


def test_sore_check_belum_diisi():
    msg = pengisian.sore_check("2026-11-03", complete=False, missing="kendala")
    assert "🕠" in msg and "belum lengkap (kendala masih kosong)" in msg
    assert "sebelum sibuk" in msg


def test_sore_check_sudah_diisi_sebagai_penanda():
    msg = pengisian.sore_check("2026-11-03", complete=True)
    assert "sudah terisi lengkap" in msg
    assert "tinggal menunggu ACC mentor" in msg


def test_lewat_tenggat_sekali():
    msg = pengisian.missed_yesterday("2026-11-02", "2026-11-03")
    assert "❗" in msg and "Kemarin Terlewat" in msg
    assert "Senin, 02 November 2026" in msg  # kemarin
    assert "Selasa, 03 November 2026" in msg  # hari ini
