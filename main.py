"""Kompatibilitas perintah lama.

Struktur sebenarnya ada di paket `bot/` dengan entrypoint `run.py`.
Versi lama proyek ini disimpan apa adanya di `legacy/main.py`.
"""

import asyncio
import sys

from run import main

if __name__ == "__main__":
    once = "--once" in sys.argv
    raise SystemExit(asyncio.run(main(once=once)))
