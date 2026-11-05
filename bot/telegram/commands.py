"""Perintah Telegram.

Multi-user: setiap chat yang terdaftar di tabel `users` dilayani sendiri, dan
siapa pun bisa mulai memakai bot tanpa tindakan pemilik — cukup tempel token
sesi (bot membaca `participant_id` dari portal). `INVITE_CODE` opsional: kalau
diisi, pendaftar wajib membawa kode.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import aiosqlite

from .. import store
from ..jobs import evaluations, token_health
from ..jobs.poll import PollResult, run_poll
from ..messages import chat, evaluasi, sesi
from ..messages.base import esc
from ..models import STATUS_ACTIVE, STATUS_PENDING, User
from ..monev import endpoints
from ..monev.client import MonevClient, MonevError, TokenRejected
from ..monev.jwt_utils import fingerprint, looks_like_jwt, session_user, ttl_seconds
from ..monev.status import ALL_LABELS
from ..settings import Settings, mask_token, normalize_cookie
from .api import TelegramClient

log = logging.getLogger("bot.commands")

# Perintah yang tetap boleh dipakai user yang belum terverifikasi (status `pending`).
PRE_ACTIVATION_COMMANDS = {"start", "help", "panduan", "daftar", "token", "cookie", "status", "tokeninfo"}


@dataclass
class BotContext:
    db: aiosqlite.Connection
    settings: Settings
    tg: TelegramClient
    last_result: PollResult | None = None
    poll_minutes: int = 15
    # Cache user owner (dipakai scheduler + test lama); user lain selalu dibaca
    # dari DB per chat_id supaya tidak ada data yang tertukar.
    _user: User | None = field(default=None, repr=False)

    async def user(self) -> User:
        """User owner: baris yang di-seed dari `config.py`."""
        if self._user is None:
            self._user = await store.ensure_user(self.db, self.settings)
        return self._user

    async def user_for(self, chat_id: str) -> User | None:
        """User pemilik chat ini — None kalau belum terdaftar."""
        chat_id = str(chat_id)
        if self._user is not None and str(self._user.tg_chat_id) == chat_id:
            return self._user
        user = await store.get_user(self.db, chat_id)
        if user is not None and str(user.tg_chat_id) == str(self.settings.telegram_chat_id):
            self._user = user
        return user

    async def active_users(self) -> list[User]:
        """Semua pengguna yang boleh dipantau scheduler (terverifikasi + aktif)."""
        await self.user()  # pastikan baris owner dari config selalu ada
        return await store.list_users(self.db, active_only=True)

    def refresh_user_cache(self, user: User) -> None:
        # Hanya user owner yang boleh mengisi cache tunggal — user lain selalu
        # dibaca dari DB per chat_id.
        if str(user.tg_chat_id) == str(self.settings.telegram_chat_id):
            self._user = user

    def client_for(self, user: User) -> MonevClient:
        # Cookie refresh ikut dibawa supaya `keepalive()` bisa memanggil
        # /auth/refresh dengan kredensial yang tersimpan di DB, bukan dari user.
        return MonevClient(
            self.settings.base_url, user.bearer_token, refresh_cookie=user.refresh_cookie
        )


async def poll_auto(ctx: BotContext, user: User | None = None) -> PollResult:
    """Satu siklus polling dengan pemulihan otomatis.

    Kalau server menolak token akses, bot coba refresh lewat cookie lebih dulu dan
    mengulang sekali. TokenRejected hanya naik kalau auto-refresh tidak mungkin
    atau tetap ditolak — itu satu-satunya saat user perlu bertindak.
    """
    user = user or await ctx.user()
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


# --- verifikasi & adopsi registrasi multi-user --------------------------------


def _profile_ids(profile: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(id_participant_sejati, id_lain_yang_mungkin_sama).

    Hanya kunci berunsur "participant" yang dianggap pasti; `id`/`user_id` ikut
    dibaca tapi cuma bisa memutuskan "cocok", bukan "tidak cocok" — bentuk
    `/users/me` belum terdokumentasi.
    """
    sure = {value.lower() for value in endpoints.walk_participant_ids(profile)}
    maybe: set[str] = set()
    for key in ("id", "user_id", "uuid"):
        value = profile.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            maybe.add(str(value).strip().lower())
    return sure, maybe


