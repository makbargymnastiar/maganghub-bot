"""Alat bantu live: panggil API monev dan cetak status hasil pemetaan bot.

Tidak menyentuh Telegram dan tidak menulis ke DB — aman dipakai kapan pun untuk
memastikan token masih hidup dan mapping status masih benar.

Pemakaian:
    python scripts/probe_api.py                     # pakai token dari config.py / DB
    python scripts/probe_api.py --token "Bearer …"  # pakai token tertentu
    python scripts/probe_api.py --days 14           # jendela tanggal yang diperiksa
    python scripts/probe_api.py --evaluations       # cetak raw JSON laporan bulanan + hasil parser
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
from bot.jobs.poll import build_records, months_for_window
from bot.monev import endpoints, evaluation
from bot.monev.client import MonevClient, MonevError, TokenRejected
from bot.monev.status import pending_detail, resolve, unknown_detail
from bot.models import LOG_FIELDS
from bot.settings import load_settings, mask_token
from run import force_utf8_console


async def probe_evaluations(client: MonevClient, participant_id: str, period: str) -> int:
    """Cetak raw JSON laporan bulanan + hasil parser bot untuk satu periode.

    Nama endpoint-nya sudah ditemukan dari bundle SPA, jadi alat ini bukan lagi
    pencari path: gunanya untuk memastikan bentuk response asli masih sama dengan
    yang diasumsikan `bot/monev/evaluation.py`, dan melihat bulan apa saja yang
    sebenarnya dikirim portal untuk participant_id kita.
    """
    start, end = evaluation.period_window(period)
    window = {
        "participant_id": participant_id,
        "start_date": start,
        "end_date": end,
        "limit": endpoints.REPORTS_LIMIT,
    }
    # Dua query ke path yang sama: yang satu berjendela tanggal, yang satu tidak.
    # Selisih jawabannya yang menjawab apakah portal benar-benar memakai
    # start_date/end_date sebagai penyaring periode.
    targets = (
        ("berjendela", endpoints.MONTHLY_REPORTS_PATH, window),
        ("semua laporan", endpoints.MONTHLY_REPORTS_PATH, {
            "participant_id": participant_id, "limit": endpoints.REPORTS_LIMIT}),
        ("per periode", endpoints.MONTHLY_REPORTS_BY_PERIOD_PATH, window),
        ("terbaru", endpoints.MONTHLY_REPORTS_LATEST_PATH, {"participant_id": participant_id}),
        ("kurikulum", endpoints.PERIOD_CURRICULUM_PATH_FMT.format(
            participant_id=participant_id, start_date=start), None),
    )
    print(f"\n== endpoint evaluasi (periode {period}: {start} s/d {end}) ==")
    hit = 0
    for label, path, query in targets:
        try:
            payload = await client.get(path, query)
        except MonevError as exc:
            print(f"\n[{label}] {path} -> {exc}")
            continue
        hit += 1
        print(f"\n[{label}] {path} {query or ''} -> OK")
        if label != "kurikulum":
            bulan = evaluation.declared_periods(payload) or ["(tanpa penanda bulan)"]
            print(f"bulan yang disebut laporan: {', '.join(bulan)}")
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:4000])
    if not hit:
        print("\nSemua path evaluasi ditolak. Pastikan token masih hidup (/tokeninfo di Telegram).")
        return 1

    result = await endpoints.fetch_evaluation(client, participant_id, period)
    print(f"\n== hasil parser untuk {period} ==")
    print(
        f"filled={result.is_filled} items={len(result.items)} kurikulum={len(result.curriculum)} "
        f"komentar={'ya' if result.comment else 'tidak'} rata-rata={result.average:.1f}"
    )
    for item in (*result.items, *result.curriculum):
        print(f"  {item.name}: {item.stars or '—'} ({item.predikat or 'tanpa nilai'})")
    return 0


async def main(token: str | None, days: int, use_db: bool, evaluations_only: bool = False) -> int:
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
        if evaluations_only:
            participant_id = settings.participant_id or await endpoints.discover_participant_id(client)
            period = settings.now().date().isoformat()[:7]
            return await probe_evaluations(client, participant_id, period)

        home = await endpoints.fetch_home(client)
        print("\n== home ==")
        print(json.dumps(home, indent=2, ensure_ascii=False))

        attendances = await endpoints.fetch_attendances(client, settings.participant_id)
        # jendela --days bisa melewati batas bulan; API menjawab per bulan, jadi
        # setiap bulan dalam jendela harus diminta (dulu hanya bulan berjalan,
        # sehingga tanggal bulan lalu tampak "belum diisi")
        today = settings.now().date()
        logs: dict[str, object] = {}
        for month in months_for_window(today, days):
            logs.update(await endpoints.fetch_daily_logs(client, settings.participant_id, month))
    except TokenRejected as exc:
        print(f"\n❌ Token ditolak: {exc}. Ambil token baru dari browser lalu kirim /token.")
        return 1
    except MonevError as exc:
        print(f"\n❌ Gagal memanggil API: {exc}")
        return 1
    finally:
        await client.close()

    records = build_records(today, home, attendances, logs, lookback_days=days)

    print(f"\n== status {len(records)} tanggal (jendela {days} hari) ==")
    for record in records:
        label = resolve(record)
        total = len(LOG_FIELDS)
        line = (
            f"{record.date}  {label.emoji} {label.key:<20} "
            f"isi={total - len(record.missing_fields)}/{total} "
            f"att={record.attendance_status or '-'} "
            f"appr={record.approval_status or '-'} "
            f"state={record.log_state or '-'}"
        )
        if label.key == "UNKNOWN":
            line += f"  << {unknown_detail(record)}"
        elif pending_detail(record):
            line += f"  << {pending_detail(record)}"
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
    parser.add_argument(
        "--evaluations", action="store_true", help="probe kandidat endpoint evaluasi bulanan"
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(main(args.token, args.days, use_db=not args.no_db, evaluations_only=args.evaluations))
    )
