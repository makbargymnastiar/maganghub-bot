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
from ..jobs import token_health
from ..jobs.poll import PollResult, run_poll
from ..messages import chat, sesi
from ..messages.base import esc
from ..monev import endpoints
from ..monev.client import MonevClient, MonevError, TokenRejected
from ..monev.jwt_utils import fingerprint, looks_like_jwt, session_user, ttl_seconds
from ..monev.status import ALL_LABELS
from ..models import User
from ..settings import Settings, mask_token, normalize_cookie
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
        # Cookie refresh ikut dibawa supaya `keepalive()` bisa memanggil
        # /auth/refresh dengan kredensial yang tersimpan di DB, bukan dari user.
        return MonevClient(
            self.settings.base_url, user.bearer_token, refresh_cookie=user.refresh_cookie
        )


async def poll_auto(ctx: BotContext) -> PollResult:
    """Satu siklus polling dengan pemulihan otomatis.

    Kalau server menolak token akses, bot coba refresh lewat cookie lebih dulu dan
    mengulang sekali. TokenRejected hanya naik kalau auto-refresh tidak mungkin
    atau tetap ditolak — itu satu-satunya saat user perlu bertindak.
    """
    user = await ctx.user()
    if not user.bearer_token:
        raise MonevError("belum ada token — kirim /cookie atau /token lebih dulu")
    client = ctx.client_for(user)
    try:
        try:
            return await _poll_once(ctx, user, client)
        except TokenRejected:
            fresh = await token_health.keepalive(
                ctx.db, ctx.settings, user, client, ctx.tg, force=True
            )
            if fresh is None:
                raise
            ctx.refresh_user_cache(fresh)
            return await _poll_once(ctx, fresh, client)
    finally:
        await client.close()


async def _poll_once(ctx: BotContext, user: User, client: MonevClient) -> PollResult:
    result = await run_poll(ctx.db, ctx.settings, user, client, notify=ctx.tg)
    if user.token_invalid:
        await token_health.mark_token_valid(ctx.db, user, ctx.settings.now())
        fresh = await store.get_user(ctx.db, user.tg_chat_id)
        ctx.refresh_user_cache(fresh or user)
    return result


async def apply_token(ctx: BotContext, raw_token: str) -> str:
    """Simpan token setelah divalidasi ke API. Kembalikan pesan balasan (HTML)."""
    if not (raw_token or "").strip():
        # /token tanpa isi = user lupa menempel tokennya; kirim panduannya
        return sesi.token_guide_text()
    token = raw_token.strip().removeprefix("Bearer ").strip()
    if not looks_like_jwt(token):
        return chat.not_jwt_message()

    user = await ctx.user()
    probe = MonevClient(ctx.settings.base_url, token)
    try:
        home = await endpoints.fetch_home(probe)
    except TokenRejected as exc:
        return sesi.token_rejected_message(exc.status, str(exc))
    except MonevError as exc:
        # status None = portal tidak bisa dihubungi, bukan token salah
        if exc.status is None:
            return sesi.token_unreachable_message(str(exc))
        return sesi.token_rejected_message(exc.status, str(exc))
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
    minutes = ttl_seconds(token) // 60
    await ctx.tg.send_message(
        user.tg_chat_id,
        sesi.token_saved_message(mask_token(token), minutes, fingerprint(token)),
    )
    # token baru -> flag peringatan lama boleh diulang untuk hari berikutnya
    await ctx.db.execute(
        "DELETE FROM notify_flags WHERE user_id = ? AND flag IN ('token_warning', 'token_alert')",
        (user.tg_chat_id,),
    )
    await ctx.db.commit()
    date_hint = home.get("date", now.date().isoformat())
    return chat.token_active_hint(date_hint)


