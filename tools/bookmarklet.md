# Mengambil token monev dengan cepat (bookmarklet)

Token sesi monev berlaku ±6 jam dan mati duluan kalau Anda login ulang, jadi
refresing token adalah satu-satunya pekerjaan rutin. Halaman ini memberi tiga
cara, dari tercepat ke paling manual.

Prasyarat: Anda sudah login di `https://monev.maganghub.kemnaker.go.id` pada
browser yang sama.

---

## 1. Bookmarklet (sekali klik, otomatis copy + buka chat bot)

Buat bookmark baru di browser, isi **URL/Address** dengan satu baris di bawah ini
(jangan sampai ada baris baru), lalu klik bookmark itu saat halaman monev terbuka:

```
javascript:(function(){var r=/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}/,t='',w='';function scan(s,n){for(var i=0;i<s.length;i++){var k=s.key(i),v='';try{v=s.getItem(k)||''}catch(e){}var m=r.exec(v);if(m){t=m[0];w=n+' \u2192 '+k;return true}}return false}if(!scan(localStorage,'localStorage')&&!scan(sessionStorage,'sessionStorage')){var c=r.exec(document.cookie);if(c){t=c[0];w='cookie'}}if(!t){alert('Token JWT tidak ketemu di storage browser. Pakai cara DevTools (bagian 3).');return}var p={};try{p=JSON.parse(atob(t.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')))}catch(e){}var out='Token: '+t+'\nSumber: '+w+(p.ttl?'\nUmur sesi: '+(p.ttl/3600)+' jam':'')+(p.user?'\nuser: '+p.user:'');try{navigator.clipboard.writeText(t)}catch(e){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove()}if(confirm(out+'\n\nBuka chat bot dan kirim /token ?')){location.href='https://t.me/GANTI_DENGAN_USERNAME_BOT?text='+encodeURIComponent('/token '+t)}})();
```

Ganti `GANTI_DENGAN_USERNAME_BOT` dengan username bot Anda (contoh
`https://t.me/monev_reminder_bot?text=...`).

Yang dilakukan bookmarklet:

1. Menyapu `localStorage`, lalu `sessionStorage`, lalu `cookie` mencari string
   berformat JWT (`eyJ….….…`) — jadi tidak perlu tahu nama key yang dipakai
   portal (nama key bisa berubah tiap rilis frontend).
2. Menyalin token ke clipboard (fallback `execCommand` untuk browser lama).
3. Menampilkan umur sesi dari klaim `ttl` (monev memakai `ttl: 21600` = 6 jam,
   dan **tidak** memakai klaim `exp`), lalu menawarkan membuka chat bot dengan
   teks `/token <jwt>` sudah terisi — Anda tinggal tekan kirim.

Bot akan memvalidasi token ke API sebelum menyimpannya, jadi token basi langsung
ditolak dengan pesan jelas.

> Catatan keamanan: teks token muncul di address bar saat tab `t.me` dibuka.
> Tidak masalah di komputer pribadi, tapi hindari cara ini di PC warnet/kantor.

## 2. Tempel manual di Console

Kalau lebih suka DevTools (F12 → Console), jalankan potongan yang bisa dibaca ini
lalu salin hasilnya:

```js
(() => {
  const re = /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}/;
  for (const [name, st] of [['localStorage', localStorage], ['sessionStorage', sessionStorage]]) {
    for (let i = 0; i < st.length; i++) {
      const m = re.exec(st.getItem(st.key(i)) || '');
      if (m) return console.log(name, '→', st.key(i), '\n\nKlik kanan -> Salin:', m[0]);
    }
  }
  const c = re.exec(document.cookie);
  console.log(c ? 'cookie → ' + c[0] : 'Token tidak ditemukan di storage; pakai cara 3.');
})();
```

Lalu di chat Telegram kirim:

```
/token eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9……
```

Prefiks `Bearer ` boleh disertakan — bot membuangnya sendiri. Bot juga menerima
token tanpa perintah: cukup tempel string `eyJ0eXAi…` sebagai pesan biasa.

## 3. Paling andal: Network tab

Kalau dua cara di atas gagal (misalnya frontend pindah ke memory-only token):

1. DevTools → tab **Network**.
2. Filter `daily-logs` atau `home`, klik salah satu request XHR.
3. Buka **Request Headers** → nilai `Authorization` → salin setelah kata `Bearer`.
4. Kirim ke bot lewat `/token`.

## Kalau semuanya gagal

Bot akan mengirim peringatan sendiri ketika token mulai ditolak server
(HTTP 401). Sampai token diganti, polling dan reminder ditahan agar tidak
mengirim pesan palsu berbasis data basi. Status `token_invalid` disimpan di DB,
sehingga setelah token baru masuk, pantauan lanjut otomatis tanpa restart service.
