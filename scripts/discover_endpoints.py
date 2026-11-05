"""Gali nama endpoint monev dari bundle SPA portal — tanpa HAR, tanpa DevTools.

Frontend monev adalah SPA: daftar URL API-nya tertulis di dalam file `.js` publik
yang dimuat halaman login. Script ini mengunduh index + chunk-nya, lalu mencetak
semua `/api/v1/...` yang ditemukan — terutama yang mengandung kata evaluasi. Itu
mengisi lubang yang selama ini cuma bisa ditutup lewat HAR atau tebakan path.

Pemakaian:
    python scripts/discover_endpoints.py                    # gali bundle (tanpa token)
    python scripts/discover_endpoints.py --max-chunks 200   # gali lebih dalam
    python scripts/discover_endpoints.py --sweep            # + cek path mana yang hidup
    python scripts/discover_endpoints.py --sweep --all      # sweep SEMUA path hasil gali
    python scripts/discover_endpoints.py --token "Bearer …"

`--sweep` memakai token (diambil dari --token / config.py / DB owner) dan hanya
melakukan GET. Tanpa token, sweep dilewati — penggalian bundle tidak butuh kredensial.
Script ini tidak menulis ke DB dan tidak menyentuh Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import db as dbmod
from bot import store
from bot.monev import endpoints, frontend_paths
from bot.monev.client import MonevClient, MonevError, TokenRejected
from bot.settings import load_settings, mask_token
from run import force_utf8_console

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) maganghub-endpoint-discovery"
SLEEP_BETWEEN_REQUESTS = 0.25


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    """Ambil teks satu URL; string kosong kalau gagal (alasannya dicetak)."""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"  ! gagal mengambil {url}: {exc.__class__.__name__}")
        return ""
    return resp.text


async def collect_bundle(
    client: httpx.AsyncClient, web_url: str, max_chunks: int, only: tuple[str, ...] = ()
) -> list[str]:
    """Unduh halaman pembuka + chunk JS SPA, kembalikan teks-teksnya.

    Semua build modern menaruh path API relatif terhadap base URL (mis. `"/evaluations"`),
    jadi teks mentahnya sekalian dikembalikan — supaya bisa di-grep.

    `only` = nama chunk tertentu (mis. hasil `--chunks`): ambil persis itu saja, tanpa
    menjelajahi seluruh antrean — sopan ke server dan jauh lebih cepat.
    """
    index = await fetch_text(client, f"{web_url.rstrip('/')}/")
    if not index:
        print("Halaman pembuka SPA tidak bisa diambil. Coba --url manual / cek koneksi.")
        return []

    texts = [index]
    queue = frontend_paths.script_urls(index, web_url)
    if not queue:
        print("Tidak ada <script src=.js> di halaman pembuka — mungkin portal berubah bentuk.")
        return texts

    asset_base = frontend_paths.asset_dir(queue)
    print(f"aset SPA: {asset_base} ({len(queue)} chunk awal)")

    if only:
        wanted = {name.strip().lstrip("/") for name in only if name.strip()}
        queue = [url for url in queue if url.rsplit("/", 1)[-1] in wanted]
        queue += [
            urljoin(asset_base, name)
            for name in sorted(wanted)
            if not any(url.endswith(name) for url in queue)
        ]
        print(f"mode terarah: {len(queue)} chunk diminta")

    seen: set[str] = set()
    while queue and len(texts) <= max_chunks:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        js = await fetch_text(client, url)
        if not js:
            continue
        texts.append(js)
        if not only:
            queue.extend(frontend_paths.chunk_urls(js, web_url, asset_base))
        print(f"\r  {len(texts) - 1} chunk dibaca, {len(queue)} antrean", end="", flush=True)

    print()
    return texts


def grep_bundle(texts: list[str], pattern: str, limit: int = 40) -> None:
    """Cetak potongan yang cocok dengan `pattern` — cara cepat menemukan nama endpoint."""
    import re

    rx = re.compile(pattern, re.IGNORECASE)
    shown = 0
    seen_snippets: set[str] = set()
    for text in texts:
        for match in rx.finditer(text):
            start = max(0, match.start() - 70)
            snippet = " ".join(text[start : match.end() + 70].split())
            if snippet in seen_snippets:
                continue
            seen_snippets.add(snippet)
            shown += 1
            print(f"\n  [{shown}] ...{snippet}...")
            if shown >= limit:
                print(f"\n  (cukup {limit} cuplikan; naikkan dengan --grep-limit)")
                return


async def sweep(client: MonevClient, paths: list[str]) -> dict[str, tuple[int | None, str]]:
    """GET satu per satu; catat status HTTP / ringkasan kegagalan.

    Yang dibaca hanya status: 404 = path tidak ada, 422/400 = path ada tapi butuh
    parameter, 200 = path hidup. Tidak ada satu pun request yang menulis data.
    """
    results: dict[str, tuple[int | None, str]] = {}
    for path in paths:
        try:
            payload = await client.get(path)
        except TokenRejected as exc:
            print(f"  {path:<48} TOKEN DITOLAK: {exc}")
            return results
        except MonevError as exc:
            results[path] = (exc.status, str(exc))
            print(f"  {path:<48} HTTP {exc.status or '-'}")
        else:
            shape = type(payload).__name__
            size = len(payload) if isinstance(payload, (dict, list, str)) else 0
            results[path] = (200, f"{shape}({size})")
            print(f"  {path:<48} 200 OK -> {shape}, {size} entri")
        await asyncio.sleep(SLEEP_BETWEEN_REQUESTS)
    return results


async def resolve_token(settings, token_arg: str, use_db: bool) -> str:
    token = (token_arg or "").strip().removeprefix("Bearer ").strip()
    if not token and use_db and Path(settings.db_path).exists():
        database = await dbmod.connect(settings.db_path)
        user = await store.get_user(database, settings.telegram_chat_id)
        if user:
            token = user.bearer_token
        await database.close()
    return token or settings.bearer_token


async def main(args) -> int:
    force_utf8_console()
    settings = load_settings()
    web_url = (args.url or settings.web_url).rstrip("/")

    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as http:
        texts = await collect_bundle(
            http, web_url, args.max_chunks,
            only=tuple(args.chunks.split(",")) if args.chunks else (),
        )

    if args.grep:
        print(f"\n== grep {args.grep!r} pada {len(texts)} berkas bundle ==")
        grep_bundle(texts, args.grep, args.grep_limit)
        return 0

    bases = sorted(frontend_paths.api_bases(texts))
    absolute: set[str] = set()
    for text in texts:
        absolute |= frontend_paths.extract_api_paths(text)
    relative = frontend_paths.extract_call_paths(texts)

    if bases:
        print("\n== apiBase dari runtime config SPA ==")
        for base in bases:
            print(f"  {base}")

    # Yang ditanyakan frontend sebenarnya relatif: `/daily-logs` + apiBase.
    normalized = {f"/api/v1{path}" for path in relative} | absolute
    if not normalized:
        print(
            "\nTidak ada path API terbaca. Cari manual di bundle dengan --grep, mis.:\n"
            "  python scripts/discover_endpoints.py --grep \"evalua|penilaian|daily-logs\""
        )
        return 1

    print(f"\n== {len(normalized)} path API hasil gali dari bundle ==")
    for path in sorted(normalized):
        print(f"  {path}")

    candidates = frontend_paths.interesting(relative) or frontend_paths.interesting(absolute)
    print(f"\n== yang mengandung kata kunci evaluasi ({len(candidates)}) ==")
    for path in candidates:
        print(f"  {path}")
    if not candidates:
        print("  (tidak ada — artinya nama endpointnya mungkin tidak memakai kata itu)")
    if not args.sweep:
        print(
            "\nLanjut ke verifikasi: ulangi dengan --sweep supaya tiap path benar-benar "
            "dipanggil memakai token aktif."
        )
        return 0

    token = await resolve_token(settings, args.token, not args.no_db)
    if not token:
        print("\n--sweep butuh token (--token, config.py, atau data/bot.db).")
        return 2
    print(f"\n== sweep dengan token {mask_token(token)} ==")
    # Path evaluasi yang sudah dikenal bot ikut disapu, sebagai pembanding kalau
    # ternyata portal punya varian lain.
    known_eval = (
        endpoints.MONTHLY_REPORTS_PATH,
        endpoints.MONTHLY_REPORTS_BY_PERIOD_PATH,
        endpoints.MONTHLY_REPORTS_LATEST_PATH,
    )
    to_try = list(dict.fromkeys(
        [*(f"/api/v1{p}" if not p.startswith("/api/v") else p for p in candidates),
         *known_eval,
         *(sorted(normalized) if args.all else [])]
    ))
    client = MonevClient(settings.base_url, token)
    try:
        results = await sweep(client, to_try)
    finally:
        await client.close()

    alive = [path for path, (status, _) in results.items() if status == 200]
    print("\n== ringkasan ==")
    if alive:
        for path in alive:
            print(f"  HIDUP  {path} -> {results[path][1]}")
        print("\nPath baru yang menarik: tambahkan sebagai konstanta di bot/monev/endpoints.py, "
              "lalu jalankan probe_api.py --evaluations untuk melihat raw response-nya.")
    else:
        print("  Tidak ada satu pun path yang menjawab 200.")
    return 0 if alive else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gali + verifikasi nama endpoint monev")
    parser.add_argument("--url", help="origin SPA (default MONEV_WEB_URL dari config)")
    parser.add_argument("--max-chunks", type=int, default=80, help="batas chunk JS yang dibaca")
    parser.add_argument("--chunks", help="nama chunk tertentu, pisahkan dengan koma (tanpa BFS)")
    parser.add_argument("--grep", help="cetak cuplikan bundle yang cocok pola ini (regex)")
    parser.add_argument("--grep-limit", type=int, default=40, help="jumlah cuplikan --grep")
    parser.add_argument("--sweep", action="store_true", help="panggil tiap path kandidat dengan token")
    parser.add_argument("--all", action="store_true", help="sweep semua path hasil gali, bukan cuma yang berbau evaluasi")
    parser.add_argument("--token", help="Bearer token untuk --sweep")
    parser.add_argument("--no-db", action="store_true", help="jangan baca token dari DB")
    parsed = parser.parse_args()
    raise SystemExit(asyncio.run(main(parsed)))
