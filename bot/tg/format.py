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


def token_expired_message(reason: str) -> str:
    return (
        "⚠️ <b>Token monev tidak berlaku lagi</b>\n"
        f"Penyebab: {esc(reason)}\n\n"
        "Buka portal monev di browser (login seperti biasa), lalu kirim token baru ke bot:\n"
        "<code>/token &lt;Bearer …&gt;</code>\n"
        "atau cukup tempel token yang dimulai dengan <code>eyJ0eXAi…</code>.\n"
        "Polling otomatis lanjut lagi begitu token valid."
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


def token_rejected_message(status: int | None, detail: str) -> str:
    return (
        "❌ <b>Token ditolak server</b>\n"
        f"HTTP: {status if status else '-'}\n"
        f"Detail: {esc(detail)}\n"
        "Pastikan token berasal dari sesi monev yang masih terbuka (bukan token lama)."
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
        "/tokeninfo — kondisi token & perkiraan umur\n"
        "/reminder on|off — hidupkan/matikan pengingat\n"
        "/interval &lt;menit&gt; — ubah frekuensi polling (min 5)\n"
        "/log [n] — n event terakhir (default 10)\n"
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
