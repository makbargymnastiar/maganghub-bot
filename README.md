# Bot Pengingat Absensi Maganghub (monev)

Bot Telegram pribadi yang memantau portal monev Maganghub atas nama Anda dan
mengirim pesan ketika:

- status absensi/uraian harian berubah (mis. di-ACC mentor, diminta revisi, ditolak),
- laporan hari ini belum diisi menjelang malam,
- token sesi monev mendekati kedaluwarsa atau mulai ditolak server,
- ada pengumuman/warning dari portal.

Bot **hanya membaca** API monev — ia tidak pernah mengisi atau mengirim laporan.

---

## Struktur

```
run.py                entrypoint (scheduler + long polling Telegram)
main.py               shim kompatibilitas -> run.py
bot/
  settings.py         baca config.py (rahasia), normalisasi, masking token
  db.py               skema SQLite (users, day_state, notify_flags, event_log, settings)
  store.py            seluruh query; satu-satunya modul yang menyentuh SQL
  models.py           User, DayRecord
  status.py           PETA STATUS — port fungsi i(e, t, r) dari frontend monev
  api/
    client.py         httpx + retry + pemisahan TokenRejected (401/403) vs error lain
    endpoints.py      home, attendances, daily-logs, absence-warnings, announcements, system/time, users/me
    b64.py            dekode response base64 (announcements)
    jwt_utils.py      baca klaim ttl/fingerprint (token monev tidak punya `exp`)
  jobs/
    poll.py           gabung 3 endpoint, deteksi perubahan, kirim notifikasi
    reminders.py      pengingat 18:00 / 21:00 / 23:00 + tindak lanjut revisi
    token_health.py   peringatan H-30 menit, deteksi 401, pemulihan otomatis
    announcements.py  pengumuman + warning keterlambatan dari portal
  tg/
    api.py            wrapper Bot API (getUpdates/sendMessage), tanpa library eksternal
    commands.py       dispatch perintah + BotContext
    format.py         perender pesan HTML (semua data API di-escape)
legacy/main.py        versi lama (requests + schedule + state.json), disimpan apa adanya
scripts/probe_api.py  alat live: panggil API, cetak status hasil pemetaan
deploy/               systemd unit + installer Ubuntu
tools/bookmarklet.md  panduan langkah-demi-langkah mengambil & mengisi token
```

## Cara kerja status

Yang dikejar bot ini adalah **tiga isian harian di portal**: `activity_log`
(aktivitas), `lesson_learned` (pembelajaran), `obstacles` (kendala) — nama kolom
terkonfirmasi dari response `/api/v1/daily-logs`. Selama ketiganya belum lengkap,
status masih ditagih reminder; begitu lengkap, bot mengirim "Sudah Diisi".

Frontend monev menggabungkan **dua** objek untuk melabeli satu tanggal:
`attendance` (`status`, `approval_status`) dan `daily-log` (`state`). Versi lama
hanya membaca daily-log, sehingga label yang bergantung pada kehadiran tidak
pernah aktif. `bot/status.py` menata ulang urutan pengecekan seperti frontend:
`DAYOFF` → `ABSENT` → `NEEDS_REVISION` → `REJECTED` → `REVISION_SUBMITTED` →
`COMPLETED` → kelengkapan isian → `APPROVED` → `SUBMITTED` → fallback.

Satu **deviasi sadar** dari frontend: frontend sudah menulis "Hadir disetujui"
begitu `attendance.approval_status == APPROVED`, padahal uraian harian boleh jadi
masih kosong. Untuk bot pengingat, ACC kehadiran tidak dianggap menyelesaikan
pekerjaan hari itu — jadi isian yang belum lengkap tetap ditandai
🔵/🟡/🟣 (hari izin `ON_LEAVE` dikecualikan, karena tidak ada uraian yang ditagih).

| Label | Emoji | Arti | Ditagih reminder? |
| --- | --- | --- | --- |
| Belum Diisi | ⚪ | belum ada absensi & laporan | ya |
| Sudah Absen, Uraian Belum Diisi | 🔵 | kehadiran tercatat, isi masih kosong | ya |
| Sebagian Terisi | 🟡 | 1–2 dari 3 kolom terisi (yang kosong disebutkan) | ya |
| Sudah Diisi, Belum Dikirim | 🟣 | tiga kolom lengkap, tombol kirim belum ditekan | ya |
| Menunggu Persetujuan | 📝 | terkirim, tunggu ACC mentor | tidak |
| Perlu Tindakan Anda | 🟠 | mentor minta revisi (+ catatan dikirim bila ada) | tidak |
| Perbaikan Menunggu Peninjauan | ⏳ | revisi sudah dikirim | tidak |
| Hadir/Izin disetujui | ✅ / 🟢 | final | tidak |
| Kehadiran Ditolak / Tidak Hadir | ❌ / ⛔ | perlu cek portal | tidak |
| Hari Libur | 🗓️ | `DAYOFF` atau penanda libur dari `/home` | tidak |
| Status Tidak Dikenal | ❔ | enum baru dari portal; bot kirim klaim mentahnya | tidak |

Kalau API mengirim nilai enum yang belum dipetakan, label jadi `UNKNOWN` dan bot
mengirim satu pesan berisi klaim mentahnya (`attendance.status=… log.state=…`)
supaya mapping bisa dikalibrasi, bukan didiamkan.

**Kapan pesan dikirim?** Hanya saat label sebuah tanggal **berubah** (satu pesan per
perubahan, state tersimpan di DB). Menambah satu kolom isian sambil tetap berstatus
🟡 Sebagian Terisi tidak mengirim pesan baru — anti-spam; yang dikirim adalah
pesan berikutnya saat status naik ke 🟣 / 📝 / ✅.

