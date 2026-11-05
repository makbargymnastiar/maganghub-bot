"""Kalimat untuk respons perintah chat: /help, /start, /status, /cek, /log, error."""

from __future__ import annotations

from typing import Any, Iterable

from ..monev.status import ALL_LABELS, Label
from .base import day_name, esc, numeric_date, pretty_date

# Jadwal berjenjang ditampilkan apa adanya — memang tidak bisa diubah dari chat.
REMINDER_SCHEDULE = (
    "🌅 08:00 pengingat pagi logbook · 🕠 16:55 cek sore · 🔔 18:00–20:59 tiap 30 mnt · "
    "⏰ 21:00–23:29 tiap 15 mnt · 🚨 23:30–23:45 tiap 5 mnt · 🆘 23:46–23:59 tiap 3 mnt"
)
MORNING_SCHEDULE = (
    "🗓️ 08:00 info libur · ⏳ tagihan ACC ≥5 hari · 📊 pengingat evaluasi H-7..H-1 (tgl 2–8) · "
    "📊 hari-H evaluasi · 💸 uang saku (tgl 9)"
)

# Nama & emoji tampilan /status dan /rekap per template review user (2026-11-05).
# Label internal yang tidak disebut di sini tetap pakai judul aslinya di status.py.
STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "APPROVED": ("✅", "Disetujui"),
    "EMPTY": ("⚠️", "Belum Isi Absensi"),
    "ATTENDED_NO_LOG": ("⚠️", "Belum Isi Absensi"),
    "PENDING_REVIEW": ("⏳", "Menunggu ACC Mentor"),
    "NEEDS_REVISION": ("🟠", "Perlu Revisi"),
    "HOLIDAY": ("🗓️", "Hari Libur"),
}


def _display(label: Label) -> tuple[str, str]:
    """(emoji, judul) versi chat — sesuai template, fallback ke label umum."""
    return STATUS_DISPLAY.get(label.key, (label.emoji, label.title))


def help_text(username: str) -> str:
    return (
        "🤖 <b>Bot Pengingat Absensi Maganghub</b>\n\n"
        "<b>Perintah</b>\n"
        "/status — status absensi hari ini\n"
        "/rekap — riwayat absensi 5 hari terakhir\n"
        "/cek — paksa cek sekarang (lapor perubahan)\n"
        "/token &lt;Bearer …&gt; — ganti token sesi monev\n"
        "/cookie &lt;monev_refresh_token&gt; — aktifkan perpanjangan token otomatis\n"
        "/tokeninfo — kondisi token &amp; perkiraan umur\n"
        "/interval &lt;menit&gt; — ubah frekuensi polling (min 5)\n"
        "/log [n] — n event terakhir (default 10)\n"
        "/panduan — cara ambil &amp; isi token monev (step by step)\n"
        "/daftar &lt;participant_id&gt; — daftar sendiri (token bisa langsung dibaca otomatis)\n"
        "/evaluasi [YYYY-MM] — hasil evaluasi mentor (bintang + komentar)\n"
        "/stop — hentikan pemantauan akun ini (data tetap tersimpan)\n"
        "/lanjut — aktifkan lagi pemantauan setelah /stop\n"
        "/help — pesan ini\n\n"
        "<b>Jadwal pengingat otomatis (terkunci, tidak bisa diubah/dimatikan dari chat)</b>\n"
        f"{REMINDER_SCHEDULE}\n{MORNING_SCHEDULE}\n\n"
        "<b>Yang dipantau</b>: tiga isian harian portal — aktivitas, pembelajaran, "
        "kendala. Selama masih ada yang kosong, bot mengingatkan.\n"
        "Setiap perubahan status dilaporkan lengkap dengan catatan mentor "
        "(menunggu ACC, di-ACC, diminta revisi, ditolak).\n\n"
        "Token bisa juga dikirim tanpa perintah: cukup tempel string <code>eyJ0eXAi…</code>.\n"
        "Bot hanya membaca portal, tidak mengisi laporan."
    )


