"""Perender pesan Telegram (HTML). Semua data API di-escape dulu."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Iterable

from ..status import NAGGABLE_KEYS, Label

WEEKDAY_ID = {
    0: "Senin",
    1: "Selasa",
    2: "Rabu",
    3: "Kamis",
    4: "Jumat",
    5: "Sabtu",
    6: "Minggu",
}


def esc(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=False)


def pretty_date(iso_date: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_date)
    except ValueError:
        return esc(iso_date)
    return f"{WEEKDAY_ID[dt.weekday()]}, {dt.strftime('%d %B %Y')}"


def status_change(label: Label, log_date: str, extra: str = "") -> str:
    lines = [
        f"<b>{label.emoji} {esc(label.title)}</b>",
        f"Tanggal: <b>{pretty_date(log_date)}</b>",
    ]
    if label.action:
        lines.append(f"Tindakan: {esc(label.action)}")
    if extra:
        lines.append(f"<i>{esc(extra)}</i>")
    return "\n".join(lines)


def reminder_message(kind: str, log_date: str, label: Label) -> str:
    """Teks reminder; `kind` menentukan tingkat urgensi."""
    day = pretty_date(log_date)
    if kind == "emergency":
        return (
            f"🚨 <b>Menit-menit terakhir!</b>\n"
            f"Absensi/uraian <b>{day}</b> belum lengkap ({esc(label.title)}).\n"
            f"hari berganti dalam beberapa menit — segera isi di portal monev."
        )
    if kind == "followup":
        return (
            f"⏰ <b>Pengingat kedua</b>\n"
            f"{day} masih berstatus <b>{esc(label.title)}</b>.\n"
            f"Isi sekarang supaya tidak kena warning keterlambatan."
        )
    if kind == "attended_no_log":
        return (
            f"🔵 <b>Kehadiran sudah ada, uraian belum</b>\n"
            f"{day}: absensi tercatat, tapi uraian aktivitas masih kosong/draf.\n"
            f"Isi uraian aktivitas agar bisa di-ACC mentor."
        )
    # Label baru (Sebagian Terisi / Sudah Diisi Belum Dikirim) ikut terbaca di sini
    # karena pesannya diambil dari `label.action`, bukan teks generik.
    tail = f"\n{esc(label.action)}" if label.action else ""
    if label.key not in NAGGABLE_KEYS:
        # Bukan untuk ditagih; dipakai hanya bila ada pemanggilan tak terduga,
        # jadi jangan klaim "belum lengkap".
        return (
            f"🔔 <b>Pengingat laporan magang</b>\n"
            f"{day} status tercatat: <b>{esc(label.title)}</b>.{tail}\n"
            f"Cek portal monev bila tampak aneh."
        )
    return (
        f"🔔 <b>Pengingat laporan magang</b>\n"
        f"{day} belum lengkap ({esc(label.title)}).{tail}\n"
        f"Isi aktivitas, pembelajaran, dan kendala sebelum hari berganti."
    )


def revision_followup_message(log_date: str, hours: int) -> str:
    return (
        f"🟠 <b>Revisi belum dikerjakan</b>\n"
        f"Uraian {pretty_date(log_date)} diminta diperbaiki mentor dan sudah "
        f"berlalu {hours} jam tanpa perubahan.\n"
        f"Segera perbaiki lalu kirim ulang."
    )


def token_guide_text() -> str:
    """Dikirim saat user mengetik /token tanpa isi, atau /panduan.

    Isinya langkah yang sama dengan tools/bookmarklet.md, ringkas agar terbaca
    di layar HP. Inti yang sering salah paham: token tidak ada di menu portal,
    ia kunci sesi yang tersimpan di browser setelah login.
    """
    return (
        "📌 <b>Cara mengambil &amp; mengisi token monev</b>\n\n"
        "Token monev <b>bukan</b> sesuatu yang Anda cari di menu portal. Ia kunci sesi "
        "yang otomatis dibuat browser setelah Anda login. Jadi alurnya selalu tiga "
        "langkah: <i>login di portal → ambil token dari browser → kirim ke chat ini</i>.\n\n"
        "<b>0. Pastikan botnya sedang jalan</b>\n"
        "Bot menjawab hanya kalau prosesnya hidup. PC: <code>python run.py</code>. "
        "Server: <code>systemctl status monev-bot</code> harus <code>active (running)</code>.\n\n"
        "<b>1. Ambil tokennya (Chrome/Edge di PC)</b>\n"
        "a. Buka <code>monev.maganghub.kemnaker.go.id</code>, login seperti biasa.\n"
        "b. Tekan <code>F12</code> → tab <code>Console</code>.\n"
        "c. Kalau muncul peringatan biru, ketik <code>allow pasting</code> lalu Enter.\n"
        "d. Tempel snippet “Cara A” dari <code>tools/bookmarklet.md</code>, tekan Enter.\n"
        "   Token otomatis masuk clipboard (tulisan ✅ muncul di console).\n"
        "e. Alternatif tanpa kode: F12 → tab <code>Network</code> → klik menu apa pun di "
        "portal → pilih request <code>daily-logs</code> → Headers → Request Headers → "
        "baris <code>authorization</code> → copy nilainya.\n\n"
        "<b>2. Kirim ke bot</b>\n"
        "Ketik <code>/token</code>, satu spasi, lalu Ctrl+V. Boleh juga tempel polos "
        "tanpa <code>/token</code>, dan awalan <code>Bearer</code> boleh ikut tersalin.\n\n"
        "<b>3. Cek</b>\n"
        "Balasan yang benar: “✅ Token tersimpan &amp; tervalidasi”. Lanjutkan "
        "<code>/status</code> — kalau keluar tabel tanggal, token aktif. "
        "<code>/tokeninfo</code> menunjukkan umur token yang tersimpan.\n\n"
        "⚠️ Umur token ±6 jam dan mati lagi begitu Anda login di perangkat lain, jadi "
        "mengisi ulang itu normal. Tidak perlu restart apa pun setelah kirim token.\n"
        "🔁 Mau berhenti kirim token selamanya? Ambil cookie <code>monev_refresh_token</code> "
        "(F12 → <code>Application</code> → <code>Cookies</code> → domain "
        "<code>monev-api.maganghub…</code>) lalu kirim <code>/cookie &lt;nilainya&gt;</code>. "
        "Setelah itu bot memperbarui tokennya sendiri.\n"
        "📱 Hanya punya HP? Pakai <code>firefox</code>/<code>Kiwi</code> yang punya mode "
        "desktop + console, atau kirimkan tokennya dari PC lain lewat Cara D di panduan."
    )


def token_expired_message(reason: str) -> str:
    return (
        "⚠️ <b>Token monev tidak berlaku lagi</b>\n"
        f"Penyebab: {esc(reason)}\n\n"
        "Cara isi ulang (±30 detik, tanpa SSH):\n"
        "1. Buka <code>monev.maganghub.kemnaker.go.id</code> dan login seperti biasa di browser.\n"
        "2. Tekan <code>F12</code> → tab <code>Console</code> → tempel snippet “Cara A” dari\n"
        "   <code>tools/bookmarklet.md</code> (di server: <code>/opt/monev-bot/tools/bookmarklet.md</code>)\n"
        "   → token otomatis tersalin ke clipboard.\n"
        "3. Paste di chat ini dengan awalan <code>/token</code> — atau tempel polos, bot\n"
        "   mengenalinya dari awalan <code>eyJ0eXAi</code>.\n"
        "4. Tunggu “✅ Token tersimpan &amp; tervalidasi”. Polling dan reminder lanjut sendiri,\n"
        "   tanpa restart service.\n\n"
        "Sambil menunggu, pantauan tanggal-tanggal sebelumnya tetap aman di database.\n"
        "Supaya ini tidak terulang: kirim cookie refresh — lihat <code>/cookie</code>."
    )


def token_warning_message(minutes_left: int) -> str:
    return (
        f"⏳ <b>Token mendekati kedaluwarsa</b>\n"
        f"Perkiraan sisa umur: {minutes_left} menit (sesi monev berlaku ±6 jam dan "
        "otomatis mati saat Anda login ulang).\n"
        "Siapkan token baru lewat <code>/token</code> supaya pantauan tidak bolong."
    )


def token_saved_message(masked: str, ttl_minutes: int, fingerprint: str) -> str:
    return (
        "✅ <b>Token tersimpan & tervalidasi</b>\n"
        f"Token: <code>{esc(masked)}</code>\n"
        f"Perkiraan umur: {ttl_minutes} menit\n"
        f"Fingerprint sesi: <code>{esc(fingerprint[:12] or '-')}</code>"
    )


def cookie_saved_message(masked: str, ttl_minutes: int) -> str:
    return (
        "🍪 <b>Cookie refresh tersimpan</b>\n"
        f"Cookie: <code>{esc(masked)}</code>\n"
        f"Token akses terakhir berlaku ±{ttl_minutes} menit, tapi sekarang bot yang "
        "memperbaruinya sendiri lewat <code>/api/v1/auth/refresh</code>.\n\n"
        "Mulai sekarang Anda tidak perlu mengirim token lagi — kecuali kalau sesi "
        "di akun Kemnaker Anda benar-benar berakhir (bot akan memberi tahu bila itu "
        "terjadi)."
    )


def cookie_missing_message() -> str:
    return (
        "🍪 <b>Belum ada cookie refresh tersimpan</b>\n"
        "Tanpa cookie, bot hanya bisa memantau sampai token aksesnya mati (±6 jam).\n\n"
        "Cara mengambilnya (Chrome/Edge, sekali saja):\n"
        "1. Login di <code>monev.maganghub.kemnaker.go.id</code>.\n"
        "2. Tekan <code>F12</code> → tab <code>Application</code> (Firefox: <code>Storage</code>).\n"
        "3. Kiri: <code>Cookies</code> → pilih <code>https://monev-api.maganghub.kemnaker.go.id</code>.\n"
        "4. Cari baris <code>monev_refresh_token</code> → klik kanan nilainya → <code>Copy value</code>.\n"
        "5. Kirim ke chat ini: <code>/cookie</code> lalu spasi lalu paste.\n\n"
        "Cookie itu httpOnly, jadi tidak muncul di Console/localStorage — hanya di "
        "panel Application atau header request."
    )


def refresh_cookie_dead_message(detail: str) -> str:
    return (
        "🔒 <b>Sesi monev benar-benar berakhir</b>\n"
        f"Penyebab: {esc(detail)}\n"
        "Bot masih punya cookie refresh, tapi server menolaknya — biasanya karena "
        "Anda logout, atau sesi akun Kemnaker sudah lewat umur maksimumnya.\n\n"
        "Yang perlu dilakukan (±1 menit):\n"
        "1. Login lagi di portal monev sampai halaman aktivitas harian terbuka.\n"
        "2. Ambil cookie <code>monev_refresh_token</code> yang baru (F12 → Application →\n"
        "   Cookies → <code>monev-api.maganghub…</code>) → kirim <code>/cookie &lt;nilai&gt;</code>.\n"
        "3. Sementara itu, token akses bisa langsung lewat <code>/token &lt;eyJ0eXAi…&gt;</code>.\n\n"
        "Setelah cookie baru masuk, pembaruan otomatis jalan lagi tanpa Anda sentuh."
    )


def token_unreachable_message(detail: str) -> str:
    """Dipakai saat portal tidak bisa dihubungi: bukan berarti token salah."""
    return (
        "⚠️ <b>Token belum bisa diverifikasi</b>\n"
        f"Portal tidak bisa dihubungi: {esc(detail)}\n"
        "Token tidak disimpan dan token lama tetap dipakai. Coba lagi sebentar; kalau "
        "masih gagal, cek koneksi server lewat <code>journalctl -u monev-bot</code>."
    )


def token_rejected_message(status: int | None, detail: str) -> str:
    return (
        "❌ <b>Token ditolak server</b>\n"
        f"HTTP: {status if status else '-'}\n"
        f"Detail: {esc(detail)}\n"
        "Token lama tidak tertimpa. Ambil ulang dari sesi yang masih terbuka: login di "
        "browser, lalu F12 → Console → snippet “Cara A” di <code>tools/bookmarklet.md</code>, "
        "kirim lagi <code>/token</code>."
    )


def status_table(
    today: str,
    today_label: Label,
    rows: Iterable[Any],
    header_note: str = "",
) -> str:
    lines = [f"📊 <b>Status absensi</b> — hari ini {pretty_date(today)}: {today_label.emoji} {esc(today_label.title)}"]
    if header_note:
        lines.append(f"<i>{esc(header_note)}</i>")
    lines.append("")
    any_row = False
    for row in rows:
        any_row = True
        label_key = row["label"] if not isinstance(row, dict) else row.get("label", "")
        label = _label_by_key(label_key)
        state = row["log_state"] if not isinstance(row, dict) else row.get("log_state")
        lines.append(
            f"{pretty_date(row['log_date'] if not isinstance(row, dict) else row.get('log_date', ''))} — "
            f"{label.emoji} {esc(label.title)} <i>({esc(state or '-')})</i>"
        )
    if not any_row:
        lines.append("Belum ada riwayat tersimpan. Jalankan /cek untuk mengisi.")
    return "\n".join(lines)


def _label_by_key(key: str) -> Label:
    from .. import status as st

    return st.ALL_LABELS.get(key, st.UNKNOWN)


def help_text(username: str) -> str:
    return (
        "🤖 <b>Bot Pengingat Absensi Maganghub</b>\n\n"
        "<b>Perintah</b>\n"
        "/status — status hari ini + riwayat 7 hari\n"
        "/cek — paksa cek sekarang (lapor perubahan)\n"
        "/token &lt;Bearer …&gt; — ganti token sesi monev\n"
        "/cookie &lt;monev_refresh_token&gt; — aktifkan perpanjangan token otomatis\n"
        "/tokeninfo — kondisi token & perkiraan umur\n"
        "/reminder on|off — hidupkan/matikan pengingat\n"
        "/interval &lt;menit&gt; — ubah frekuensi polling (min 5)\n"
        "/log [n] — n event terakhir (default 10)\n"
        "/panduan — cara ambil &amp; isi token monev (step by step)\n"
        "/help — pesan ini\n\n"
        "<b>Yang dipantau</b>: tiga isian harian portal — aktivitas, pembelajaran, "
        "kendala. Selama masih ada yang kosong atau belum dikirim, bot mengingatkan.\n"
        "Begitu lengkap bot lapor “Sudah Diisi”, lalu kabari juga kalau status "
        "berubah (menunggu ACC, di-ACC, diminta revisi, ditolak).\n\n"
        f"Token bisa juga dikirim tanpa perintah: cukup tempel string <code>eyJ0eXAi…</code>.\n"
        f"Bot hanya membaca portal, tidak mengisi laporan."
    )


def announcement_message(title: str, content: str, kind: str) -> str:
    badge = "📢" if kind.upper() == "INFO" else "⚠️"
    body = content if len(content) <= 1200 else content[:1200] + "…"
    return f"{badge} <b>{esc(title)}</b>\n\n{esc(body)}"


def warning_message(text: str) -> str:
    return f"🚩 <b>Warning keterlambatan absensi</b>\n{esc(text)}"


def unknown_status_message(label_raw: str, log_date: str) -> str:
    return (
        "❔ <b>Status belum dikenal bot</b>\n"
        f"Tanggal: {pretty_date(log_date)}\n"
        f"Klaim API: <code>{esc(label_raw)}</code>\n"
        "Silakan cek portal; mapping bot perlu dikalibrasi bila ini status baru."
    )
