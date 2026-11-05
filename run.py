"""Entrypoint: `python run.py`.

Satu proses asyncio dengan dua loop:
1. long polling Telegram (perintah user),
2. scheduler job: polling status, reminder, kesehatan token, pengumuman, evaluasi.

Tidak butuh port inbound/HTTPS, jadi aman dijalankan sebagai systemd service
di server Ubuntu.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import timedelta

from bot import db as dbmod
from bot import store
from bot.jobs import token_health
from bot.jobs.announcements import run_info_job
from bot.jobs.evaluations import run_evaluation_job
from bot.jobs.reminders import run_reminders
from bot.monev.client import MonevError, TokenRejected
from bot.settings import load_settings
from bot.telegram.api import TelegramClient
from bot.telegram.commands import BotContext, apply_cookie, apply_token, dispatch, poll_auto

REMINDER_EVERY = timedelta(minutes=1)  # jadwal terkunci butuh tick menit (tier 3 menit)
TOKEN_HEALTH_EVERY = timedelta(minutes=30)
INFO_EVERY = timedelta(hours=6)
EVALUATION_EVERY = timedelta(minutes=30)  # hasil evaluasi dikirim hampir instan, bukan 6 jam
POLL_USER_GAP_SECONDS = 2  # jeda antar pengguna dalam satu siklus polling


def force_utf8_console() -> None:
    """Log berisi emoji status; konsol Windows (cp1252) membuat logging crash
    tanpa langkah ini. Di Ubuntu tidak berpengaruh."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def setup_logging(level: str) -> None:
    force_utf8_console()
    # httpx mencetak URL request utuh, dan URL Bot API berisi TELEGRAM_BOT_TOKEN.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