def start_card(
    chat_id: str,
    participant_id: str,
    base_url: str,
    poll_minutes: int,
    tz_name: str,
    token_ok: bool,
    refresh_ok: bool,
) -> str:
    status_token = "✅ Tersimpan 🔒" if token_ok else "❌ Belum ada — kirim /token"
    status_cookie = "✅ Aktif" if refresh_ok else "❌ Belum — kirim /cookie"
    participant = (
        f"<code>{esc(participant_id[:8])}…</code>" if participant_id else "belum terbaca — tempel token atau /daftar"
    )
    return (
        "🤖 <b>Bot pengingat absensi Maganghub aktif</b>\n"
        f"Chat: <code>{esc(chat_id)}</code>\n"
        f"Participant: {participant}\n"
        f"Base URL: {esc(base_url)}\n\n"
        f"⏱️ <b>Konfigurasi Jadwal</b> ({esc(tz_name)}):\n"
        f"• Polling API: Tiap {poll_minutes} menit\n"
        "• Cek Sore: 16:55\n"
        "• Pengingat Berjenjang: 18:00–23:59\n"
        "• Info Pagi: 08:00\n\n"
        "🔒 <b>Status Sesi Auth</b>:\n"
        f"• Token: {status_token}\n"
        f"• Auto-refresh: {status_cookie}\n\n"
        "Ketik /help untuk daftar perintah."
    )


def welcome_message() -> str:
    """Sambutan pertama chat yang belum pernah mendaftar (tombol "Mulai" / /start).

    Daftar fitur harus tetap mencerminkan yang benar-benar dikerjakan scheduler —
    kalau ada fitur baru, tambahkan pointernya di sini, bukan sekadar janji.
    """
    return (
        "👋 <b>Selamat Datang di Bot Absensi MagangHub!</b>\n\n"
        "Bot ini siap bantu ingatkan logbook harian\n"
        "&amp; pantau status ACC mentor kamu secara otomatis.\n\n"
        "✨ <b>Fitur Utama Bot:</b>\n"
        "• 🔔 <b>Pengingat Logbook Harian</b>\n"
        "  (Paket pagi 08:00, cek sore 16:55, berjenjang 18:00–23:59)\n"
        "• 📊 <b>Cek Status &amp; Riwayat</b>\n"
        "  (/status hari ini, /rekap 5 hari terakhir, /cek paksa sekarang)\n"
        "• 💬 <b>Notifikasi Mentor Otomatis</b>\n"
        "  (Update instan saat di-ACC, diminta revisi, atau ada catatan mentor)\n"
        "• 🔑 <b>Auto-Refresh Sesi</b>\n"
        "  (Isi /cookie sekali — token diperpanjang sendiri sebelum mati)\n"
        "• 📈 <b>Pemantauan Evaluasi Mentor</b>\n"
        "  (Hasil evaluasi bintang keluar → notifikasi + rincian 8 poin)\n"
        "• 💸 <b>Pengingat Uang Saku &amp; Evaluasi</b>\n"
        "  (Info libur, pengingat H-7 evaluasi, hari-H tanggal 9)\n"
        "• 📢 <b>Pengumuman &amp; Warning Portal</b>\n"
        "  (Pengumuman monev dan peringatan keterlambatan ikut terkirim)\n\n"
        "📌 <b>Langkah Awal Penggunaan:</b>\n"
        "1. Ambil token dari portal monev MagangHub.\n"
        "2. Kirim ke chat ini dengan format:\n"
        "   <code>/token &lt;Bearer …&gt;</code>\n"
        "3. Atau kirim <code>/cookie &lt;refresh_token&gt;</code>\n"
        "   supaya token diperbarui otomatis!\n\n"
        "💡 Ketik /panduan untuk cara ambil token.\n"
        "💡 Ketik /help untuk daftar perintah lengkap.\n\n"
        "<i><code>participant_id</code> kamu terbaca otomatis dari token — tidak perlu "
        "mengetik ID apa pun.</i>"
    )


def status_today(today: str, label: Label, header_note: str = "") -> str:
    """/status — hanya status hari ini (riwayat pindah ke /rekap)."""
    emoji, title = _display(label)
    lines = [
        "📊 <b>STATUS ABSENSI HARI INI</b>",
        f"📅 {pretty_date(today)}",
        f"Status: {emoji} {esc(title)}",
    ]
    if header_note:
        lines.append(f"⚠️ <i>{esc(header_note)}</i>")
    return "\n".join(lines)


