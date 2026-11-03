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
bot/
  settings.py         baca config.py (rahasia), normalisasi, masking token
  db.py               skema SQLite (users, day_state, notify_flags, event_log, settings)
  store.py            seluruh query; satu-satunya modul yang menyentuh SQL
  models.py           User, DayRecord
  monev/
    status.py         PETA STATUS — port fungsi i(e, t, r) dari frontend monev
    client.py         httpx + retry + pemisahan TokenRejected (401/403) vs error lain
    endpoints.py      home, attendances, daily-logs, absence-warnings, announcements, system/time, users/me
    b64.py            dekode response base64 (announcements)
    jwt_utils.py      baca klaim ttl/fingerprint (token monev tidak punya `exp`)
  messages/           SEMUA kalimat pesan, dipecah per topik (tanpa logika)
    base.py           esc(), pretty_date(), short_date() (hari/bulan bahasa Indonesia)
    absensi.py        notif perubahan status + catatan mentor + pengumuman/warning
    pengisian.py      pengingat berjenjang (soft/rawan/dekat/mepet) + cek sore + terlewat
    harian.py        paket pagi: libur hari ini, evaluasi H-7, pengajuan uang saku
    tagihan.py        tagihan absensi belum di-ACC ≥5 hari
    sesi.py           token/cookie: panduan, kedaluwarsa, tersimpan, ditolak
    chat.py           /help, /start, /status, /cek, dan balasan perintah lain
  jobs/
    poll.py           gabung 3 endpoint, deteksi perubahan, kirim notifikasi
    reminders.py      JADWAL TERKUNCI: 16:55 + berjenjang 18:00–23:59 + paket pagi 08:00
    token_health.py   peringatan H-30 menit, deteksi 401, auto-refresh via cookie
    announcements.py  pengumuman + warning keterlambatan dari portal
  telegram/
    api.py            wrapper Bot API (getUpdates/sendMessage), tanpa library eksternal
    commands.py       dispatch perintah + BotContext (kalimat diambil dari bot/messages/)