async def verify_participant(ctx: BotContext, user: User, token: str) -> str:
    """Cocokkan participant_id klaim dengan token milik user.

    Return 'ok' (cocok), 'mismatch' (profil menyebut ID peserta lain — klaim
    salah), atau 'unknown' (profil tidak terbaca / tidak memuat ID). 'unknown'
    tetap diaktifkan: yang dipakai verifikasi adalah token milik user sendiri.
    """
    claimed = (user.participant_id or "").strip().lower()
    if not claimed:
        return "unknown"
    if session_user(token).strip().lower() == claimed:
        return "ok"

    probe = MonevClient(ctx.settings.base_url, token)
    try:
        profile = await endpoints.fetch_user_profile(probe)
    except MonevError:
        return "unknown"
    finally:
        await probe.close()

    sure, maybe = _profile_ids(profile)
    if claimed in sure or claimed in maybe:
        return "ok"
    return "mismatch" if sure else "unknown"


async def adopt_participant_id(ctx: BotContext, user: User, token: str) -> User | None:
    """Ambil participant_id dari portal — pendaftaran cukup tempel token saja.

    None berarti portal tidak memberi petunjuk ID; user diminta kirim /daftar.
    """
    probe = MonevClient(ctx.settings.base_url, token)
    try:
        found = await endpoints.discover_participant_id(probe)
    except MonevError:
        found = ""
    finally:
        await probe.close()
    if not found:
        return None

    await store.set_participant_id(ctx.db, user.tg_chat_id, found)
    await store.activate_user(ctx.db, user.tg_chat_id)
    fresh = await store.get_user(ctx.db, user.tg_chat_id) or user
    await store.add_event(
        ctx.db, user.tg_chat_id, ctx.settings.now().date().isoformat(),
        "", "", store.KIND_INFO, "participant_id terbaca otomatis", ctx.settings.now(),
    )
    ctx.refresh_user_cache(fresh)
    await notify_owner(ctx, fresh, approved=True)
    return fresh


async def notify_owner(ctx: BotContext, user: User, approved: bool) -> None:
    """Kabar ke chat owner bahwa ada pengguna baru (bukan owner itu sendiri)."""
    owner = str(ctx.settings.telegram_chat_id)
    if not owner or str(user.tg_chat_id) == owner:
        return
    await ctx.tg.send_message(
        owner,
        chat.owner_notify_new_user(
            user.tg_chat_id, user.name, user.participant_id, approved
        ),
    )


async def finalize_registration(ctx: BotContext, user: User, token: str) -> str:
    """Setelah token/cookie masuk: aktifkan user `pending` kalau bisa diverifikasi.

    Return tambahan teks balasan (kosong kalau tidak ada yang berubah).
    """
    if user.status != STATUS_PENDING or not user.participant_id:
        return ""
    verdict = await verify_participant(ctx, user, token)
    if verdict == "mismatch":
        log.info("verifikasi participant gagal untuk chat %s", user.tg_chat_id)
        return "\n\n" + chat.participant_mismatch_message(user.participant_id)

    await store.activate_user(ctx.db, user.tg_chat_id)
    fresh = await store.get_user(ctx.db, user.tg_chat_id)
    if fresh:
        ctx.refresh_user_cache(fresh)
    await store.add_event(
        ctx.db, user.tg_chat_id, ctx.settings.now().date().isoformat(),
        "", "", store.KIND_INFO, "akun terverifikasi", ctx.settings.now(),
    )
    await notify_owner(ctx, user, approved=verdict == "ok")
    note = chat.registration_done_message(user.participant_id, ctx.poll_minutes)
    if verdict != "ok":
        note += (
            "\n<i>Verifikasi otomatis belum bisa memastikan ID Anda. Kalau /status "
            "nanti kosong, kirim ulang /daftar dengan participant_id yang benar.</i>"
        )
    return "\n\n" + note


