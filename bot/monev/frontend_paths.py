"""Korek URL API dari bundle SPA portal — jalan ketiga selain HAR dan tebakan path.

Kenapa ini mungkin: frontend monev adalah SPA yang memuat daftar URL API-nya sendiri
di dalam file `.js` hasil build. File itu publik (tidak butuh login), jadi nama
endpoint evaluasi bisa dibaca langsung dari kodenya, tanpa perlu menebak atau
menyalakan DevTools.

Modul ini murni teks-ke-teks (tanpa HTTP): yang memanggil jaringan hanya
`scripts/discover_endpoints.py`. Bentuk seperti ini sengaja dipakai supaya fungsi
penjelajahannya bisa diuji tanpa koneksi internet.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

# Path API yang dipakai portal. `$` dan `{}` ikut: frontend kerap menulis
# `/api/v1/evaluations/${id}` lewat template literal.
API_PATH_RE = re.compile(r"/api/v1/[A-Za-z0-9_\-./{}:$]+")

# Aset JS di HTML pembuka: <script src="/_nuxt/xyz.js">, <link rel=modulepreload href=...>
_SCRIPT_SRC_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']([^"'?#]+\.js(?:\?[^"']*)?)["']""", re.IGNORECASE
)

# Nama chunk yang disebut di dalam JS (dynamic import): "evaluated-1a2b3c4d.js",
# "../assets/pages.Dssf2.js", atau URL penuh.
_CHUNK_RE = re.compile(r"""["'`]([\w./@~+-]+\.js(?:\?[\w.=&%-]*)?)["'`]""")

# Kata kunci yang menandakan satu path ada hubungannya dengan evaluasi bulanan.
# "monthly"/"report"/"laporan" ikut dipakai karena nama sungguhnya di portal ternyata
# `monthly-reports` — tanpa kata "evaluation" sama sekali.
EVAL_HINTS = ("eval", "penilaian", "assess", "appraisal", "rating", "score", "nilai",
              "monthly", "report", "laporan")

# Akhiran yang bukan path API sungguhan (terbawa oleh regex yang terlalu rakus).
_NOISE_SUFFIX = (".js", ".css", ".map", ".json", ".png", ".svg", ".ico", ".woff")

# Build SPA menaruh path relatif terhadap `apiBase` (Nuxt: window.__NUXT__.config.public).
# Yang dicari hanya string berawalan "/" yang jadi argumen pertama sebuah fungsi pendek
# — pola khas instance axios: `await e(`/daily-logs`, params)`. Dengan begitu route
# halaman (`/dashboard/evaluasi`) dan template HTML tidak ikut terbawa.
_CALL_PATH_RE = re.compile(
    r"""[A-Za-z_$][\w$]{0,3}\s*\(\s*[`"'](/[a-zA-Z][a-zA-Z0-9_\-/{}$.]{2,80})[`"']"""
)

# Base URL API yang dikonfigurasi di runtime config Nuxt: `apiBase:"https://…/api/v1"`.
_API_BASE_RE = re.compile(r"""apiBase\s*[:=]\s*["'](https://[^"']+)["']""")


def extract_api_paths(text: str) -> set[str]:
    """Semua `/api/v1/...` yang tertulis di satu berkas HTML/JS, sudah dibersihkan."""
    found: set[str] = set()
    for raw in API_PATH_RE.findall(text or ""):
        path = raw.rstrip("/").rstrip(".")
        if not path or path.endswith(_NOISE_SUFFIX):
            continue
        found.add(path)
    return found


def interesting(paths: set[str], hints: tuple[str, ...] = EVAL_HINTS) -> list[str]:
    """Path yang paling mungkin jadi endpoint evaluasi, urut nama."""
    return sorted(path for path in paths if any(hint in path.lower() for hint in hints))


def extract_call_paths(texts: list[str] | str) -> set[str]:
    """Path relatif terhadap apiBase yang dipakai frontend saat memanggil API."""
    if isinstance(texts, str):
        texts = [texts]
    found: set[str] = set()
    for text in texts:
        for raw in _CALL_PATH_RE.findall(text or ""):
            # `/evaluations/${id}` dipotong jadi `/evaluations`: yang dicari namanya.
            path = raw.split("${")[0].rstrip("/")
            if not path or path.endswith(_NOISE_SUFFIX) or path.startswith("/api/v"):
                continue  # path utuh sudah ditangani extract_api_paths
            found.add(path)
    return found


def api_bases(texts: list[str] | str) -> set[str]:
    """Base URL API yang dikonfigurasi di dalam bundle (runtime config SPA)."""
    if isinstance(texts, str):
        texts = [texts]
    found: set[str] = set()
    for text in texts:
        found.update(_API_BASE_RE.findall(text or ""))
    return found


def script_urls(html: str, base_url: str) -> list[str]:
    """URL absolut aset JS yang dimuat halaman pembuka SPA."""
    out: list[str] = []
    for raw in _SCRIPT_SRC_RE.findall(html or ""):
        url = urljoin(base_url, raw)
        if _same_origin(url, base_url) and url not in out:
            out.append(url)
    return out


def asset_dir(js_urls: list[str]) -> str:
    """Direktori aset SPA (mis. `https://…/_nuxt/`) — tempat chunk lain berada."""
    if not js_urls:
        return ""
    url = js_urls[0]
    return url[: url.rindex("/") + 1] if "/" in url else ""


def chunk_urls(js_text: str, origin: str, asset_base: str) -> set[str]:
    """Chunk yang disebut di dalam satu berkas JS (dynamic import antar halaman).

    Nama file diambil polos (tanpa direktori) lalu digabung ke `asset_base`, karena
    bundler biasanya menaruh semua chunk di satu folder yang sama.
    """
    found: set[str] = set()
    if not asset_base:
        return found
    for raw in _CHUNK_RE.findall(js_text or ""):
        if raw.startswith(("http://", "https://")):
            if _same_origin(raw, origin):
                found.add(raw)
            continue
        name = raw.split("?")[0].rsplit("/", 1)[-1]
        if name and len(name) > 3:
            found.add(urljoin(asset_base, name))
    return found


def _same_origin(url: str, base_url: str) -> bool:
    """Same-origin saja, supaya script tidak ikut mengunduh aset CDN sembarang."""
    root = urljoin(base_url, "/")
    return url.startswith(root)
