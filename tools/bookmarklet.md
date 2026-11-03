# Token & cookie monev: cara mengambil & mengisinya (step by step)

Bot tidak punya akun/password ke Maganghub. Yang bot punya cuma **token sesi** Anda.
Token itu:

- umur efektifnya ±6 jam (`ttl: 21600` detik), **dan**
- mati seketika begitu Anda login lagi di perangkat lain (HP/PC lain),

jadi token memang bisa mati kapan saja. Kalau mati, bot mengirim satu pesan
*"Token monev tidak berlaku lagi"* — tidak spam, dan lanjut otomatis begitu ada
kredensial baru.

**Supaya Anda tidak perlu mengisi token lagi**: kirim cookie `monev_refresh_token`
satu kali lewat **Cara E** (bagian bawah). Dengan cookie itu bot mencetak token baru
sendiri lewat `/api/v1/auth/refresh`, dan mode manual di bawah tetap bisa dipakai
kalau cookie ikut mati.

Isi token **tidak** lewat file config dan **tidak** lewat server. Cukup dari chat
Telegram. Dibawah ini 5 cara, urut dari yang paling gampang.

> Token juga **tidak** ada di dalam bentuk isian di website monev. Banyak yang
> macet di sini: portal tidak menampilkan token dimanapun. Token hanya bisa diambil
> dari *dalam browser* Anda (localStorage/cookie/header request) setelah login.

## Langkah 0 — sebelum mulai (sering terlewat)

1. `config.py` sudah berisi `TELEGRAM_BOT_TOKEN` (dari @BotFather) dan
   `TELEGRAM_CHAT_ID` (ID chat Anda — kirim pesan apa pun ke @RawDataBot untuk
   melihatnya).
2. Botnya harus **hidup** saat Anda mengirim token, karena yang memproses `/token`
   adalah bot itu sendiri, bukan Telegram.
   - Di PC (Windows, folder ini):
     ```powershell
     .\.venv\Scripts\python.exe run.py
     ```
     Biarkan jendelanya terbuka. Semua perintah (`/token`, `/status`) dikirim dari
     chat Telegram, bukan dari jendela ini.
   - Di server: `sudo systemctl status monev-bot` → harus `active (running)`.
3. Kirim `/start` di chat @monev_bot. Ada balasan = bot siap, lanjut ke Cara A.
   Tidak ada balasan sama sekali = masalahnya di token bot Telegram / chat ID /
   proses bot yang mati — bukan di token monev.

Ketik `/panduan` kapan pun di chat bot untuk melihat langkah-langkah ini ulang.


---

## Cara A — Console + `copy()` (direkomendasikan, tanpa install apa pun)

Kurang dari 30 detik, tidak perlu membuat bookmark.

1. Di laptop/PC, buka `https://monev.maganghub.kemnaker.go.id` dan **login seperti
   biasa** lewat Chrome atau Edge (Firefox juga bisa).
2. Tekan **F12** → pilih tab **Console**.
3. Kalau muncul tulisan peringatan biru `"allow pasting"`, ketik persis
   `allow pasting` lalu Enter. (Ini pengaman Chrome sekali per browser; setelah itu
   Anda bisa menempel kode.)
4. Tempel potongan di bawah ini, tekan Enter:

   ```js
   (function () {
     var re = /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}/;
     var token = null, where = '';
     function scan(st, name) {
       for (var i = 0; i < st.length; i++) {
         var m = re.exec(st.getItem(st.key(i)) || '');
         if (m) { token = m[0]; where = name + ' -> ' + st.key(i); return true; }
       }
       return false;
     }
     if (!scan(localStorage, 'localStorage') && !scan(sessionStorage, 'sessionStorage')) {
       var c = re.exec(document.cookie || '');
       if (c) { token = c[0]; where = 'cookie'; }
     }
     if (!token) { console.log('Token tidak ketemu di storage. Pakai Cara B (Network tab).'); return; }
     console.log('Sumber :', where);
     console.log('Panjang:', token.length, 'karakter');
     if (typeof copy === 'function') {
       copy(token);
       console.log('✅ Token sudah tersalin ke clipboard — tinggal paste di chat bot.');
     } else if (navigator.clipboard) {
       navigator.clipboard.writeText(token).then(
         function () { console.log('✅ Token tersalin.'); },
         function (e) { console.log('Gagal salin otomatis:', e, '\nSalin manual teks di atas.'); }
       );
     }
     console.log('Atau klik link ini untuk buka chat bot dengan teks sudah terisi:');
     console.log('https://t.me/monev_bot?text=' + encodeURIComponent('/token ' + token));
   })();
   ```