async def scheduler_loop(ctx: BotContext, stop: asyncio.Event) -> None:
    settings = ctx.settings
    next_poll = settings.now()
    next_reminder = settings.now()
    next_token = settings.now()
    next_info = settings.now()
    next_eval = settings.now()

    while not stop.is_set():
        now = settings.now()

        if now >= next_poll:
            next_poll = now + timedelta(minutes=ctx.poll_minutes)
            await do_poll_all(ctx)

        if now >= next_reminder:
            next_reminder = now + REMINDER_EVERY
            for user in await ctx.active_users():
                # Jadwal pengingat terkunci: selalu jalan, tidak bisa dimatikan dari chat.
                try:
                    await run_reminders(ctx.db, settings, user, ctx.tg, now=now)
                except Exception:  # noqa: BLE001
                    logging.exception("job reminder gagal untuk user %s", user.tg_chat_id)

        if now >= next_token:
            next_token = now + TOKEN_HEALTH_EVERY
            for user in await ctx.active_users():
                await keepalive_one(ctx, settings, user, now)

        if now >= next_info:
            next_info = now + INFO_EVERY
            for user in await ctx.active_users():
                if user.token_invalid or not user.bearer_token:
                    continue
                client = ctx.client_for(user)
                try:
                    await run_info_job(ctx.db, settings, user, client, ctx.tg, now=now)
                except Exception:  # noqa: BLE001
                    logging.exception("job info gagal untuk user %s", user.tg_chat_id)
                finally:
                    await client.close()

        if now >= next_eval:
            next_eval = now + EVALUATION_EVERY
            for user in await ctx.active_users():
                if user.token_invalid or not user.bearer_token:
                    continue
                client = ctx.client_for(user)
                try:
                    await run_evaluation_job(ctx.db, settings, user, client, ctx.tg, now=now)
                except Exception:  # noqa: BLE001
                    logging.exception("job evaluasi gagal untuk user %s", user.tg_chat_id)
                finally:
                    await client.close()

        try:
            await asyncio.wait_for(stop.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass


async def keepalive_one(ctx: BotContext, settings, user, now) -> None:
    """Perpanjang token lebih dulu (kalau umur mendekati habis), baru peringatan."""
    client = ctx.client_for(user)
    try:
        fresh = await token_health.keepalive(
            ctx.db, settings, user, client, ctx.tg, now=now
        )
        if fresh is not None:
            ctx.refresh_user_cache(fresh)
        await token_health.run_token_health(ctx.db, settings, user, ctx.tg, now=now)
    except Exception:  # noqa: BLE001
        logging.exception("job token health gagal untuk user %s", user.tg_chat_id)
    finally:
        await client.close()


async def do_poll(ctx: BotContext, user=None) -> None:
    user = user or await ctx.user()
    if not user.bearer_token:
        logging.warning(
            "user %s belum punya token — kirim /cookie atau /token lewat chat",
            user.tg_chat_id,
        )
        return
    if user.token_invalid and not user.can_auto_refresh:
        # token mati dan tidak ada cookie: tunggu aksi user, jangan spam API
        return

    try:
        result = await poll_auto(ctx, user)
        if str(user.tg_chat_id) == str(ctx.settings.telegram_chat_id):
            ctx.last_result = result
    except TokenRejected as exc:
        await token_health.mark_token_invalid(ctx.db, user, ctx.tg, str(exc), ctx.settings.now())
        fresh = await store.get_user(ctx.db, user.tg_chat_id)
        if fresh:
            ctx.refresh_user_cache(fresh)
    except MonevError as exc:
        logging.warning("polling user %s gagal: %s", user.tg_chat_id, exc)
        await store.add_event(
            ctx.db,
            user.tg_chat_id,
            ctx.settings.now().date().isoformat(),
            "",
            "",
            store.KIND_ERROR,
            str(exc),
            ctx.settings.now(),
        )
    except Exception:  # noqa: BLE001
        logging.exception("polling user %s gagal tak terduga", user.tg_chat_id)


async def do_poll_all(ctx: BotContext) -> None:
    """Satu siklus polling untuk semua pengguna aktif, berurutan dengan jeda."""
    users = await ctx.active_users()
    for index, user in enumerate(users):
        if index:
            # Jeda kecil antar user supaya N akun tidak jadi satu ledakan request.
            await asyncio.sleep(POLL_USER_GAP_SECONDS)
        await do_poll(ctx, user)


async def telegram_loop(ctx: BotContext, stop: asyncio.Event) -> None:
    offset: int | None = None
    while not stop.is_set():
        try:
            updates = await ctx.tg.get_updates(offset)
        except Exception:  # noqa: BLE001
            logging.exception("getUpdates gagal")
            await asyncio.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"]
            message = update.get("message")
            if not message:
                continue
            chat_info = message.get("chat", {}) or {}
            chat_id = str(chat_info.get("id", ""))
            if chat_info.get("type") not in (None, "private"):
                # Grup/channel: bot ini memantau akun pribadi, tidak di grup.
                continue
            sender = message.get("from", {}) or {}
            display_name = sender.get("first_name") or sender.get("username") or ""
            text = message.get("text") or message.get("caption") or ""
            if not text:
                continue
            try:
                await dispatch(ctx, chat_id, text, display_name)
            except Exception:  # noqa: BLE001
                logging.exception("dispatch gagal")


async def main(once: bool = False, force_token: str | None = None, force_cookie: str | None = None) -> int:
    settings = load_settings()
    setup_logging(settings.log_level)

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logging.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID kosong — salin config.py.example")
        return 2
    if not settings.participant_id:
        logging.warning(
            "PARTICIPANT_ID kosong — owner tidak di-seed dari config; pengguna "
            "(termasuk Anda) daftar lewat chat: /daftar atau tempel token."
        )

    database = await dbmod.connect(settings.db_path)
    tg = TelegramClient(settings.telegram_bot_token)
    ctx = BotContext(db=database, settings=settings, tg=tg, poll_minutes=settings.poll_minutes)

    stored_minutes = await store.get_setting(database, "poll_minutes")
    if stored_minutes.isdigit():
        ctx.poll_minutes = max(5, int(stored_minutes))

    user = await ctx.user()
    me = await tg.get_me()
    everyone = await store.list_users(database, active_only=False)
    logging.info(
        "bot aktif sebagai @%s, owner chat %s, interval %d menit, %d pengguna terdaftar "
        "(%d aktif), pendaftaran %s",
        me.get("username"),
        user.tg_chat_id,
        ctx.poll_minutes,
        len(everyone),
        sum(1 for u in everyone if u.is_active_user),
        "terbuka" if settings.registration_open else "perlu kode undangan",
    )

    if force_token:
        message = await apply_token(ctx, force_token)
        logging.info("token dari CLI: %s", message.replace("<b>", "").replace("</b>", ""))

    if force_cookie:
        message = await apply_cookie(ctx, force_cookie)
        logging.info(
            "cookie dari CLI: %s",
            message.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""),
        )

    if once:
        await do_poll_all(ctx)
        result = ctx.last_result
        print(
            "hasil:",
            {
                "today": result.today if result else "-",
                "label": result.today_label.key if result and result.today_label else "-",
                "changes": result.changes if result else [],
                "seeded": result.seeded if result else False,
            },
        )
    else:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows tidak mendukung add_signal_handler
                pass
        await asyncio.gather(scheduler_loop(ctx, stop), telegram_loop(ctx, stop))

    await tg.close()
    await database.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot pengingat absensi monev Maganghub")
    parser.add_argument("--once", action="store_true", help="satu siklus polling lalu keluar (tanpa Telegram loop)")
    parser.add_argument("--set-token", metavar="BEARER", help="simpan & validasi token lalu lanjut jalan")
    parser.add_argument("--set-cookie", metavar="COOKIE", help="simpan cookie monev_refresh_token (auto-refresh)")
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(main(once=args.once, force_token=args.set_token, force_cookie=args.set_cookie))
    )