def rekap_history(rows: Iterable[Any]) -> str:
    """/rekap — riwayat 5 hari terakhir: '- Kamis, 05/11 — ✅ Disetujui'."""
    lines = ["📜 <b>RIWAYAT ABSENSI MAGANGHUB</b>"]
    any_row = False
    for row in rows:
        any_row = True
        emoji, title = _display(_label_by_key(row["label"]))
        day = row["log_date"]
        lines.append(f"- {day_name(day)}, {numeric_date(day)} — {emoji} {esc(title)}")
    if not any_row:
        lines.append("Belum ada riwayat tersimpan. Jalankan /cek untuk mengisi.")
    return "\n".join(lines)


def _label_by_key(key: str) -> Label:
    return ALL_LABELS.get(key, ALL_LABELS["UNKNOWN"])


def log_list_message(rows: list[Any]) -> str:
    lines = ["📜 <b>Event terakhir</b>"]
    for row in rows:
        detail = row["message"] or row["to_label"]
        lines.append(
            f"{row['created_at'][8:10]}/{row['created_at'][5:7]} "
            f"{row['created_at'][11:16]} — {esc(detail)}"
        )
    return "\n".join(lines)


def log_empty_message() -> str:
    return "Belum ada event."


def not_jwt_message() -> str:
    return "❌ Itu bukan token JWT monev. Format: <code>/token Bearer eyJ0eXAi…</code>"


def token_active_hint(date_hint: str) -> str:
    return f"🔑 Token aktif 🔒. Portal melaporkan tanggal <b>{esc(date_hint)}</b>."


def cek_no_token_message() -> str:
    return (
        "⚠️ Belum ada token. Kirim <code>/cookie &lt;monev_refresh_token&gt;</code> "
        "(biar otomatis) atau <code>/token &lt;Bearer …&gt;</code>."
    )


def cek_baseline_message(count: int, today_label: str) -> str:
    return (
        f"🌱 Baseline {count} tanggal tersimpan (tanpa notifikasi spam).\n"
        f"Hari ini: <b>{esc(today_label)}</b>"
    )


def cek_no_change_message(label: Label) -> str:
    """(C) /cek tanpa perubahan — header cek + status hari ini lengkap dengan emoji."""
    emoji, title = _display(label)
    return (
        "🔍 <b>CEK PORTAL MONEV</b>\n\n"
        "ℹ️ Tidak ada perubahan status.\n"
        f"Status hari ini: {emoji} {esc(title)}"
    )


def cek_changes_message(changes: list[tuple[str, str]]) -> str:
    lines = [f"🔄 {len(changes)} perubahan tercatat:"]
    for day, key in changes:
        label = ALL_LABELS.get(key)
        lines.append(f"- {pretty_date(day)} → {esc(label.title if label else key)}")
    return "\n".join(lines)


def heal_failed_message() -> str:
    return "⚠️ Token ditolak server dan perpanjangan otomatis gagal. Cek <code>/tokeninfo</code>."


def api_error_message(detail: str) -> str:
    return f"❌ Gagal memanggil API: {esc(detail)}"


def unknown_command_message() -> str:
    return "Perintah tidak dikenal. Coba /help."


def foreign_chat_message(chat_id: str, snippet: str) -> str:
    return f"🚫 Ada pesan dari chat asing <code>{esc(chat_id)}</code>: {esc(snippet)}"


# --- registrasi multi-user ----------------------------------------------------

def invite_required_message() -> str:
    """Pendaftaran sedang dikunci kode undangan oleh owner."""
    return (
        "🔒 Pemilik bot menutup pendaftaran mandiri.\n"
        "Minta <b>kode undangan</b> ke dia, lalu kirim:\n"
        "<code>/daftar &lt;kode&gt; &lt;participant_id&gt;</code>"
    )


def invite_missing_message(invite_required: bool = False) -> str:
    """Format /daftar — berbeda tergantung kode undangan wajib atau tidak."""
    if invite_required:
        return (
            "⌨️ Format pendaftaran:\n"
            "<code>/daftar &lt;kode undangan&gt; &lt;participant_id&gt;</code>"
        )
    return (
        "⌨️ Format pendaftaran:\n"
        "<code>/daftar &lt;participant_id&gt;</code>\n\n"
        "ID ada di URL portal monev Anda; atau lebih mudah — langsung tempel token "
        "sesi saja, bot yang membaca ID-nya (/panduan untuk cara ambil token)."
    )


