#!/usr/bin/env bash
# Pasang bot sebagai service systemd di server Ubuntu.
#
# Jalankan sebagai root dari manapun:
#   sudo bash deploy/install_ubuntu.sh
#
# Variabel yang bisa diubah lewat environment:
#   APP_DIR   lokasi install   (default /opt/monev-bot)
#   RUN_USER  user service     (default monevbot)
#   PYTHON    interpreter      (default python3)
#
# Script ini idempoten: boleh dijalankan ulang untuk upgrade kode.
# Script TIDAK menulis isi config.py — kredensial tetap diisi manual.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/monev-bot}"
RUN_USER="${RUN_USER:-monevbot}"
PYTHON="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"
UNIT_NAME="monev-bot"

if [[ $EUID -ne 0 ]]; then
  echo "Script ini harus dijalankan sebagai root (sudo)." >&2
  exit 1
fi

echo "==> 1/7 Dependensi sistem"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip ca-certificates tzdata

echo "==> 2/7 User service"
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$RUN_USER"
fi

echo "==> 3/7 Kode ke $APP_DIR"
mkdir -p "$APP_DIR" "$APP_DIR/data"
if command -v rsync >/dev/null; then
  rsync -a --delete \
    --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
    --exclude '.pytest_cache' --exclude 'config.py' --exclude 'download' --exclude '*.har' \
    "$SRC_DIR"/ "$APP_DIR"/
else
  cp -a "$SRC_DIR"/. "$APP_DIR"/
fi

echo "==> 4/7 Virtualenv"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  "$PYTHON" -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"

echo "==> 5/7 Konfigurasi"
if [[ ! -f "$APP_DIR/config.py" ]]; then
  cp "$APP_DIR/config.py.example" "$APP_DIR/config.py"
  echo "   config.py baru dibuat dari contoh. ISI KREDENSIALNYA sebelum start:"
  echo "     nano $APP_DIR/config.py"
else
  echo "   config.py sudah ada, tidak disentuh."
fi
chmod 600 "$APP_DIR/config.py"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR/data" "$APP_DIR/config.py"

echo "==> 6/7 systemd unit"
sed "s|/opt/monev-bot|$APP_DIR|g" "$SCRIPT_DIR/$UNIT_NAME.service" > "/etc/systemd/system/$UNIT_NAME.service"
systemctl daemon-reload
systemctl enable "$UNIT_NAME" >/dev/null

echo "==> 7/7 Test jalan"
if [[ ! -f "$APP_DIR/config.py" ]] || grep -q "YOUR_TELEGRAM_BOT_TOKEN" "$APP_DIR/config.py"; then
  echo "   Kredensial belum diisi — service TIDAK saya start. Isi config.py lalu:"
  echo "     sudo systemctl start $UNIT_NAME"
else
  echo "   Uji offline (1 siklus polling, tanpa loop Telegram):"
  (cd "$APP_DIR" && sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/python" run.py --once) || {
    echo "   --once gagal (kemungkinan token ditolak). Lanjut install tetap selesai."
  }
  systemctl restart "$UNIT_NAME"
  sleep 3
  systemctl --no-pager --lines=20 status "$UNIT_NAME" || true
fi

cat <<EOF

Selesai. Perintah harian:
  sudo systemctl status  $UNIT_NAME
  sudo journalctl -fu    $UNIT_NAME
  sudo systemctl restart $UNIT_NAME

Isi/refresh token monev cukup dari chat Telegram:  /token <Bearer ...>
EOF
