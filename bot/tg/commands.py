"""Perintah Telegram.

Single-user: hanya `config.TELEGRAM_CHAT_ID` yang dilayani. Chat lain ditolak
dan pemiliknya dicatat di log (siap dibuka jadi multi-user lewat tabel `users`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import aiosqlite

from .. import store
from ..api import endpoints
from ..api.client import MonevClient, MonevError, TokenRejected
from ..api.jwt_utils import fingerprint, looks_like_jwt, session_user, ttl_seconds
from ..jobs import token_health
from ..jobs.poll import PollResult, run_poll
from ..models import User
from ..settings import Settings, mask_token
from ..status import ALL_LABELS
from . import format as fmt
from .api import TelegramClient

log = logging.getLogger("bot.commands")


@dataclass
class BotContext:
    db: aiosqlite.Connection
    settings: Settings
    tg: TelegramClient
    last_result: PollResult | None = None
    poll_minutes: int = 15
    _user: User | None = field(default=None, repr=False)

    async def user(self) -> User:
        if self._user is None:
            self._user = await store.ensure_user(self.db, self.settings)
        return self._user

    def refresh_user_cache(self, user: User) -> None:
        self._user = user

    def client_for(self, user: User) -> MonevClient:
        return MonevClient(self.settings.base_url, user.bearer_token)


async def apply_token(ctx: BotContext, raw_token: str) -> str:
    """Simpan token setelah divalidasi ke API. Kembalikan pesan balasan (HTML)."""
    token = raw_token.strip().removeprefix("Bearer ").strip()
    if not looks_like_jwt(token):
        return "❌ Itu bukan token JWT monev. Format: <code>/token Bearer eyJ0eXAi…</code>"

    user = await ctx.user()
    probe = MonevClient(ctx.settings.base_url, token)
    try:
        home = await endpoints.fetch_home(probe)
    except TokenRejected as exc:
        return fmt.token_rejected_message(exc.status, str(exc))
    except MonevError as exc:
        return fmt.token_rejected_message(exc.status, str(exc))
    finally:
        await probe.close()

    now = ctx.settings.now()
    await store.update_token(
        ctx.db, user.tg_chat_id, token, fingerprint(token), ttl_seconds(token), now
    )
    fresh = await store.get_user(ctx.db, user.tg_chat_id)
    if fresh:
        ctx.refresh_user_cache(fresh)
    await store.add_event(
        ctx.db, user.tg_chat_id, now.date().isoformat(), "", "", store.KIND_TOKEN, "token diperbarui", now
    )
    today = now.date().isoformat()
    minutes = ttl_seconds(token) // 60
    await ctx.tg.send_message(
        user.tg_chat_id,
        fmt.token_saved_message(mask_token(token), minutes, fingerprint(token)),
    )
    # token baru -> flag peringatan lama boleh diulang untuk hari berikutnya
    await ctx.db.execute(
        "DELETE FROM notify_flags WHERE user_id = ? AND flag IN ('token_warning', 'token_alert')",
        (user.tg_chat_id,),
    )
    await ctx.db.commit()
    date_hint = home.get("date", today)
    return f"🔑 Token aktif. Portal melaporkan tanggal <b>{fmt.esc(date_hint)}</b>."


async def cmd_cek(ctx: BotContext, args: str = "") -> str:
    user = await ctx.user()
    if not user.bearer_token:
        return "⚠️ Belum ada token. Kirim <code>/token &lt;Bearer …&gt;</code> dulu."
    client = ctx.client_for(user)
    try:
        result = await run_poll(ctx.db, ctx.settings, user, client, notify=ctx.tg)
        ctx.last_result = result
    except TokenRejected as exc:
        await token_health.mark_token_invalid(ctx.db, user, ctx.tg, str(exc), ctx.settings.now())
        fresh = await store.get_user(ctx.db, user.tg_chat_id)
        if fresh:
            ctx.refresh_user_cache(fresh)
        return "⚠️ Token ditolak server, sudah dikirim instruksi perbarui token."
    except MonevError as exc:
        await store.add_event(
            ctx.db, user.tg_chat_id, "", "", "", store.KIND_ERROR, str(exc), ctx.settings.now()
        )
        return f"❌ Gagal memanggil API: {fmt.esc(exc)}"
    finally:
        await client.close()

    fresh = await store.get_user(ctx.db, user.tg_chat_id)
    if fresh and fresh.token_invalid:
        await token_health.mark_token_valid(ctx.db, fresh, ctx.settings.now())
        await store.set_token_invalid(ctx.db, user.tg_chat_id, False)
        ctx.refresh_user_cache(fresh)

    if result.seeded:
        return (
            f"🌱 Baseline {len(result.notes)} tanggal tersimpan (tanpa notifikasi spam).\n"
            f"Hari ini: <b>{fmt.esc(result.today_label.title if result.today_label else '-')}</b>"
        )
    if not result.changes:
        label = result.today_label.title if result.today_label else "-"
        return f"✅ Tidak ada perubahan. Status hari ini: <b>{fmt.esc(label)}</b>"
    lines = [f"🔄 {len(result.changes)} perubahan tercatat:"]
    for day, key in result.changes:
        lines.append(f"• {fmt.pretty_date(day)} → {fmt.esc(ALL_LABELS.get(key).title if ALL_LABELS.get(key) else key)}")
    return "\n".join(lines)


async def cmd_status(ctx: BotContext, args: str = "") -> str:
    user = await ctx.user()
    now = ctx.settings.now()
    today = now.date().isoformat()
    since = (now.date() - timedelta(days=7)).isoformat()
    rows = await store.list_day_states(ctx.db, user.tg_chat_id, since, limit=10)
    current = next((r for r in rows if r["log_date"] == today), None)
    label = ALL_LABELS.get(current["label"]) if current else ALL_LABELS["EMPTY"]
    note = ""
    if user.token_invalid:
        note = "Token tidak aktif — data di bawah mungkin basi. Kirim /token baru."
    return fmt.status_table(today, label, rows, note)


async def cmd_tokeninfo(ctx: BotContext, args: str = "") -> str:
    user = await ctx.user()
    claims_user = session_user(user.bearer_token)
    left = token_health.minutes_left(user, ctx.settings.now())
    lines = [
        "🔎 <b>Info token</b>",
        f"Token: <code>{fmt.esc(mask_token(user.bearer_token))}</code>",
        f"Status: {'❌ ditolak server' if user.token_invalid else '✅ aktif'}",
        f"Fingerprint: <code>{fmt.esc(user.token_fingerprint[:12] or '-')}</code>",
        f"user claim: <code>{fmt.esc(claims_user[:8] or '-')}</code>",
        f"Umur tersimpan: {user.token_ttl_seconds // 3600} jam",
    ]
    if user.token_saved_at:
        lines.append(f"Disimpan: {fmt.esc(user.token_saved_at.strftime('%d-%m-%Y %H:%M'))} WIB")
    if left is not None:
        lines.append(f"Perkiraan sisa: {left} menit")
    else:
        lines.append("Perkiraan sisa: tidak diketahui (token dari config.py sebelum migrasi)")
    return "\n".join(lines)


async def cmd_reminder(ctx: BotContext, args: str) -> str:
    user = await ctx.user()
    arg = args.strip().lower()
    if arg in ("on", "off"):
        on = arg == "on"
        await store.set_reminders_on(ctx.db, user.tg_chat_id, on)
        fresh = await store.get_user(ctx.db, user.tg_chat_id)
        if fresh:
            ctx.refresh_user_cache(fresh)
        return f"🔔 Reminder di{'hidupkan' if on else 'matikan'}."
    return (
        "Pemakaian: <code>/reminder on</code> atau <code>/reminder off</code>\n"
        f"Jadwal saat ini: sore {ctx.settings.reminder_evening:%H:%M}, "
        f"lanjutan {ctx.settings.reminder_followup:%H:%M}, darurat {ctx.settings.reminder_emergency:%H:%M} "
        f"({fmt.esc(ctx.settings.tz_name)})"
    )


async def cmd_interval(ctx: BotContext, args: str) -> str:
    arg = args.strip()
    if not arg.isdigit():
        return f"Pemakaian: <code>/interval 15</code> (saat ini {ctx.poll_minutes} menit, minimum 5)"
    minutes = max(5, int(arg))
    ctx.poll_minutes = minutes
    await store.set_setting(ctx.db, "poll_minutes", str(minutes), ctx.settings.now())
    return f"⏱️ Interval polling diubah menjadi {minutes} menit (aktif mulai siklus berikutnya)."


async def cmd_log(ctx: BotContext, args: str) -> str:
    user = await ctx.user()
    limit = int(args.strip()) if args.strip().isdigit() else 10
    rows = await store.recent_events(ctx.db, user.tg_chat_id, min(limit, 30))
    if not rows:
        return "Belum ada event."
    lines = ["📜 <b>Event terakhir</b>"]
    for row in rows:
        created = row["created_at"][11:16]
        detail = row["message"] or row["to_label"]
        lines.append(f"{row['created_at'][8:10]}/{row['created_at'][5:7]} {created} — {fmt.esc(detail)}")
    return "\n".join(lines)


async def cmd_start(ctx: BotContext, args: str = "") -> str:
    user = await ctx.user()
    return (
        "🤖 <b>Bot pengingat absensi Maganghub aktif</b>\n"
        f"Chat: <code>{fmt.esc(user.tg_chat_id)}</code>\n"
        f"Participant: <code>{fmt.esc(user.participant_id[:8])}…</code>\n"
        f"Base URL: {fmt.esc(ctx.settings.base_url)}\n"
        f"Polling: {ctx.poll_minutes} menit · Reminder: {ctx.settings.reminder_evening:%H:%M}/"
        f"{ctx.settings.reminder_followup:%H:%M}/{ctx.settings.reminder_emergency:%H:%M} "
        f"{fmt.esc(ctx.settings.tz_name)}\n"
        f"Token: {'✅ tersimpan' if user.bearer_token else '❌ belum ada — kirim /token'}\n\n"
        "Ketik /help untuk daftar perintah."
    )


HANDLERS: dict[str, Any] = {
    "start": cmd_start,
    "status": cmd_status,
    "cek": cmd_cek,
    "tokeninfo": cmd_tokeninfo,
    "reminder": cmd_reminder,
    "interval": cmd_interval,
    "log": cmd_log,
}


async def dispatch(ctx: BotContext, chat_id: str, text: str) -> None:
    """Satu pesan masuk → satu balasan. Hanya chat terdaftar yang dilayani."""
    text = (text or "").strip()
    if not text:
        return

    registered = await store.get_user(ctx.db, chat_id)
    if registered is None or str(chat_id) != str(ctx.settings.telegram_chat_id):
        log.info("pesan dari chat tidak terdaftar: %s", chat_id)
        await ctx.tg.send_message(
            ctx.settings.telegram_chat_id,
            f"🚫 Ada pesan dari chat asing <code>{fmt.esc(chat_id)}</code>: {fmt.esc(text[:80])}",
        )
        return

    if looks_like_jwt(text) and not text.startswith("/"):
        await ctx.tg.send_message(chat_id, await apply_token(ctx, text))
        return

    command, _, args = text.partition(" ")
    name = command.lstrip("/").split("@", 1)[0].lower()

    if name == "token":
        await ctx.tg.send_message(chat_id, await apply_token(ctx, args))
        return
    if name == "help":
        await ctx.tg.send_message(chat_id, fmt.help_text(""))
        return

    handler = HANDLERS.get(name)
    if handler is None:
        await ctx.tg.send_message(chat_id, "Perintah tidak dikenal. Coba /help.")
        return

    try:
        await ctx.tg.send_message(chat_id, await handler(ctx, args))
    except Exception as exc:  # noqa: BLE001 - balasan error jangan sampai mematikan loop
        log.exception("handler %s gagal", name)
        await ctx.tg.send_message(
            chat_id,
            f"❌ Terjadi kesalahan: {fmt.esc(f'{exc.__class__.__name__}: {exc}')}"
        )