def invite_wrong_message() -> str:
    return "⛔ Kode undangan tidak cocok. Pendaftaran ditolak."


def quota_full_message(limit: int) -> str:
    return (
        f"🚦 Kuota pengguna bot sudah penuh ({limit} akun).\n"
        "Hubungi pemilik bot untuk menaikkan <code>MAX_USERS</code> atau melepas "
        "pengguna yang sudah tidak aktif."
    )


def participant_id_needed_message() -> str:
    """Token tersimpan tapi portal tidak memberi ID — minta user mengirimnya."""
    return (
        "🔑 Token tersimpan 🔒, tapi portal tidak menyebutkan \"participant_id\" "
        "secara jelas, jadi pemantauan belum bisa dimulai.\n\n"
        "Kirim satu baris ini saja:\n"
        "<code>/daftar &lt;participant_id Anda&gt;</code>\n\n"
        "(ID ada di URL portal saat membuka halaman aktivitas harian.)"
    )


def already_registered_message(participant_id: str) -> str:
    return (
        "ℹ️ Chat ini sudah terdaftar.\n"
        f"Participant: <code>{esc(participant_id[:8])}…</code>\n"
        "Ketik /status untuk melihat kondisi hari ini, atau /help untuk semua perintah."
    )


def participant_mismatch_message(claimed: str) -> str:
    """Dikirim kalau token yang dipakai tidak milik participant_id yang diklaim."""
    return (
        "❌ <b>Verifikasi gagal</b>\n"
        f"Token yang bot punya tidak cocok dengan participant_id "
        f"<code>{esc(claimed[:8])}…</code>.\n"
        "Kemungkinan: token milik akun lain, atau participant_id salah ketik. "
        "Kirim ulang <code>/daftar</code> dengan ID yang benar, atau kirim "
        "<code>/token</code> dari akun Anda sendiri."
    )


def registration_pending_message(participant_id: str) -> str:
    return (
        "📝 <b>Pendaftaran dicatat, tinggal satu langkah</b>\n"
        f"Participant: <code>{esc(participant_id[:8])}…</code>\n\n"
        "Bot perlu token sesi Anda untuk memverifikasi. Kirim "
        "<code>/cookie &lt;monev_refresh_token&gt;</code> (disarankan, otomatis) "
        "atau <code>/token &lt;Bearer …&gt;</code>. Cara mengambilnya: /panduan.\n\n"
        "Pemantauan baru jalan setelah token Anda terverifikasi."
    )


def registration_done_message(participant_id: str, poll_minutes: int) -> str:
    return (
        "🎉 <b>Akun terhubung &amp; tervalidasi</b>\n"
        f"Participant: <code>{esc(participant_id[:8])}…</code>\n"
        f"⏱️ Pemantauan status: tiap {poll_minutes} menit\n\n"
        "Pengingat harian (08:00, 16:55, 18:00–23:59) mulai berjalan untuk akun ini.\n"
        "Ketik /status untuk cek sekarang, /help untuk semua perintah."
    )


def unverified_reminder_message() -> str:
    return (
        "⚠️ Akun ini belum terverifikasi — pemantauan belum berjalan.\n"
        "Kirim <code>/cookie &lt;monev_refresh_token&gt;</code> atau "
        "<code>/token &lt;Bearer …&gt;</code>. Panduan: /panduan"
    )


def stopped_message() -> str:
    return (
        "🛑 Pemantauan dihentikan untuk chat ini. Data tetap tersimpan.\n"
        "Mulai lagi: <code>/lanjut</code>"
    )


def resumed_message() -> str:
    return "▶️ Pemantauan diaktifkan lagi."


def owner_notify_new_user(chat_id: str, name: str, participant_id: str, approved: bool) -> str:
    hasil = "✅ terverifikasi" if approved else "⚠️ menunggu token"
    who = f" ({esc(name)})" if name else ""
    return (
        f"👤 Pengguna baru <code>{esc(chat_id)}</code>{who}\n"
        f"Participant: <code>{esc(participant_id[:8])}…</code> — {hasil}"
    )


def handler_error_message(detail: str) -> str:
    return f"❌ Terjadi kesalahan: {esc(detail)}"