5. Buka chat bot **@monev_bot** di Telegram, lalu:
   - ketik `/token` diikuti satu spasi, dan **paste** (Ctrl+V) token tadi, atau
   - cukup paste token polos tanpa `/token` — bot mengenalinya dari awalan `eyJ0eXAi`.
6. Tekan kirim. Balasan yang Anda harapkan:

   ```
   ✅ Token tersimpan & tervalidasi
   Token: eyJ0eXAiOi…Qq_TXw
   Perkiraan umur: 360 menit
   Fingerprint sesi: 79230891-cc
   ```

7. Pastikan dengan `/status` (harus muncul tabel tanggal, bukan pesan token mati).

> Kalau di langkah 4 tertulis `Token tidak ketemu di storage`, berarti portal
> menyimpan token di memory (bukan localStorage). Pakai Cara B.

## Cara B — Network tab (paling andal, terlihat mata)

Dipakai kalau Cara A gagal, atau kalau Anda ingin yakin token yang mana.

1. Login di portal monev pada Chrome/Edge.
2. Tekan **F12** → tab **Network** → tekan tombol 🚫 (Clear) → lalu di portal klik
   menu **Aktivitas Harian** / **Daily Activity** supaya ada request API yang lewat.
3. Di kolom filter, ketik `daily-logs` (kalau tidak ada, coba `home`).
4. Klik baris request itu → panel kanan **Headers** → gulir ke **Request Headers**.
5. Cari baris `authorization` → nilainya berbentuk `Bearer eyJ0eXAi…` →
   klik kanan nilainya → **Copy value**.
6. Ke chat @monev_bot → kirim `/token <paste>`.

Catatan: prefiks `Bearer ` boleh ikut tersalin; bot membuangnya sendiri.

## Cara C — Bookmarklet (sekali klik, untuk pemakaian jangka panjang)

Buat sekali, setelah itu ambil token cukup 2 klik.

1. Tampilkan bookmark bar: **Ctrl+Shift+B**.
2. Klik kanan di bookmark bar → **Add page** / **Tambah bookmark**.
3. Name: `Ambil token monev`. URL: tempel satu baris berikut **persis** (jangan ada
   baris baru), lalu Save:

   ```
   javascript:(function(){var r=/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}/,t='',w='';function scan(s,n){for(var i=0;i<s.length;i++){var k=s.key(i),v='';try{v=s.getItem(k)||''}catch(e){}var m=r.exec(v);if(m){t=m[0];w=n+' \u2192 '+k;return true}}return false}if(!scan(localStorage,'localStorage')&&!scan(sessionStorage,'sessionStorage')){var c=r.exec(document.cookie);if(c){t=c[0];w='cookie'}}if(!t){alert('Token JWT tidak ketemu di storage browser. Pakai Cara B (Network tab).');return}var p={};try{p=JSON.parse(atob(t.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')))}catch(e){}var out='Token: '+t+'\nSumber: '+w+(p.ttl?'\nUmur sesi: '+(p.ttl/3600)+' jam':'')+(p.user?'\nuser: '+p.user:'');try{navigator.clipboard.writeText(t)}catch(e){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove()}if(confirm(out+'\n\nBuka chat bot dan kirim /token ?')){location.href='https://t.me/monev_bot?text='+encodeURIComponent('/token '+t)}})();
   ```

   (Link-nya sudah terisi `t.me/monev_bot`. Kalau username bot Anda berubah,
   ganti di dua tempat: `location.href` dan saat menyimpan bookmark.)
4. Setiap butuh token: buka portal monev (sudah login) → klik bookmark itu →
   muncul ringkasan token + sumber penyimpanan → **OK** → tab Telegram terbuka
   dengan pesan `/token eyJ0eXAi…` sudah terisi → tekan **kirim**.

Yang dicari bookmarklet: string JWT di `localStorage` → `sessionStorage` → `cookie`.
Nama key storage sengaja tidak ditulis manual supaya tidak rusak saat portal ganti
rilis.

## Cara D — Dari server (kalau Telegram sedang tidak bisa dibuka)

```bash
cd /opt/monev-bot
sudo -u monevbot .venv/bin/python run.py --set-token "Bearer eyJ0eXAi…"
# atau aktifkan mode tanpa-token dari CLI:
sudo -u monevbot .venv/bin/python run.py --set-cookie "monev_refresh_token=…"
```

Token divalidasi ke API dulu; kalau ditolak, token lama di DB tidak tertimpa.

---

## Cara E — Cookie refresh: berhenti memasukkan token

Token akses mati tiap ±6 jam, jadi Cara A–D itu solusi sementara. Portal punya
endpoint `POST /api/v1/auth/refresh` untuk mencetak token baru, dan bot bisa
memanggilnya sendiri asalkan punya cookie `monev_refresh_token`. Cookie itu
**httpOnly**, jadi tidak akan pernah muncul di Console atau `localStorage` — hanya
bisa dilihat di panel DevTools:

