# Bot Pengingat Absensi Maganghub (monev)

Bot Telegram yang memantau portal monev Maganghub atas nama setiap pengguna
terdaftar dan mengirim pesan ketika:

- status absensi/uraian harian berubah (mis. di-ACC mentor, diminta revisi, ditolak),
- laporan hari ini belum diisi menjelang malam,
- token sesi monev mendekati kedaluwarsa atau mulai ditolak server,
- hasil evaluasi bulanan mentor keluar ("Belum Diisi" → "Sudah Diisi Mentor"),
- ada pengumuman/warning dari portal.

Bot **hanya membaca** API monev — ia tidak pernah mengisi atau mengirim laporan.

Multi-user: setiap orang bisa daftar sendiri — cukup tempel token sesinya di chat
bot, `participant_id` dibaca otomatis dari portal (lihat [Multi-user](#multi-user)).

---

## Struktur

```
run.py                entrypoint (scheduler + long polling Telegram)
bot/
  settings.py         baca config.py (rahasia), normalisasi, masking token
  db.py               skema SQLite (users, day_state, notify_flags, event_log, settings)
  store.py            seluruh query; satu-satunya modul yang menyentuh SQL
  models.py           User, DayRecord, EvalItem/EvaluationResult (bintang + predikat)
  monev/
    status.py         PETA STATUS — port fungsi i(e, t, r) dari frontend monev
    client.py         httpx + retry + pemisahan TokenRejected (401/403) vs error lain
    endpoints.py      home, attendances, daily-logs, absence-warnings, announcements, system/time, users/me, monthly-reports
    evaluation.py     Pembaca laporan bulanan + capaian kurikulum (8 aspek + normalisasi nilai)
    frontend_paths.py Gali nama endpoint dari teks bundle SPA — tanpa menjalankan browser
    b64.py            dekode response base64 (announcements)
    jwt_utils.py      baca klaim ttl/fingerprint (token monev tidak punya `exp`)
  messages/           SEMUA kalimat pesan, dipecah per topik (tanpa logika)
    base.py           esc(), pretty_date(), short_date() (hari/bulan bahasa Indonesia)
    absensi.py        notif perubahan status + catatan mentor + pengumuman/warning
    pengisian.py      pengingat berjenjang (soft/rawan/dekat/mepet) + cek sore + terlewat
    harian.py        paket pagi: libur hari ini, pengingat evaluasi H-7..H-1, hari-H (tgl 9)
    evaluasi.py       status belum diisi, alert instan, kartu hasil + kalimat motivasi dinamis
    tagihan.py        tagihan absensi belum di-ACC ≥5 hari
    sesi.py           token/cookie: panduan, kedaluwarsa, tersimpan, ditolak
    chat.py           /help, /start, /status, /cek, dan balasan perintah lain
  jobs/
    poll.py           gabung 3 endpoint, deteksi perubahan, kirim notifikasi
    reminders.py      JADWAL TERKUNCI: 16:55 + berjenjang 18:00–23:59 + paket pagi 08:00
    evaluations.py    pemantau hasil evaluasi bulanan (cek 30 menit, dedup per periode)
    token_health.py   peringatan H-30 menit, deteksi 401, auto-refresh via cookie
    announcements.py  pengumuman + warning keterlambatan dari portal
  telegram/
    api.py            wrapper Bot API (getUpdates/sendMessage), tanpa library eksternal
    commands.py       dispatch perintah + BotContext (kalimat diambil dari bot/messages/)
scripts/probe_api.py  alat live: panggil API, cetak status hasil pemetaan
scripts/discover_endpoints.py  gali + verifikasi nama endpoint dari bundle SPA monev
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
| 08:00–11:59 | Paket pagi: info libur, tagihan ACC ≥5 hari, pengingat evaluasi H-7..H-1 (tgl 2–8), hari-H evaluasi + pengajuan uang saku (tgl 9) | 1x per item |

Tier malam hanya jalan selama status masih layak ditagih (`EMPTY`/`ATTENDED_NO_LOG`/`PARTIAL`).
Ketelitian menit diambil dari selisih waktu `set_at` flag per tier, bukan dari jumlah
thread; tiap "sekali sehari" dijamin flag unik di `notify_flags` sehingga restart di
tengah hari tidak mengulang pesan yang sudah terkirim.

## Evaluasi bulanan

Mentor mengisi evaluasi sikap setiap bulan, dan isinya biasanya baru masuk pada
tanggal 9 bulan berikutnya. Karena itu job evaluasi (tiap 30 menit, terpisah dari
polling absensi) menanyakan **dua periode sekaligus**: bulan berjalan + bulan
sebelumnya. Begitu status berubah menjadi "diisi mentor", flag `eval:<YYYY-MM>:filled`
ditulis lebih dulu lalu **dua pesan** dikirim berurutan:

| Pesan | Isi |
| --- | --- |
| 1 — alert | `🔔 NOTIFIKASI EVALUASI MAGANG` + "mentor baru saja selesai mengisi", "sedang memuat rincian nilai" (tanpa angka, memang belum ditampilkan) |
| 2 — kartu | `🎉 HASIL EVALUASI MAGANG KELUAR!` + 8 aspek, capaian kurikulum, komentar mentor, kalimat motivasi |

Flag ditulis sebelum mengirim supaya koneksi Telegram yang putus di tengah jalan
tidak menghasilkan alert ganda. Kartu yang sama tidak pernah datang dua kali untuk
satu periode — termasuk setelah restart service, dan termasuk kalau mentor mengedit
nilainya belakangan.

Cek manual (`/cek` dan `/evaluasi [YYYY-MM]`) memakai Respons A saat portal masih
kosong:

```
⏳ EVALUASI MENTOR BELUM DIISI

📅 Periode: November 2026
📌 Status: ⚠️ Belum ada nilai di portal
```

Di `/cek` blok itu hanya menempel kalau evaluasi bulan ini memang belum ada nilainya;
sudah terisi = `/cek` tetap ringkas seperti biasanya (kartu lengkap lewat `/evaluasi`).

Isi kartu: 8 poin penilaian sikap (kehadiran & disiplin, sikap & perilaku,
komunikasi, inisiatif & tanggung jawab, adaptasi, pengetahuan teknis,
produktivitas & ketepatan waktu, kerja sama tim), capaian kurikulum, komentar
mentor, dan satu kalimat motivasi yang dipilih dari distribusi nilai:

| Kondisi nilai | Nada kalimat |
| --- | --- |
| Semua ⭐⭐⭐⭐⭐ | Puji konsistensi + dorong ambil tanggung jawab/tantangan berikutnya |
| Dominan ⭐⭐⭐⭐–⭐⭐⭐⭐⭐ | Apresiasi performa solid + asah detail kecil (dokumentasi, inisiatif melapor) |
| Ada ⭐⭐⭐/⭐⭐ atau ada catatan spesifik | Apresiasi poin yang kuat, rangkul masukan mentor sebagai bahan belajar — tidak menghakimi |

Pemetaan nilainya: Sangat Baik (SB) ⭐⭐⭐⭐⭐, Baik (B) ⭐⭐⭐⭐, Cukup (C) ⭐⭐⭐,
Kurang (K) ⭐⭐. Kalimat motivasinya deterministik per periode, jadi notifikasi yang
dibaca ulang tidak berubah-ubah.

Aturan utamanya: **tidak ada angka karangan**. Nilai yang tidak terbaca ditampilkan
apa adanya ("—"), bagian yang tidak dikirim portal dilewati, dan kalau mentor belum
mengisi, cek manual menjawab Respons A — bukan kartu kosong.

Jebakan yang sudah dinetralkan: angka yang dikirim portal adalah **peringkat 1..4**
(1 = terbaik), bukan jumlah bintang. `1` jadi ⭐⭐⭐⭐⭐, `4` jadi ⭐⭐. Kode
`evaluation.grade_rank_of()` satu-satunya tempat pembacaan nilai, jadi aturan itu
tidak bisa terlupakan di pemanggilnya.

### Dari mana nama endpoint evaluasi diketahui

Tidak ada dokumen publik untuk API monev, dan fitur lain di proyek ini dikunci dari
HAR browser. Untuk evaluasi, namanya digali dari **bundle JS SPA yang memang
merupakan aset statis publik** — halaman `monev.maganghub.kemnaker.go.id` dibaca,
semua `<script>` dan chunk-nya disapu, lalu pemanggilan API-nya dibaca langsung dari
kodenya:

```bash
python scripts/discover_endpoints.py                    # gali: apiBase + semua path API
python scripts/discover_endpoints.py --grep "evalua|grade|monthly"   # baca cuplikan kodenya
python scripts/discover_endpoints.py --sweep            # verifikasi path dengan token aktif
```

Hasilnya: evaluasi bulanan adalah **laporan bulanan** (`/api/v1/monthly-reports`,
ditanya dengan `participant_id` + `start_date`/`end_date`), materi kurikulum lewat
`/api/v1/participants/{id}/periods/{start_date}/curriculum`, dan field nilainya
bernama `*_grade` (portal boleh membalas dengan alias `*_score`) plus
`mentor_feedback`. Tidak ada satu pun path yang mengandung kata "evaluation" —
semua tebakan sebelumnya ternyata salah.

Untuk melihat raw response aslinya di server (memastikan bentuknya masih sama):

```bash
python scripts/probe_api.py --evaluations
```

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
| `TELEGRAM_CHAT_ID` | — | chat owner: seed awal + laporan aktivitas pendaftaran |
| `MONEV_API_URL` | `https://monev-api.maganghub.kemnaker.go.id/api/v1/daily-logs` | boleh URL endpoint; base URL diturunkan otomatis |
| `PARTICIPANT_ID` | — | UUID peserta owner; kosong = tidak ada seed, hanya pendaftar mandiri |
| `INVITE_CODE` | kosong | **opsional** — kosong = pendaftaran terbuka (siapa pun bisa daftar sendiri); terisi = pendaftar wajib membawa kode |
| `BEARER_TOKEN` | — | token awal; setelah itu cukup lewat `/token` di chat |
| `MONEV_REFRESH_TOKEN` | kosong | cookie `monev_refresh_token`; setelah itu cukup lewat `/cookie` |
| `MONEV_WEB_URL` | `https://monev.maganghub.kemnaker.go.id` | origin SPA untuk `version.json` (header build id) |
| `TOKEN_REFRESH_LEAD_MINUTES` | `90` | mulai refresh saat sisa umur token di bawah selang ini |
| `TZ_NAME` | `Asia/Jakarta` | zona waktu untuk pengingat & "hari ini" |
| `POLL_MINUTES` | `15` | minimum 5; bisa diubah runtime lewat `/interval` |
| `DB_PATH` | `data/bot.db` | SQLite |
| `MAX_USERS` | `10` | plafon jumlah akun yang dipantau satu bot (penjaga spam saat pendaftaran terbuka) |
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
| `/start` | sambutan & cara mulai (untuk yang baru pertama chat) |
| `/status` | status hari ini + riwayat 7 hari |
| `/cek` | paksa polling sekarang, laporkan perubahan |
| `/evaluasi [YYYY-MM]` | hasil evaluasi bulan ini (atau bulan yang diminta) on-demand; Respons A kalau mentor belum mengisi |
| `/token <Bearer …>` | ganti token sesi (divalidasi dulu ke API) |
| `/tokeninfo` | token aktif?, sisa umur, fingerprint sesi |
| `/interval <menit>` | ubah frekuensi polling (min 5, tersimpan di DB, berlaku untuk semua pengguna) |
| `/log [n]` | n event terakhir |
| `/daftar [kode] <participant_id>` | daftar sebagai pengguna baru (kode hanya kalau `INVITE_CODE` diisi) |
| `/stop` · `/lanjut` | hentikan / lanjutkan pemantauan akun Anda (data tidak dihapus) |
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
python -m pytest -q                # 154 test: status mapping, alur notifikasi, pengingat berjenjang, auto-refresh, registrasi multi-user, laporan evaluasi + alert + kartu hasil, penggalian nama endpoint, kalimat pesan, b64/JWT/settings
python scripts/probe_api.py        # probe live: cetak status per tanggal + enum yang muncul di data nyata
python scripts/probe_api.py --days 14 --token "Bearer …"
python scripts/probe_api.py --evaluations        # raw JSON laporan bulanan + hasil parser
python scripts/discover_endpoints.py             # gali nama endpoint baru dari bundle SPA
```

`probe_api.py` tidak menyentuh Telegram dan tidak menulis DB. Nilai enum yang
ditampilkan di bagian akhir probe adalah bahan untuk mengoreksi `bot/status.py`
bila portal melepas status baru.

## Multi-user

Satu bot, satu proses, satu SQLite — tiap orang yang chat bot punya baris sendiri
di tabel `users` (dibedakan oleh Telegram chat ID) beserta token, cookie refresh,
dan status hariannya sendiri.

Cara teman Anda bergabung — **tanpa Anda perlu menyentuh config/restart**:

1. Dia chat bot dan **tempel token sesi monev** (atau `/cookie <monev_refresh_token>`).
   Baris user-nya dibuat otomatis saat itu juga.
2. Bot membaca `participant_id` miliknya sendiri dari endpoint "me" portal
   (`/users/me`, `/users/me/home`, `/participant-absence-warnings/me`) lalu langsung
   berstatus **`active`** — polling dan seluruh jadwal pengingat jalan untuk akunnya.
   ID yang salah tidak mungkin terpasang, karena datanya diambil dari token miliknya.
3. Alternatif manual, kalau portal tidak menyebutkan ID: `/daftar <participant_id>`
   (status `pending` sampai token masuk), atau `/daftar <kode> <participant_id>` jika
   `INVITE_CODE` diisi. Saat token itu dikirim, bot mencocokkan klaim dengan profil
   akun milik token (`/api/v1/users/me` + klaim `user` di JWT) — cocok → `active`,
   tidak cocok → tetap `pending` dan yang bersangkutan disuruh mengulang.
4. Anda dapat notifikasi "Pengguna baru" di chat owner beserta chat ID-nya, dan
   pendaftaran berhenti otomatis kalau jumlah user menyentuh `MAX_USERS`.

Chat baru yang menekan `/start` (atau `/help`, `/panduan`) mendapat **sambutan**
`chat.welcome_message()` — isi bot, fitur, dan langkah awal — tanpa dibuatkan baris
user dan tanpa laporan apa pun ke owner. Baru setelah dia mengirim perintah yang
menyentuh data tanpa mendaftar, owner dapat laporan "chat asing".

Yang dijamin: balasan perintah selalu memakai chat ID pengirim, jadi tidak ada
user yang melihat data user lain; `config.py` hanya jadi seed untuk owner. Satu
`/interval` berlaku global (scheduler tunggal). Pesan dari grup/channel diabaikan.

Pengguna nonaktif sementara: `/stop` (keluar dari scheduler, data tetap ada),
dihidupkan lagi dengan `/lanjut`. Menghapus orang permanen saat ini lewat SQL:
`DELETE FROM users WHERE tg_chat_id='…'`.

## Batasan yang disengaja

- Tidak ada webhook/port inbound — cuma long polling, jadi server tanpa domain/HTTPS pun aman.
- Bot tidak mengisi laporan; menulis ke portal sengaja tidak diimplementasikan.
- Jadwal pengingat sama untuk semua pengguna (terkunci di `bot/jobs/reminders.py`).
