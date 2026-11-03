"""Entrypoint: `python run.py`.

Satu proses asyncio dengan dua loop:
1. long polling Telegram (perintah user),
2. scheduler job: polling status, reminder, kesehatan token, pengumuman.

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
from bot.api.client import MonevError, TokenRejected
from bot.jobs import token_health
from bot.jobs.announcements import run_info_job
from bot.jobs.poll import run_poll
from bot.jobs.reminders import run_reminders
from bot.settings import load_settings
from bot.tg.api import TelegramClient
from bot.tg.commands import BotContext, apply_token, dispatch

REMINDER_EVERY = timedelta(minutes=5)
TOKEN_HEALTH_EVERY = timedelta(minutes=30)
INFO_EVERY = timedelta(hours=6)


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

    while not stop.is_set():
        now = settings.now()

        if now >= next_poll:
            next_poll = now + timedelta(minutes=ctx.poll_minutes)
            await do_poll(ctx)

        if now >= next_reminder:
            next_reminder = now + REMINDER_EVERY
            user = await ctx.user()
            if not user.token_invalid and user.reminders_on:
                try:
                    await run_reminders(ctx.db, settings, user, ctx.tg, now=now)
                except Exception:  # noqa: BLE001
                    logging.exception("job reminder gagal")

        if now >= next_token:
            next_token = now + TOKEN_HEALTH_EVERY
            user = await ctx.user()
            try:
                await token_health.run_token_health(ctx.db, settings, user, ctx.tg, now=now)
            except Exception:  # noqa: BLE001
                logging.exception("job token health gagal")

        if now >= next_info:
            next_info = now + INFO_EVERY
            user = await ctx.user()
            if not user.token_invalid:
                client = ctx.client_for(user)
                try:
                    await run_info_job(ctx.db, settings, user, client, ctx.tg, now=now)
                except Exception:  # noqa: BLE001
                    logging.exception("job info gagal")
                finally:
                    await client.close()

        try:
            await asyncio.wait_for(stop.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass


async def do_poll(ctx: BotContext) -> None:
    user = await ctx.user()
    if not user.bearer_token:
        logging.warning("belum ada token — jalankan /token dulu")
        return
    if user.token_invalid:
        return

    client = ctx.client_for(user)
    try:
        ctx.last_result = await run_poll(ctx.db, ctx.settings, user, client, notify=ctx.tg)
        fresh = await store.get_user(ctx.db, user.tg_chat_id)
        if fresh and fresh.token_invalid:
            await token_health.mark_token_valid(ctx.db, fresh, ctx.settings.now())
            ctx.refresh_user_cache(await store.get_user(ctx.db, user.tg_chat_id))
    except TokenRejected as exc:
        await token_health.mark_token_invalid(ctx.db, user, ctx.tg, str(exc), ctx.settings.now())
        ctx.refresh_user_cache(await store.get_user(ctx.db, user.tg_chat_id))
    except MonevError as exc:
        logging.warning("polling gagal: %s", exc)
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
        logging.exception("polling gagal tak terduga")
    finally:
        await client.close()


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
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text") or message.get("caption") or ""
            if not text:
                continue
            try:
                await dispatch(ctx, chat_id, text)
            except Exception:  # noqa: BLE001
                logging.exception("dispatch gagal")


async def main(once: bool = False, force_token: str | None = None) -> int:
    settings = load_settings()
    setup_logging(settings.log_level)

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logging.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID kosong — salin config.py.example")
        return 2
    if not settings.participant_id:
        logging.error("PARTICIPANT_ID kosong di config.py")
        return 2

    database = await dbmod.connect(settings.db_path)
    tg = TelegramClient(settings.telegram_bot_token)
    ctx = BotContext(db=database, settings=settings, tg=tg, poll_minutes=settings.poll_minutes)

    stored_minutes = await store.get_setting(database, "poll_minutes")
    if stored_minutes.isdigit():
        ctx.poll_minutes = max(5, int(stored_minutes))

    user = await ctx.user()
    me = await tg.get_me()
    logging.info("bot aktif sebagai @%s, chat %s, interval %d menit", me.get("username"), user.tg_chat_id, ctx.poll_minutes)

    if force_token:
        message = await apply_token(ctx, force_token)
        logging.info("token dari CLI: %s", message.replace("<b>", "").replace("</b>", ""))

    if once:
        await do_poll(ctx)
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
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(once=args.once, force_token=args.set_token)))
