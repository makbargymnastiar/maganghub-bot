"""Semua kalimat yang dikirim bot ke chat Telegram, dikelompokkan per topik.

Prinsip: folder ini adalah "mulut" (teks), `jobs/` + `telegram/` adalah "otak"
(kapan dan atas kondisi apa). Mau mengganti kalimat? Buka file sesuai topik:

- absensi.py    → notif perubahan status (ACC/revisi/ditolak) + pengumuman portal
- pengisian.py  → pengingat mengisi laporan (16:55, berjenjang 18:00→23:59, 00:01)
- harian.py      → terjadwal pagi 08:00: libur, evaluasi H-7, uang saku tanggal 9
- tagihan.py    → absensi menunggu ACC mentor ≥ 5 hari
- sesi.py       → token & cookie (panduan, tersimpan, kedaluwarsa)
- chat.py       → /help, tabel /status, /start, balasan perintah

Tidak ada logika bisnis di sini — hanya perakitan kalimat HTML Telegram.
"""