## Mengisi token lewat Telegram

Token disimpan di DB, jadi penggantiannya cukup dari chat — tanpa SSH, tanpa restart:

1. Ambil token dari browser tempat Anda login monev — langkah lengkapnya (Console,
   Network tab, bookmarklet satu klik) ada di
   [`tools/bookmarklet.md`](tools/bookmarklet.md); yang paling cepat adalah cara
   Console + `copy()` karena tidak perlu membuat bookmark.
2. Kirim ke bot:
   ```
   /token Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9....
   ```
   Prefiks `Bearer ` boleh dipakai boleh tidak, dan menempel token polos tanpa
   perintah (pesan yang dimulai `eyJ0eXAi…`) juga diterima.
3. Bot memanggil `/api/v1/users/me/home` dengan token itu sebagai uji. Kalau lolos,
   token disimpan + flag `token_invalid` dibersihkan + balasan berisi sisa umur
   sesi; kalau ditolak, token lama tidak tertimpa dan bot langsung menjelaskan penyebabnya.

Cara lain (bila Telegram sedang tidak bisa diakses):

```bash
python run.py --set-token "Bearer eyJ0eXAi…"   # validasi + simpan ke DB, lalu bot jalan
```

Isi `BEARER_TOKEN` di `config.py` hanya dipakai sebagai seed saat DB belum punya
user; setelah token masuk lewat `/token`, yang dipercaya adalah token di DB.

## Konfigurasi

```bash
cp config.py.example config.py    # lalu isi: token bot, chat id, participant id, bearer
chmod 600 config.py
```

| Key | Default | Keterangan |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | dari @BotFather |
| `TELEGRAM_CHAT_ID` | — | chat yang dilayani (single-user) |
| `MONEV_API_URL` | `https://monev-api.maganghub.kemnaker.go.id/api/v1/daily-logs` | boleh URL endpoint; base URL diturunkan otomatis |
| `PARTICIPANT_ID` | — | UUID peserta magang |
| `BEARER_TOKEN` | — | token awal; setelah itu cukup lewat `/token` di chat |
| `TZ_NAME` | `Asia/Jakarta` | zona waktu untuk reminder & "hari ini" |
| `POLL_MINUTES` | `15` | minimum 5; bisa diubah runtime lewat `/interval` |
| `REMINDER_EVENING` / `_FOLLOWUP` / `_EMERGENCY` | `18:00` / `21:00` / `23:00` | jam pengingat |
| `DB_PATH` | `data/bot.db` | SQLite |
| `LOG_LEVEL` | `INFO` | |

`config.py`, `data/`, `state.json`, dan file HAR/`download` sudah di-gitignore
(kandungannya berisi token).

## Menjalankan

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows; Linux: source .venv/bin/activate
pip install -r requirements.txt

python run.py --once      # satu siklus polling lalu keluar (cek token & mapping)
python run.py             # jalan terus: polling + perintah Telegram
```

```bash
# Ubuntu
sudo bash deploy/install_ubuntu.sh          # pasang ke /opt/monev-bot + systemd
sudo systemctl status monev-bot
sudo journalctl -fu monev-bot
```

### Perintah di chat

| Perintah | Fungsi |
| --- | --- |
| `/status` | status hari ini + riwayat 7 hari |
| `/cek` | paksa polling sekarang, laporkan perubahan |
| `/token <Bearer …>` | ganti token sesi (divalidasi dulu ke API) |
| `/tokeninfo` | token aktif?, sisa umur, fingerprint sesi |
| `/reminder on\|off` | hidupkan/matikan pengingat harian |
| `/interval <menit>` | ubah frekuensi polling (min 5, tersimpan di DB) |
| `/log [n]` | n event terakhir |
| `/help` | daftar perintah |

Token juga diterima tanpa perintah: cukup tempel string `eyJ0eXAi…`.

## Catatan token

Sesi monev berlaku ±6 jam (`ttl: 21600`) dan **login baru membatalkan sesi lama** —
token yang berumur 2 jam bisa sudah 401 begitu Anda login ulang di HP. Karena itu:

1. bot menandai `token_invalid`, berhenti polling/reminder (tidak mengirim pesan dari data basi),
2. mengirim instruksi penggantian token sekali per hari (bukan tiap 15 menit),
3. lanjut otomatis begitu token baru masuk — tanpa restart service.

Cara tercepat mengambil token: bookmarklet di [`tools/bookmarklet.md`](tools/bookmarklet.md).

## Pengujian

```bash
python -m pytest -q                # 55 test: status mapping, alur notifikasi, reminder plan, b64/JWT/settings
python scripts/probe_api.py        # probe live: cetak status per tanggal + enum yang muncul di data nyata
python scripts/probe_api.py --days 14 --token "Bearer …"
```

`probe_api.py` tidak menyentuh Telegram dan tidak menulis DB. Nilai enum yang
ditampilkan di bagian akhir probe adalah bahan untuk mengoreksi `bot/status.py`
bila portal melepas status baru.

## Batasan yang disengaja

- **Single-user.** Chat selain `TELEGRAM_CHAT_ID` ditolak (dan Anda diberi tahu ada pesan asing).
  Tabel `users` sudah ber-`user_id` di semua relasi, jadi perluasan multi-user tidak butuh migrasi.
- Tidak ada webhook/port inbound — cuma long polling, jadi server tanpa domain/HTTPS pun aman.
- Bot tidak mengisi laporan; menulis ke portal sengaja tidak diimplementasikan.
- Versi lama ada di `legacy/main.py` dan tidak dipelihara.
