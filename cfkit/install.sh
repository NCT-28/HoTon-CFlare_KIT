#!/bin/bash
# CFKit installer. Run as root: sudo ./install.sh   (re-run to upgrade; keeps /etc/cfkit/.env)
set -euo pipefail

APP_DIR=/opt/cfkit
ENV_DIR=/etc/cfkit
ENV_FILE=$ENV_DIR/.env
DATA_DIR=/var/lib/cfkit
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO] $1${NC}"; }
error() { echo -e "${RED}[ERROR] $1${NC}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || error "Chạy với quyền root: sudo $0"
command -v python3 >/dev/null || error "Cần python3"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || error "Cần Python 3.10+"
python3 -c 'import venv, ensurepip' 2>/dev/null || error "Cần gói python3-venv (apt install python3-venv)"

info "Cài code vào $APP_DIR"
install -d -m 755 "$APP_DIR"
rm -rf "$APP_DIR/app" "$APP_DIR/static"
cp -r "$SRC_DIR/app" "$SRC_DIR/static" "$SRC_DIR/requirements.txt" "$APP_DIR/"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

install -d -m 700 "$ENV_DIR" "$DATA_DIR"
if [[ ! -f $ENV_FILE ]]; then
    read -r -s -p "Mật khẩu admin: " PW; echo
    read -r -s -p "Nhập lại: " PW2; echo
    [[ -n $PW && $PW == "$PW2" ]] || error "Mật khẩu rỗng hoặc không khớp"
    HASH=$(CFKIT_PW=$PW PYTHONPATH=$APP_DIR "$APP_DIR/venv/bin/python" -c \
        'import os; from app.auth import hash_password; print(hash_password(os.environ["CFKIT_PW"]))')
    SECRET=$("$APP_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
    (
        umask 077
        cat > "$ENV_FILE" <<EOF
CFKIT_PASSWORD_HASH='$HASH'
CFKIT_SECRET='$SECRET'
CFKIT_PORT=8787
CFKIT_DB=$DATA_DIR/cfkit.db
EOF
    )
    chmod 600 "$ENV_FILE"
    info "Đã tạo $ENV_FILE"
else
    info "Giữ nguyên $ENV_FILE hiện có"
fi

cat > /etc/systemd/system/cfkit.service <<EOF
[Unit]
Description=CFKit - Cloudflare Tunnel Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
User=root
SyslogIdentifier=cfkit

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cfkit.service
systemctl restart cfkit.service

info "--- HOÀN THÀNH ---"
echo "CFKit chạy tại 127.0.0.1:8787 (chỉ localhost)."
echo "Từ máy của bạn:  ssh -L 8787:127.0.0.1:8787 <user>@<server>  rồi mở http://localhost:8787"
echo "Trạng thái: systemctl status cfkit    Log: journalctl -u cfkit -f"