scripts/probe_api.py  alat live: panggil API, cetak status hasil pemetaan
tools/bookmarklet.md  panduan langkah-demi-langkah mengambil & mengisi token
```

## Cara kerja status

Yang dikejar bot ini adalah **tiga isian harian di portal**: `activity_log`
(aktivitas), `lesson_learned` (pembelajaran), `obstacles` (kendala) — nama kolom
terkonfirmasi dari response `/api/v1/daily-logs`. Selama ketiganya belum lengkap,
status masih ditagih pengingat; begitu lengkap, portal langsung menandainya
terkirim (tidak ada tahap "belum dikirim"), lalu bot mengirim "Menunggu ACC Mentor".

Frontend monev menggabungkan **dua** objek untuk melabeli satu tanggal:
`attendance` (`status`, `approval_status`) dan `daily-log` (`state`). Versi lama
hanya membaca daily-log, sehingga label yang bergantung pada kehadiran tidak
pernah aktif. `bot/monev/status.py` menata ulang urutan pengecekan seperti frontend:
`DAYOFF` → `ABSENT` → `NEEDS_REVISION` → `REJECTED` → `REVISION_SUBMITTED` →
`COMPLETED` → kelengkapan isian → `APPROVED` → `SUBMITTED` → fallback.

Satu **deviasi sadar** dari frontend: frontend sudah menulis "Hadir disetujui"
begitu `attendance.approval_status == APPROVED`, padahal uraian harian boleh jadi
masih kosong. Untuk bot pengingat, ACC kehadiran tidak dianggap menyelesaikan
pekerjaan hari itu — jadi isian yang belum lengkap tetap ditandai
🔵/🟡 (hari izin `ON_LEAVE` dikecualikan, karena tidak ada uraian yang ditagih).

| Label | Emoji | Arti | Ditagih pengingat? |
| --- | --- | --- | --- |
| Belum Diisi | ⚪ | belum ada absensi & laporan | ya |
| Sudah Absen, Uraian Belum Diisi | 🔵 | kehadiran tercatat, isi masih kosong | ya |
| Sebagian Terisi | 🟡 | 1–2 dari 3 kolom terisi (yang kosong disebutkan) | ya |
| Menunggu ACC Mentor | 📝 | lengkap/terkirim, tunggu ACC mentor | tidak |
| Perlu Revisi | 🟠 | mentor minta revisi (+ catatan dikirim bila ada) | tidak |
| Perbaikan Menunggu Peninjauan | ⏳ | revisi sudah dikirim | tidak |
| Hadir/Izin disetujui | ✅ / 🟢 | final | tidak |
| Ditolak / Tidak Hadir | ❌ / ⛔ | perlu cek portal | tidak |
| Hari Libur | 🗓️ | `DAYOFF` atau penanda libur dari `/home` | tidak |
| Status Tidak Dikenal | ❔ | enum baru dari portal; bot kirim klaim mentahnya | tidak |

Kalau API mengirim nilai enum yang belum dipetakan, label jadi `UNKNOWN` dan bot
mengirim satu pesan berisi klaim mentahnya (`attendance.status=… log.state=…`)
supaya mapping bisa dikalibrasi, bukan didiamkan.

**Kapan pesan dikirim?** Hanya saat label sebuah tanggal **berubah** (satu pesan per
perubahan, state tersimpan di DB). Menambah satu kolom isian sambil tetap berstatus
🟡 Sebagian Terisi tidak mengirim pesan baru — anti-spam; yang dikirim adalah
pesan berikutnya saat status naik ke 📝 / ✅.

## Jadwal pengingat (terkunci)

Penjadwal memanggil `bot/jobs/reminders.py` tiap 1 menit. Jadwalnya **sengaja
terkunci** — tidak bisa diubah atau dimatikan dari chat, karena ini pengingat
penting. Semua kalimat aslinya ada di `bot/messages/pengisian.py`, `harian.py`,
dan `tagihan.py`.

| Jam | Isi | Frekuensi |
| --- | --- | --- |
| 16:55 | Cek sore — menagih yang belum / menandai yang sudah diisi | 1x sehari |
| 18:00–20:59 | Pengingat lunak selama isian belum lengkap | tiap 30 menit |
| 21:00–23:29 | Jam rawan — nada lebih tegas | tiap 15 menit |
| 23:30–23:45 | Mendekati tenggat | tiap 5 menit |
| 23:46–23:59 | Menit-menit terakhir | tiap 3 menit |
| 00:01–06:00 | Laporan kemarin terlewat | 1x |
| 08:00–11:59 | Paket pagi: info libur, tagihan ACC ≥5 hari, evaluasi H-7 (tgl 2–8), uang saku (tgl 9) | 1x per item |

Tier malam hanya jalan selama status masih layak ditagih (`EMPTY`/`ATTENDED_NO_LOG`/`PARTIAL`).
Ketelitian menit diambil dari selisih waktu `set_at` flag per tier, bukan dari jumlah
thread; tiap "sekali sehari" dijamin flag unik di `notify_flags` sehingga restart di
tengah hari tidak mengulang pesan yang sudah terkirim.

## Mengisi token lewat Telegram

Token disimpan di DB, jadi penggantiannya cukup dari chat — tanpa SSH, tanpa restart.
Yang perlu diingat: portal monev **tidak** menampilkan token di manapun; token adalah
kunci sesi yang tersimpan di dalam browser setelah Anda login, jadi ia diambil dari
DevTools, bukan dari menu website.

0. Nyalakan botnya lebih dulu (`python run.py` di PC, atau service `monev-bot` aktif di
   server) dan pastikan `/start` dijawab. `/token` diproses oleh bot, bukan Telegram.
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
4. Lupa langkahnya? Ketik `/panduan` di chat bot — langkah 0–3 di atas diulang langsung
   dalam bahasa chat ( `/token` tanpa isi juga mengirim panduan yang sama).

Cara lain (bila Telegram sedang tidak bisa diakses):

```bash
python run.py --set-token "Bearer eyJ0eXAi…"   # validasi + simpan ke DB, lalu bot jalan
python run.py --set-cookie "monev_refresh_token=…"  # aktifkan auto-refresh dari CLI
```

Isi `BEARER_TOKEN` di `config.py` hanya dipakai sebagai seed saat DB belum punya
user; setelah token masuk lewat `/token`, yang dipercaya adalah token di DB.

## Mode tanpa token (auto-refresh)

Token akses monev berumur ±6 jam, jadi mengisinya manual itu melelahkan. Portal punya
`POST /api/v1/auth/refresh` yang mencetak token baru — tapi endpoint itu **tidak**
membaca Bearer, ia membaca cookie httpOnly `monev_refresh_token` milik domain API.
Karena httpOnly, cookie itu tidak kelihatan di Console/localStorage; hanya bisa
diambil dari panel DevTools:

1. Login di `monev.maganghub.kemnaker.go.id`.
2. F12 → tab **Application** (Firefox: **Storage**) → **Cookies** →
   `https://monev-api.maganghub.kemnaker.go.id`.
