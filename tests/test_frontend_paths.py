"""Tes penjelajah bundle SPA: semua fungsinya murni teks, tanpa koneksi."""

from bot.monev import frontend_paths

WEB = "https://monev.maganghub.kemnaker.go.id"

INDEX = f"""<!doctype html><html><head>
<link rel="modulepreload" href="/_nuxt/entry-ab12cd34.js">
<script src="/_nuxt/vendor-9f8e7d6c.js"></script>
<script src="https://cdn.example.com/tracking.js"></script>
</head><body><div id="__nuxt"></div></body></html>"""

JS = """
const base = "/api/v1";
await request("/api/v1/users/me/home");
fetch(`/api/v1/evaluations/${id}/monthly`);
const logs = "/api/v1/daily-logs/";
const asset = "/_nuxt/evaluation-page-1a2b3c4d.js";
import("./mentor-5f4e3d2c.js");
const version = "/version.json";
"""


def test_extract_api_paths_dari_js():
    paths = frontend_paths.extract_api_paths(JS)
    assert "/api/v1/users/me/home" in paths
    # Template literal ikut utuh — nanti tinggal dipotong saat dipakai.
    assert "/api/v1/evaluations/${id}/monthly" in paths
    assert "/api/v1/daily-logs" in paths  # sisa "/" di belakang dibuang
    assert "/version.json" not in paths  # bukan path API
    assert not any(p.endswith(".js") for p in paths)


def test_extract_api_paths_amankan_input_kosong():
    assert frontend_paths.extract_api_paths("") == set()
    assert frontend_paths.extract_api_paths("tidak ada path sama sekali") == set()


def test_interesting_hanya_yang_berbau_evaluasi():
    paths = frontend_paths.extract_api_paths(JS)
    assert frontend_paths.interesting(paths) == ["/api/v1/evaluations/${id}/monthly"]


def test_script_urls_absolute_dan_same_origin():
    urls = frontend_paths.script_urls(INDEX, WEB)
    assert urls == [f"{WEB}/_nuxt/entry-ab12cd34.js", f"{WEB}/_nuxt/vendor-9f8e7d6c.js"]
    assert frontend_paths.asset_dir(urls) == f"{WEB}/_nuxt/"


def test_chunk_urls_mengikuti_dynamic_import():
    found = frontend_paths.chunk_urls(JS, WEB, f"{WEB}/_nuxt/")
    assert f"{WEB}/_nuxt/evaluation-page-1a2b3c4d.js" in found
    assert f"{WEB}/_nuxt/mentor-5f4e3d2c.js" in found
    assert all(url.startswith(WEB) for url in found)


def test_chunk_urls_butuh_asset_base():
    assert frontend_paths.chunk_urls(JS, WEB, "") == set()
