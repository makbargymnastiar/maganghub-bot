"""Kalimat untuk evaluasi bulanan mentor: kartu hasil, status belum diisi, motivasi.

Aturan main yang dijaga di sini: apa yang tidak ada di payload tidak boleh muncul
di pesan. Baris tanpa bintang ditampilkan sebagai "—" (bukan ⭐ karangan), bagian
kurikulum/komentar dilewati kalau portal memang tidak mengirimnya.
"""

from __future__ import annotations

from ..models import EvalItem, EvaluationResult
from .base import esc, month_year_id

MAX_STARS = 5

# Ditulis dalam urutan prioritas; kasus dipilih dari kondisi, bukan dari angka acak.
_MOTIVATION_ALL_TOP = (
    "Konsistensi kamu luar biasa — semua poin Sangat Baik. Profesionalisme seperti ini "
    "bikin mentor tidak ragu kasih tantangan berikutnya, jadi ambil project yang lebih "
    "beresiko kalau ada tawaran.",
    "Nilai penuh di semua poin bukan kebetulan: itu hasil disiplin yang kamu jaga tiap "
    "hari. Saatnya naik level — minta tanggung jawab yang lebih besar bulan depan.",
    "Sempurna di semua aspek. Pertahankan ritme ini, dan mulai siapkan bukti karya yang "
    "bisa kamu bawa setelah magang.",
)

_MOTIVATION_SOLID = (
    "Performa kamu sudah solid. Yang biasanya membedakan Baik dari Sangat Baik cuma "
    "detail kecil — dokumentasi dan inisiatif melapor — jadi asah itu bulan depan.",
    "Hasil yang bagus dan stabil. Coba lirik poin dengan bintang terbanyak lalu "
    "naikkan satu tingkat lagi; biasanya cukup dengan lebih aktif update ke mentor.",
    "Nilainya sudah nyaman dilihat. Selisih menuju Sangat Baik biasanya ada di "
    "kebiasaan kecil: catatan harian yang lebih spesifik dan follow-up tanpa ditanya.",
)

_MOTIVATION_GROWTH = (
    "Yang nilainya tinggi pertahankan; masukan mentor baca sebagai peta jalan, bukan "
    "vonis. Satu hal kecil yang kamu perbaiki bulan ini sudah terasa di nilai bulan depan.",
    "Setiap catatan mentor itu materi belajar — ambil satu, praktikkan pelan-pelan. "
    "Kamu sudah terbukti bisa dari poin-poin yang nilainya bagus.",
    "Tidak semua poin harus langsung sempurna. Pilih satu yang paling sering dipakai "
    "sehari-hari, perbaiki itu dulu, dan minta cek ulang ke mentor minggu depan.",
)


def _pick(period: str, variants: tuple[str, ...]) -> str:
    """Varian tetap per periode — notifikasi yang diulang tidak boleh berubah-ubah."""
    return variants[sum(ord(char) for char in period) % len(variants)]


def stars(value: int) -> str:
    return "⭐" * value if 0 < value <= MAX_STARS else "—"


def _line(item: EvalItem) -> str:
    predikat = esc(item.predikat or item.raw or "")
    return f"• {esc(item.name)}\n  {stars(item.stars)} {predikat}".rstrip()


def motivation_sentence(result: EvaluationResult) -> str:
    """Kalimat penyemangat dari distribusi nilai + ada/tidaknya masukan mentor.

    Nama poin TIDAK di-escape di sini: seluruh kalimat hasil di-escape sekali oleh
    pemanggilnya, kalau tidak "&" di nama poin jadi &amp;amp;.
    """
    if result.all_top:
        return _pick(result.period, _MOTIVATION_ALL_TOP)
    if result.needs_improvement or (result.comment and not result.all_top):
        kuat = ", ".join(item.name for item in result.strengths[:2])
        pembukaan = f"{kuat} nilainya bagus — itu fondasinya. " if kuat else ""
        return pembukaan + _pick(result.period, _MOTIVATION_GROWTH)
    if result.average >= 4:
        return _pick(result.period, _MOTIVATION_SOLID)
    return _pick(result.period, _MOTIVATION_GROWTH)


def eval_result_message(result: EvaluationResult) -> str:
    """(C) Kartu hasil evaluasi — dikirim sesaat setelah mentor mengisi."""
    lines = [
        "🎉 <b>HASIL EVALUASI MAGANG KELUAR!</b>",
        "",
        f"📅 {esc(month_year_id(result.period))}",
        "Status: ✅ Evaluasi Diisi Mentor",
    ]
    if result.reviewer:
        lines.append(f"👤 Mentor: {esc(result.reviewer)}")

    if result.items:
        lines += ["", "📊 <b>Rincian Evaluasi:</b>"]
        lines += [_line(item) for item in result.items]
    if result.curriculum:
        lines += ["", "🎓 <b>Capaian Kurikulum:</b>"]
        lines += [_line(item) for item in result.curriculum]
    if result.comment:
        lines += ["", "💬 <b>Komentar Mentor:</b>", f"“{esc(result.comment)}”"]

    lines += ["", f"✨ {esc(motivation_sentence(result))}"]
    return "\n".join(lines)


def eval_pending_message(period: str) -> str:
    """/evaluasi saat mentor belum mengisi — status jujur, tanpa angka palsu."""
    return (
        f"⏳ <b>EVALUASI {esc(month_year_id(period)).upper()} BELUM DIISI MENTOR</b>\n\n"
        "Belum ada nilai yang bisa ditunjukkan. Pengingat H-7 sampai hari-H tetap "
        "berjalan, dan bot mengirim kartu hasil begitu mentor mengisi."
    )