async def session_note(ctx: BotContext, user: User, token: str) -> str:
    """Tambahan balasan setelah sesi tersimpan: lengkapi ID atau verifikasi klaim."""
    if not user.participant_id:
        adopted = await adopt_participant_id(ctx, user, token)
        if adopted is None:
            return "\n\n" + chat.participant_id_needed_message()
        return "\n\n" + chat.registration_done_message(adopted.participant_id, ctx.poll_minutes)
    return await finalize_registration(ctx, user, token)


async def apply_token(ctx: BotContext, raw_token: str, chat_id: str | None = None) -> str:
    """Simpan token setelah divalidasi ke API. Kembalikan pesan balasan (HTML)."""
    if not (raw_token or "").strip():
        # /token tanpa isi = user lupa menempel tokennya; kirim panduannya
        return sesi.token_guide_text()
    token = raw_token.strip().removeprefix("Bearer ").strip()
    if not looks_like_jwt(token):
        return chat.not_jwt_message()

    user = await (ctx.user() if chat_id is None else ctx.user_for(chat_id))
    if user is None:
        return chat.welcome_message()
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
    saved = await store.get_user(ctx.db, user.tg_chat_id) or user
    note = await session_note(ctx, saved, token)
    return chat.token_active_hint(date_hint) + note


