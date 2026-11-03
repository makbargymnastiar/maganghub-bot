"""Alat bantu live: panggil API monev dan cetak status hasil pemetaan bot.

Tidak menyentuh Telegram dan tidak menulis ke DB — aman dipakai kapan pun untuk
memastikan token masih hidup dan mapping status masih benar.

Pemakaian:
    python scripts/probe_api.py                     # pakai token dari config.py / DB
    python scripts/probe_api.py --token "Bearer …"  # pakai token tertentu
    python scripts/probe_api.py --days 14           # jendela tanggal yang diperiksa
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import db as dbmod
from bot import store
from bot.api import endpoints
from bot.api.client import MonevClient, MonevError, TokenRejected
from bot.jobs.poll import build_records
from bot.settings import load_settings, mask_token
from bot.status import resolve, unknown_detail
from run import force_utf8_console


async def main(token: str | None, days: int, use_db: bool) -> int:
    force_utf8_console()
    settings = load_settings()
    token = (token or "").strip().removeprefix("Bearer ").strip()

    if not token and use_db and Path(settings.db_path).exists():
        database = await dbmod.connect(settings.db_path)
        user = await store.get_user(database, settings.telegram_chat_id)
        if user:
            token = user.bearer_token
        await database.close()

    if not token:
        token = settings.bearer_token

    if not token:
        print("Tidak ada token. Isi config.py atau pakai --token.")
        return 2

    print(f"base_url : {settings.base_url}")
    print(f"token    : {mask_token(token)}")
    print(f"participant: {settings.participant_id}")

    client = MonevClient(settings.base_url, token)
    try:
        home = await endpoints.fetch_home(client)
        print("\n== home ==")
        print(json.dumps(home, indent=2, ensure_ascii=False))

        attendances = await endpoints.fetch_attendances(client, settings.participant_id)
        logs = await endpoints.fetch_daily_logs(
            client, settings.participant_id, settings.now().date().replace(day=1).isoformat()
        )
    except TokenRejected as exc:
        print(f"\n❌ Token ditolak: {exc}. Ambil token baru dari browser lalu kirim /token.")
        return 1
    except MonevError as exc:
        print(f"\n❌ Gagal memanggil API: {exc}")
        return 1
    finally:
        await client.close()

    today = settings.now().date()
    records = build_records(today, home, attendances, logs, lookback_days=days)

    print(f"\n== status {len(records)} tanggal (jendela {days} hari) ==")
    for record in records:
        label = resolve(record)
        line = (
            f"{record.date}  {label.emoji} {label.key:<16} "
            f"att={record.attendance_status or '-'} "
            f"appr={record.approval_status or '-'} "
            f"state={record.log_state or '-'}"
        )
        if label.key == "UNKNOWN":
            line += f"  << {unknown_detail(record)}"
        print(line)

    enum_values = {
        "attendance.status": sorted({r.attendance_status for r in records if r.attendance_status}),
        "attendance.approval_status": sorted({r.approval_status for r in records if r.approval_status}),
        "log.state": sorted({r.log_state for r in records if r.log_state}),
    }
    print("\n== nilai enum yang muncul di data nyata ==")
    print(json.dumps(enum_values, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Probe API monev + hasil pemetaan status")
    parser.add_argument("--token", help="Bearer token (dengan/tanpa prefiks 'Bearer ')")
    parser.add_argument("--days", type=int, default=7, help="jumlah hari ke belakang yang diperiksa")
    parser.add_argument(
        "--no-db", action="store_true", help="jangan baca token dari data/bot.db, pakai config.py saja"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.token, args.days, use_db=not args.no_db)))
