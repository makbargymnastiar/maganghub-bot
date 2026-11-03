import os
import json
import time
from datetime import datetime
import requests
import schedule
import config

STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"date": None, "state": "NONE", "notified_evening": False}

def save_state(state_data):
    with open(STATE_FILE, "w") as f:
        json.dump(state_data, f, indent=4)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"[ERROR Telegram] {e}")
        return False

def fetch_daily_logs():
    headers = {
        "Authorization": config.BEARER_TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    params = {
        "participant_id": config.PARTICIPANT_ID,
        "limit": 30
    }
    try:
        res = requests.get(config.MONEV_API_URL, headers=headers, params=params, timeout=15)
        if res.status_code == 401:
            send_telegram("⚠️ *Token Sesi Kadaluarsa!*\n\nBearer token Monev kamu sudah habis sesi. Harap perbarui token di `config.py`.")
            return None
        elif res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[ERROR Fetch API] {e}")
    return None

def parse_monev_status(daily_log):
    if not daily_log:
        return "EMPTY"
    
    state = daily_log.get("state")
    approval_status = daily_log.get("approval_status")
    status = daily_log.get("status")

    if status == "DAYOFF":
        return "HOLIDAY"
    if status == "ABSENT":
        return "ABSENT"
    if state == "NEEDS_REVISION":
        return "NEEDS_REVISION"
    if approval_status == "REJECTED":
        return "REJECTED"
    if state == "REVISION_SUBMITTED":
        return "REVISION_SUBMITTED"
    if state == "COMPLETED" or approval_status == "SUBMITTED":
        return "SUBMITTED"
    if approval_status == "APPROVED":
        if status == "ON_LEAVE":
            return "APPROVED_LEAVE"
        return "APPROVED"

    return state or "PENDING"

def job_check_status_change():
    """Pengecekan perubahan status (Isi Laporan / ACC / Reject / Revisi) tiap 15 menit"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    data = fetch_daily_logs()
    if not data:
        return

    items = data if isinstance(data, list) else data.get("data", data.get("results", []))
    today_log = next((item for item in items if item.get("date") == today_str), None)

    state_data = load_state()
    if state_data.get("date") != today_str:
        state_data = {"date": today_str, "state": "NONE", "notified_evening": False}

    current_status = parse_monev_status(today_log)
    last_status = state_data.get("state")

    if current_status != last_status:
        # Notifikasi saat laporan baru saja diisi
        if current_status == "SUBMITTED":
            send_telegram(f"📝 *Laporan Harian Terisi!*\n\nLaporan kegiatan tanggal *{today_str}* sudah berhasil dikirim. Menunggu peninjauan/ACC dari mentor.")
        elif current_status == "APPROVED":
            send_telegram(f"✅ *Hadir Disetujui (ACC)*\n\nLaporan kegiatan tanggal *{today_str}* telah disetujui oleh mentor.")
        elif current_status == "APPROVED_LEAVE":
            send_telegram(f"🟢 *Izin Disetujui*\n\nPengajuan izin tanggal *{today_str}* telah disetujui oleh mentor.")
        elif current_status == "REJECTED":
            send_telegram(f"❌ *Kehadiran Ditolak*\n\nLaporan kegiatan tanggal *{today_str}* ditolak oleh mentor. Silakan periksa portal Monev.")
        elif current_status == "NEEDS_REVISION":
            send_telegram(f"⚠️ *Perlu Tindakan Anda*\n\nMentor meminta perbaikan pada uraian kegiatan tanggal *{today_str}*.")
        elif current_status == "REVISION_SUBMITTED":
            send_telegram(f"⏳ *Revisi Terkirim*\n\nPerbaikan laporan tanggal *{today_str}* telah dikirim. Menunggu peninjauan mentor.")

        state_data["state"] = current_status
        save_state(state_data)

def job_reminder():
    """Pengecekan pengingat 30m (18:00-22:59) & 3m (23:00-23:59) jika belum isi laporan"""
    now = datetime.now()
    hour = now.hour
    today_str = now.strftime("%Y-%m-%d")

    if hour < 18:
        return

    data = fetch_daily_logs()
    if not data:
        return

    items = data if isinstance(data, list) else data.get("data", data.get("results", []))
    today_log = next((item for item in items if item.get("date") == today_str), None)

    status = parse_monev_status(today_log)

    if status in ["SUBMITTED", "APPROVED", "APPROVED_LEAVE", "NEEDS_REVISION", "REJECTED", "HOLIDAY"]:
        return

    state_data = load_state()
    if state_data.get("date") != today_str:
        state_data = {"date": today_str, "state": "NONE", "notified_evening": False}

    if hour >= 23:
        send_telegram(f"🚨 *DARURAT! Belum Isi Laporan*\n\nHari ini (*{today_str}*) tinggal beberapa menit lagi pergantian hari dan kamu **BELUM mengisi laporan kegiatan**.\nSegera buka portal Monev!")
    elif hour >= 18:
        if not state_data.get("notified_evening"):
            send_telegram(f"🔔 *Pengingat Laporan MagangHub*\n\nHari ini (*{today_str}*) kamu belum mengisi laporan harian. Jangan lupa diisi sebelum malam ya!")
            state_data["notified_evening"] = True
            save_state(state_data)

schedule.every(15).minutes.do(job_check_status_change)
schedule.every(30).minutes.do(job_reminder)
schedule.every(3).minutes.do(job_reminder)

if __name__ == "__main__":
    print("Bot MagangHub Server Running...")
    job_check_status_change()
    while True:
        schedule.run_pending()
        time.sleep(30)