async def apply_cookie(ctx: BotContext, raw_cookie: str, chat_id: str | None = None) -> str:
    """Simpan cookie refresh setelah dites nyata ke /auth/refresh.

    Cookie tidak bisa diperiksa bentuknya seperti JWT, jadi satu-satunya validasi
    adalah memanggil endpointnya. Kalau ditolak, cookie tidak disimpan supaya
    konfigurasi lama tidak rusak.
    """
    if not (raw_cookie or "").strip():
        return sesi.cookie_missing_message()

    user = await (ctx.user() if chat_id is None else ctx.user_for(chat_id))
    if user is None:
        return chat.welcome_message()
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
    saved = await store.get_user(ctx.db, user.tg_chat_id) or user
    note = await session_note(ctx, saved, token)
    return sesi.cookie_saved_message(mask_token(cookie.split("=", 1)[-1]), ttl_seconds(token) // 60) + note


async def cmd_cek(ctx: BotContext, user: User, args: str = "") -> str:
    if not user.bearer_token:
        return chat.cek_no_token_message()
    try:
        result = await poll_auto(ctx, user)
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
        return chat.cek_no_change_message(result.today_label or ALL_LABELS["UNKNOWN"])
    return chat.cek_changes_message(result.changes)


async def cmd_status(ctx: BotContext, user: User, args: str = "") -> str:
    now = ctx.settings.now()
    today = now.date().isoformat()
    rows = await store.list_day_states(ctx.db, user.tg_chat_id, today, limit=1)
    current = rows[0] if rows and rows[0]["log_date"] == today else None
    label = ALL_LABELS.get(current["label"]) if current else ALL_LABELS["EMPTY"]
    note = ""
    if user.token_invalid:
        note = "Token tidak aktif — data di atas mungkin basi. Kirim /token baru."
    return chat.status_today(today, label, note)


async def cmd_rekap(ctx: BotContext, user: User, args: str = "") -> str:
    """Riwayat 5 tanggal terakhir, format daftar ringkas (template review user)."""
    now = ctx.settings.now()
    since = (now.date() - timedelta(days=10)).isoformat()
    rows = await store.list_day_states(ctx.db, user.tg_chat_id, since, limit=30)
    # baris terbaru dulu (urutan store DESC) — tampilkan 5 hari, tanpa duplikat tanggal
    seen: set[str] = set()
    recent = []
    for r in rows:
        if r["log_date"] in seen:
            continue
        seen.add(r["log_date"])
        recent.append(r)
        if len(recent) == 5:
            break
    return chat.rekap_history(recent)


async def cmd_tokeninfo(ctx: BotContext, user: User, args: str = "") -> str:
    """/tokeninfo — format template review user (2026-11-05): INFO SESI & TOKEN."""
    now = ctx.settings.now()
    lines = ["🔎 <b>INFO SESI &amp; TOKEN</b>", ""]
    if user.bearer_token:
        lines.append("🔑 Token Akses: 🔒 Tersimpan")
        lines.append("Status: ❌ Ditolak server" if user.token_invalid else "Status: ✅ Aktif")
        lines.append("🛡️ Sesi ID: 🔐 Terverifikasi")
        if user.token_saved_at:
            lines.append(f"⏱️ Disimpan: {esc(user.token_saved_at.strftime('%d-%m-%Y %H:%M'))} WIB")
        else:
            lines.append("⏱️ Disimpan: tidak tercatat (token dari config.py sebelum migrasi)")
        left = token_health.minutes_left(user, now)
        if left is not None:
            lines.append(f"⏳ Perkiraan Sisa: {left} menit")
        else:
            lines.append("⏳ Perkiraan Sisa: tidak diketahui")
    else:
        lines.append("🔑 Token Akses: ❌ Belum ada — kirim /token atau /cookie")
    lines.append("")
    if user.can_auto_refresh:
        lines.append("🔄 Auto-refresh: 🔒 Cookie Aktif")
        lines.append(
            f"Bot memperpanjang token otomatis saat sisa ≤ "
            f"{ctx.settings.token_refresh_lead_minutes} menit."
        )
    else:
        lines.append("🔄 Auto-refresh: ❌ Belum Ada Cookie")
        lines.append(
            "Token harus ditempel manual tiap ±6 jam. Supaya otomatis, kirim "
            "<code>/cookie &lt;nilai&gt;</code>."
        )
    lines.append("")
    lines.append("Lupa caranya? Ketik <code>/panduan</code>.")
    return "\n".join(lines)


async def cmd_cookie(ctx: BotContext, user: User, args: str = "") -> str:
    return await apply_cookie(ctx, args, user.tg_chat_id)


async def cmd_panduan(ctx: BotContext, user: User, args: str = "") -> str:
    return sesi.token_guide_text()


async def cmd_interval(ctx: BotContext, user: User, args: str) -> str:
    arg = args.strip()
    if not arg.isdigit():
        return (
            f"Pemakaian: <code>/interval 15</code> "
            f"(saat ini {ctx.poll_minutes} menit untuk semua pengguna, minimum 5)"
        )
    minutes = max(5, int(arg))
    ctx.poll_minutes = minutes
    await store.set_setting(ctx.db, "poll_minutes", str(minutes), ctx.settings.now())
    return (
        f"⏱️ Interval polling diubah menjadi {minutes} menit untuk semua pengguna "
        "(aktif mulai siklus berikutnya)."
    )


async def cmd_log(ctx: BotContext, user: User, args: str) -> str:
    limit = int(args.strip()) if args.strip().isdigit() else 10
    rows = await store.recent_events(ctx.db, user.tg_chat_id, min(limit, 30))
    if not rows:
        return chat.log_empty_message()
    return chat.log_list_message(rows)


async def cmd_start(ctx: BotContext, user: User, args: str = "") -> str:
    return chat.start_card(
        chat_id=user.tg_chat_id,
        participant_id=user.participant_id,
        base_url=ctx.settings.base_url,
        poll_minutes=ctx.poll_minutes,
        tz_name=ctx.settings.tz_name,
        token_ok=bool(user.bearer_token),
        refresh_ok=user.can_auto_refresh,
    )


async def cmd_daftar(ctx: BotContext, user: User, args: str = "") -> str:
    """/daftar [kode undangan] <participant_id> — gabung sebagai pengguna baru.

    Kode hanya diminta kalau owner mengisi `INVITE_CODE`. Status masih `pending`
    sampai token user masuk dan participant_id-nya cocok, supaya bot tidak memantau
    akun atas dasar ID yang belum terbukti.
    """
    ok, participant_id = parse_daftar(ctx.settings, args)
    if not ok:
        return registration_decline(ctx.settings, args)

    if user.status == STATUS_ACTIVE and user.participant_id and user.participant_id != participant_id:
        # sudah aktif dengan ID lain: jangan diam-diam pindah akun
        return chat.already_registered_message(user.participant_id)

    await store.set_participant_id(ctx.db, user.tg_chat_id, participant_id)
    user = await store.get_user(ctx.db, user.tg_chat_id) or user
    await store.add_event(
        ctx.db, user.tg_chat_id, ctx.settings.now().date().isoformat(),
        "", "", store.KIND_INFO, f"daftar participant={participant_id[:8]}…", ctx.settings.now(),
    )

    if user.bearer_token:
        note = await finalize_registration(ctx, user, user.bearer_token)
        if note:
            return note
        if user.status == STATUS_ACTIVE:
            return chat.registration_done_message(participant_id, ctx.poll_minutes)
    await notify_owner(ctx, user, approved=False)
    return chat.registration_pending_message(participant_id)


PERIODE_RE = re.compile(r"^\d{4}-\d{2}$")


async def cmd_evaluasi(ctx: BotContext, user: User, args: str = "") -> str:
    """/evaluasi [YYYY-MM] — kartu hasil evaluasi bulanan mentor.

    Periode boleh diminta eksplisit karena evaluasi bulan lalu baru terisi pada
    tanggal 9 bulan berikutnya; tanpa argumen yang ditanya bulan berjalan.
    """
    period = (args or "").strip() or ctx.settings.now().date().isoformat()[:7]
    if not PERIODE_RE.match(period):
        return "Format: <code>/evaluasi 2026-11</code> (kosong = bulan ini)."
    if not user.participant_id:
        return chat.participant_id_needed_message()

    client = ctx.client_for(user)
    try:
        result = await evaluations.lookup_evaluation(ctx.db, user, client, period, ctx.settings.now())
    except MonevError as exc:
        return chat.api_error_message(str(exc))
    finally:
        await client.close()

    if not result.is_filled:
        return evaluasi.eval_pending_message(period)
    return evaluasi.eval_result_message(result)


async def cmd_stop(ctx: BotContext, user: User, args: str = "") -> str:
    await store.deactivate_user(ctx.db, user.tg_chat_id)
    return chat.stopped_message()


async def cmd_lanjut(ctx: BotContext, user: User, args: str = "") -> str:
    await ctx.db.execute("UPDATE users SET active = 1 WHERE tg_chat_id = ?", (user.tg_chat_id,))
    await ctx.db.commit()
    return chat.resumed_message()


HANDLERS: dict[str, Any] = {
    "start": cmd_start,
    "status": cmd_status,
    "rekap": cmd_rekap,
    "cek": cmd_cek,
    "tokeninfo": cmd_tokeninfo,
    "cookie": cmd_cookie,
    "panduan": cmd_panduan,
    "interval": cmd_interval,
    "log": cmd_log,
    "daftar": cmd_daftar,
    "evaluasi": cmd_evaluasi,
    "stop": cmd_stop,
    "lanjut": cmd_lanjut,
}


def parse_daftar(settings: Settings, args: str) -> tuple[bool, str]:
    """(lolos, participant_id) — kode undangan cuma wajib kalau `INVITE_CODE` diisi."""
    parts = (args or "").split()
    invite = (settings.invite_code or "").strip()
    if not parts:
        return False, ""
    if invite:
        if len(parts) < 2 or parts[0].strip() != invite:
            return False, ""
        return True, parts[1].strip()
    return True, parts[0].strip()


def registration_decline(settings: Settings, args: str) -> str:
    """Balasan untuk yang belum berhasil mendaftar (tanpa mencatat apa pun)."""
    if not (settings.invite_code or "").strip():
        return chat.invite_missing_message(False) if args.strip() else chat.welcome_message()
    parts = (args or "").split()
    if len(parts) < 2:
        return chat.invite_missing_message(True)
    return chat.invite_wrong_message()


def pre_registration_reply(cmd: str) -> str:
    """Jawaban untuk chat yang belum mendaftar tapi belum menyentuh data siapa pun.

    `/start`, `/help`, `/panduan` adalah cara orang baru mengenal bot, jadi dijawab
    dengan sopan dan tidak dilaporkan ke owner sebagai chat asing.
    """
    if cmd == "panduan":
        return sesi.token_guide_text()
    if cmd == "help":
        return chat.help_text("")
    return chat.welcome_message()


async def register_chat(ctx: BotContext, chat_id: str, name: str) -> User | None:
    """Buat baris user baru; None + pesan alasan kalau kuota `MAX_USERS` penuh.

    Cek kode undangan bukan di sini — itu tanggung jawab parse_daftar/dispatch,
    supaya orang yang sudah membawa kode benar tetap bisa daftar.
    """
    total = await store.count_users(ctx.db)
    if total >= ctx.settings.max_users:
        log.info("pendaftaran ditolak (kuota %d penuh): %s", ctx.settings.max_users, chat_id)
        await ctx.tg.send_message(chat_id, chat.quota_full_message(ctx.settings.max_users))
        return None
    return await store.register_user(ctx.db, chat_id, name, now=ctx.settings.now())


async def dispatch(ctx: BotContext, chat_id: str, text: str, name: str = "") -> None:
    """Satu pesan masuk → satu balasan, ditujukan ke pengguna chat tersebut."""
    text = (text or "").strip()
    if not text:
        return

    command, _, args = text.partition(" ")
    cmd = command.lstrip("/").split("@", 1)[0].lower()
    is_raw_token = looks_like_jwt(text) and not text.startswith("/")

    user = await ctx.user_for(chat_id)
    if user is None and str(chat_id) == str(ctx.settings.telegram_chat_id):
        user = await ctx.user()  # owner yang DB-nya masih kosong

    if user is None:
        # Chat baru: daftar sendiri lewat /daftar, atau langsung tempel token/cookie —
        # keduanya menciptakan baris user tanpa perlu tindakan owner.
        if cmd == "daftar":
            ok, _pid = parse_daftar(ctx.settings, args)
            if not ok:
                log.info("percobaan pendaftaran gagal dari chat %s", chat_id)
                await ctx.tg.send_message(chat_id, registration_decline(ctx.settings, args))
                return
            user = await register_chat(ctx, chat_id, name)
            if user is None:
                return
        elif is_raw_token or cmd in ("token", "cookie"):
            if not ctx.settings.registration_open:
                await ctx.tg.send_message(chat_id, chat.invite_required_message())
                return
            user = await register_chat(ctx, chat_id, name)
            if user is None:
                return
        elif cmd in ("start", "help", "panduan"):
            await ctx.tg.send_message(chat_id, pre_registration_reply(cmd))
            return
        else:
            # Menyentuh data sebelum mendaftar: dijawab, lalu dilaporkan ke owner.
            log.info("pesan dari chat belum terdaftar: %s", chat_id)
            petunjuk = (
                chat.welcome_message() if ctx.settings.registration_open
                else chat.invite_required_message()
            )
            await ctx.tg.send_message(chat_id, petunjuk)
            await ctx.tg.send_message(
                ctx.settings.telegram_chat_id, chat.foreign_chat_message(chat_id, text[:80])
            )
            return

    if is_raw_token:
        await ctx.tg.send_message(chat_id, await apply_token(ctx, text, chat_id))
        return

    if cmd == "token":
        await ctx.tg.send_message(chat_id, await apply_token(ctx, args, chat_id))
        return
    if cmd == "help":
        await ctx.tg.send_message(chat_id, chat.help_text(""))
        return

    handler = HANDLERS.get(cmd)
    if handler is None:
        await ctx.tg.send_message(chat_id, chat.unknown_command_message())
        return

    # User sudah terdaftar tapi belum terverifikasi: perintah yang menyentuh data
    # monev belum boleh jalan — yang bisa jalan hanya panduan & urusan sesi.
    if user.status != STATUS_ACTIVE and cmd not in PRE_ACTIVATION_COMMANDS:
        await ctx.tg.send_message(chat_id, chat.unverified_reminder_message())
        return

    try:
        await ctx.tg.send_message(chat_id, await handler(ctx, user, args))
    except Exception as exc:  # noqa: BLE001 - balasan error jangan sampai mematikan loop
        log.exception("handler %s gagal", cmd)
        await ctx.tg.send_message(
            chat_id,
            chat.handler_error_message(f"{exc.__class__.__name__}: {exc}"),
        )