1. Login biasa di `monev.maganghub.kemnaker.go.id` (Chrome/Edge di PC paling mudah).
2. Tekan `F12` → tab **Application**. (Firefox: tab **Storage**.)
3. Kiri: **Cookies** → klik `https://monev-api.maganghub.kemnaker.go.id`
   — perhatikan, ini domain API, bukan domain halaman portal.
4. Cari baris **`monev_refresh_token`**, klik nilai panjang di kolom Value,
   lalu `Ctrl+C` (atau klik kanan → *Copy value*).
5. Kirim ke bot, satu kali saja:
   ```
   /cookie aiS7k…(nilai lengkap)…
   ```
   Awalan `monev_refresh_token=` boleh ikut ditempel, bot memaklumi keduanya.

Bot langsung mengetes cookie itu ke API. Balasan **“🍪 Cookie refresh tersimpan”**
berarti mulai sekarang token diperpanjang otomatis (job tiap 30 menit + percobaan
ulang setiap kali API membalas 401) dan Anda tidak perlu mengirim token lagi.
Kalau balasannya **“🔒 Sesi monev benar-benar berakhir”**, cookie tidak disimpan —
umumnya artinya Anda sedang logout atau sesi akun Kemnaker-nya habis; login lagi,
ulang langkah 2–5.

Catatan yang belum terukur di lapangan: server bisa saja merotasinya (cookie lama
tidak berlaku lagi) atau menolak sesi ganda. Kalau setelah bot refresh tab browser
Anda minta login ulang, itu tanda sesi berganti — tidak merusak data apa pun, dan
cukup kirim `/cookie` yang baru. Cek kondisi riilnya lewat `/log` dan `/tokeninfo`.

---

## FAQ yang pasti muncul

**Kenapa tidak cukup isi `BEARER_TOKEN` di `config.py`?**
`config.py` hanya jadi *seed* saat database belum punya baris user. Begitu Anda
pernah mengirim `/token`, yang dipercaya bot adalah token di `data/bot.db` —
mengubah `config.py` tidak akan berpengaruh. Ini disengaja supaya token tidak
pernah tertulis di file yang bisa ikut terbaca backup/git.

**Habis kirim token, service perlu restart?**
Tidak. Scheduler membaca token dari DB setiap siklus polling.

**Ditolak terus, padahal token baru.**
Tiga penyebab paling sering:
1. Token berasal dari sesi yang sudah Anda gantikan (Anda login ulang di HP setelah
   menyalin token) → ambil token lagi **setelah** login terakhir, dari browser yang sama.
2. Salah potong — token terpotong. Token utuh biasanya >800 karakter dan punya **tepat
   dua** titik (`header.payload.signature`). Kirim `/tokeninfo` untuk melihat apa
   yang tersimpan di DB.
3. Sesi Anda sendiri sudah kedaluwarsa di browser. Cek: masih bisa buka halaman
   aktivitas harian tidak? Kalau diminta login lagi, login dulu lalu ulangi Cara A.

**Bisa tidak bot refresh token sendiri?**
Tidak, dan itu bukan kekurangan yang mau saya tutup diam-diam: portal memakai
OAuth (login → redirect `/api/v1/auth/login/callback?code=…` → `access_token`).
Mengotomatinya berarti bot harus menyimpan email/password Anda dan ikut alur
login, yang melanggar prinsip "bot ini hanya pembaca". Jadi: refresh manual,
tapi dibuat 30 detik lewat Cara A/C.

**Aman nggak token lewat chat Telegram?**
Token tersimpan di riwayat chat Anda. Pakai bot di akun pribadi, dan aktifkan
auto-delete pesan untuk chat itu (Telegram Desktop: klik nama chat di bagian atas →
**Auto-delete messages** → **24 hours**). Jangan kirim token dari PC warnet/kantor,
karena bookmarklet membuka tab `t.me` dengan token terbaca di address bar.

**Bagaimana kalau saya cuma punya HP?**
Chrome/Edge di HP tidak punya Console, dan header request juga tidak bisa dilihat di
sana. Jadi ambil token tetap perlu salah satu dari ini: browser HP dengan dev tools
(Firefox Android atau Kiwi/Bromium, aktifkan *Request desktop site*), pinjam PC sebentar
untuk Cara A/B/C, atau `run.py --set-token` lewat SSH (Cara D). Setelah token masuk ke
DB, yang Anda kerjakan dari HP hanya mengirim ulang `/token` kalau sesi berganti.

**Bot-nya tahu dari mana saya bukan orang lain?**
Hanya `TELEGRAM_CHAT_ID` di `config.py` yang dilayani. Chat lain ditolak, dan Anda
dikabari ada pesan masuk dari chat asing.