async def apply_cookie(ctx: BotContext, raw_cookie: str) -> str:
    """Simpan cookie refresh setelah dites nyata ke /auth/refresh.

    Cookie tidak bisa diperiksa bentuknya seperti JWT, jadi satu-satunya validasi
    adalah memanggil endpointnya. Kalau ditolak, cookie tidak disimpan supaya
    konfigurasi lama tidak rusak.
    """
    if not (raw_cookie or "").strip():
        return sesi.cookie_missing_message()

    user = await ctx.user()
    probe = MonevClient(ctx.settings.base_url, user.bearer_token)
    probe.set_refresh_cookie(normalize_cookie(raw_cookie))
    try:
        build_id = await endpoints.fetch_frontend_build_id(probe, ctx.settings.web_url)
        token, rotated = await endpoints.refresh_access_token(probe, build_id=build_id)
    except TokenRejected as exc:
        return sesi.refresh_cookie_dead_message(str(exc))
    except MonevError as exc:
        if exc.status is None:
            return sesi.token_unreachable_message(str(exc))
        return sesi.refresh_cookie_dead_message(str(exc))
    finally:
        await probe.close()

    now = ctx.settings.now()
    cookie = normalize_cookie(f"{probe.cookie_name}={rotated or probe.refresh_cookie}")
    await store.update_refresh_cookie(ctx.db, user.tg_chat_id, cookie, now)
    await store.update_token(
        ctx.db, user.tg_chat_id, token, fingerprint(token), ttl_seconds(token), now
    )
    await store.set_token_invalid(ctx.db, user.tg_chat_id, False)
    fresh = await store.get_user(ctx.db, user.tg_chat_id)
    if fresh:
        ctx.refresh_user_cache(fresh)
    await store.add_event(
        ctx.db, user.tg_chat_id, now.date().isoformat(), "", "", store.KIND_TOKEN,
        "cookie refresh disimpan, token diperbarui", now,
    )
    await ctx.db.execute(
        "DELETE FROM notify_flags WHERE user_id = ? AND flag IN ('token_warning', 'token_alert', 'refresh_failed')",
        (user.tg_chat_id,),
    )
    await ctx.db.commit()
    return sesi.cookie_saved_message(mask_token(cookie.split("=", 1)[-1]), ttl_seconds(token) // 60)


async def cmd_cek(ctx: BotContext, args: str = "") -> str:
    user = await ctx.user()
    if not user.bearer_token:
        return chat.cek_no_token_message()
    try:
        result = await poll_auto(ctx)
    except TokenRejected as exc:
        await token_health.mark_token_invalid(ctx.db, user, ctx.tg, str(exc), ctx.settings.now())
        fresh = await store.get_user(ctx.db, user.tg_chat_id)
        if fresh:
            ctx.refresh_user_cache(fresh)
        return chat.heal_failed_message()
    except MonevError as exc:
        await store.add_event(
            ctx.db, user.tg_chat_id, "", "", "", store.KIND_ERROR, str(exc), ctx.settings.now()
        )
        return chat.api_error_message(str(exc))

    ctx.last_result = result

    if result.seeded:
        return chat.cek_baseline_message(
            len(result.notes), result.today_label.title if result.today_label else "-"
        )
    if not result.changes:
        label = result.today_label.title if result.today_label else "-"
        return chat.cek_no_change_message(label)
    return chat.cek_changes_message(result.changes)


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
    return chat.status_table(today, label, rows, note)


async def cmd_tokeninfo(ctx: BotContext, args: str = "") -> str:
    user = await ctx.user()
    claims_user = session_user(user.bearer_token)
    left = token_health.minutes_left(user, ctx.settings.now())
    lines = [
        "🔎 <b>Info token</b>",
        f"Token: <code>{esc(mask_token(user.bearer_token))}</code>",
        f"Status: {'❌ ditolak server' if user.token_invalid else '✅ aktif'}",
        f"Fingerprint: <code>{esc(user.token_fingerprint[:12] or '-')}</code>",
        f"user claim: <code>{esc(claims_user[:8] or '-')}</code>",
        f"Umur tersimpan: {user.token_ttl_seconds // 3600} jam",
    ]
    if user.token_saved_at:
        lines.append(f"Disimpan: {esc(user.token_saved_at.strftime('%d-%m-%Y %H:%M'))} WIB")
    if left is not None:
        lines.append(f"Perkiraan sisa: {left} menit")
    else:
        lines.append("Perkiraan sisa: tidak diketahui (token dari config.py sebelum migrasi)")
    if user.can_auto_refresh:
        saved = (
            f" (sejak {user.refresh_saved_at.strftime('%d-%m-%Y %H:%M')})"
            if user.refresh_saved_at
            else ""
        )
        lines.append(
            f"Auto-refresh: ✅ cookie <code>{esc(mask_token(user.refresh_cookie))}</code>{saved}"
        )
        lines.append(f"Bot memperpanjang token sendiri saat sisa ≤ {ctx.settings.token_refresh_lead_minutes} menit.")
    else:
        lines.append(
            "Auto-refresh: ❌ belum ada cookie — token harus ditempel manual tiap ±6 jam."
        )
        lines.append("Supaya tidak perlu input ulang: kirim <code>/cookie &lt;nilai&gt;</code> (caranya: /panduan).")
    lines.append("Lupa caranya? Ketik <code>/panduan</code>.")
    return "\n".join(lines)


async def cmd_cookie(ctx: BotContext, args: str = "") -> str:
    return await apply_cookie(ctx, args)


async def cmd_panduan(ctx: BotContext, args: str = "") -> str:
    return sesi.token_guide_text()


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
        return chat.log_empty_message()
    return chat.log_list_message(rows)


async def cmd_start(ctx: BotContext, args: str = "") -> str:
    user = await ctx.user()
    return chat.start_card(
        chat_id=user.tg_chat_id,
        participant_id=user.participant_id,
        base_url=ctx.settings.base_url,
        poll_minutes=ctx.poll_minutes,
        tz_name=ctx.settings.tz_name,
        token_ok=bool(user.bearer_token),
        refresh_ok=user.can_auto_refresh,
    )


HANDLERS: dict[str, Any] = {
    "start": cmd_start,
    "status": cmd_status,
    "cek": cmd_cek,
    "tokeninfo": cmd_tokeninfo,
    "cookie": cmd_cookie,
    "panduan": cmd_panduan,
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
            chat.foreign_chat_message(chat_id, text[:80]),
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
        await ctx.tg.send_message(chat_id, chat.help_text(""))
        return

    handler = HANDLERS.get(name)
    if handler is None:
        await ctx.tg.send_message(chat_id, chat.unknown_command_message())
        return

    try:
        await ctx.tg.send_message(chat_id, await handler(ctx, args))
    except Exception as exc:  # noqa: BLE001 - balasan error jangan sampai mematikan loop
        log.exception("handler %s gagal", name)
        await ctx.tg.send_message(
            chat_id,
            chat.handler_error_message(f"{exc.__class__.__name__}: {exc}"),
        )