3. Salin nilai baris `monev_refresh_token`.
4. Kirim sekali saja: `/cookie <nilai>` — bot langsung mengetesnya ke API, menyimpan
   cookie + token baru, lalu menolak menyimpan kalau server menolaknya.

Setelah itu bot memperpanjang token sendiri: job token-health (tiap 30 menit)
memanggil refresh saat sisa umur token ≤ `TOKEN_REFRESH_LEAD_MINUTES` (default 90),
dan setiap polling yang kena 401 langsung coba refresh lalu mengulang sekali
(`poll_auto`). Selama cookie masih hidup, tidak ada lagi input token.

Kalau suatu hari sesi Kemnaker Anda benar-benar berakhir (logout, umur maksimum
sesi lewat), bot mengirim pesan sekali sehari berisi cara ambil cookie baru; token
manual lewat `/token` tetap diterima sebagai jalan pintas. `/tokeninfo` menampilkan
status auto-refresh. Belum tahu caranya? Ketik `/cookie` (kosong) untuk panduan ambil
cookie, atau `/panduan` untuk ambil token.

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
| `MONEV_REFRESH_TOKEN` | kosong | cookie `monev_refresh_token`; setelah itu cukup lewat `/cookie` |
| `MONEV_WEB_URL` | `https://monev.maganghub.kemnaker.go.id` | origin SPA untuk `version.json` (header build id) |
| `TOKEN_REFRESH_LEAD_MINUTES` | `90` | mulai refresh saat sisa umur token di bawah selang ini |
| `TZ_NAME` | `Asia/Jakarta` | zona waktu untuk pengingat & "hari ini" |
| `POLL_MINUTES` | `15` | minimum 5; bisa diubah runtime lewat `/interval` |
| `DB_PATH` | `data/bot.db` | SQLite |
| `LOG_LEVEL` | `INFO` | |

> Jadwal pengingat harian **sengaja tidak dibuat configurable** — terkunci di
> `bot/jobs/reminders.py` dan tidak bisa diubah/dimatikan dari chat.

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
# Ubuntu (server — sudah terpasang di /opt/monev-bot + systemd)
cd /opt/monev-bot && sudo -u monevbot git pull origin master
sudo systemctl restart monev-bot
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
| `/interval <menit>` | ubah frekuensi polling (min 5, tersimpan di DB) |
| `/log [n]` | n event terakhir |
| `/help` | daftar perintah |

Token juga diterima tanpa perintah: cukup tempel string `eyJ0eXAi…`.

## Catatan token

Sesi monev berlaku ±6 jam (`ttl: 21600`) dan **login baru membatalkan sesi lama** —
token yang berumur 2 jam bisa sudah 401 begitu Anda login ulang di HP. Karena itu:

1. bot menandai `token_invalid`, berhenti polling/pengingat (tidak mengirim pesan dari data basi),
2. mengirim instruksi penggantian token sekali per hari (bukan tiap 15 menit),
3. lanjut otomatis begitu token baru masuk — tanpa restart service.

Cara tercepat mengambil token: bookmarklet di [`tools/bookmarklet.md`](tools/bookmarklet.md).

## Pengujian

```bash
python -m pytest -q                # 89 test: status mapping, alur notifikasi, pengingat berjenjang, auto-refresh, kalimat pesan, b64/JWT/settings
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
