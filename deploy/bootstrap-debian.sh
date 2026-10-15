#!/usr/bin/env bash
# One-shot setup for Debian 13 (trixie).
#
#   sudo bash deploy/bootstrap-debian.sh bio.example.com
#
# Creates the service account, installs dependencies, deploys the app to
# /srv/ibshit, generates a SECRET_KEY, imports the question bank and starts the
# service behind nginx. Re-running it is safe: it updates the code and restarts.
set -euo pipefail

APP_USER="ibshit"
APP_DIR="/srv/ibshit"
ENV_FILE="/etc/ibshit.env"
REPO="${REPO:-https://github.com/Dellis908/ibshit.git}"
SERVER_NAME="${1:-}"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this with sudo"

log "Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    git python3 python3-venv python3-pip nginx ca-certificates curl

# --- service account -------------------------------------------------------
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    log "Creating service account $APP_USER"
    useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

# --- application code ------------------------------------------------------
if [[ -d "$APP_DIR/.git" ]]; then
    log "Updating existing checkout"
    git -C "$APP_DIR" fetch --quiet origin
    git -C "$APP_DIR" reset --hard --quiet origin/main
else
    log "Cloning $REPO"
    mkdir -p "$APP_DIR"
    git clone --quiet "$REPO" "$APP_DIR"
fi

# --- virtualenv ------------------------------------------------------------
log "Installing Python dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- configuration ---------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
    log "Generating $ENV_FILE with a fresh SECRET_KEY"
    install -m 0640 -o root -g "$APP_USER" "$APP_DIR/deploy/ibshit.env.example" "$ENV_FILE"
    SECRET=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" "$ENV_FILE"
else
    log "$ENV_FILE already exists, leaving it alone"
fi

# --- database and question bank -------------------------------------------
log "Preparing the database and importing questions"
sudo -u "$APP_USER" env $(grep -v '^#' "$ENV_FILE" | xargs) \
    "$APP_DIR/.venv/bin/flask" --app "$APP_DIR/wsgi.py" quiz seed
sudo -u "$APP_USER" env $(grep -v '^#' "$ENV_FILE" | xargs) \
    "$APP_DIR/.venv/bin/flask" --app "$APP_DIR/wsgi.py" quiz import "$APP_DIR/seed/rdojo.json"

# The service account must own the database and image cache.
chown -R "$APP_USER:$APP_USER" "$APP_DIR/instance"

# --- systemd ---------------------------------------------------------------
log "Installing the systemd unit"
install -m 0644 "$APP_DIR/deploy/ibshit.service" /etc/systemd/system/ibshit.service
systemctl daemon-reload
systemctl enable --now ibshit
systemctl --no-pager --lines=0 status ibshit || true

# --- nginx -----------------------------------------------------------------
log "Configuring nginx"
install -m 0644 "$APP_DIR/deploy/nginx-ibshit.conf" /etc/nginx/sites-available/ibshit
if [[ -n "$SERVER_NAME" ]]; then
    sed -i "s|server_name bio.example.com;|server_name ${SERVER_NAME};|" \
        /etc/nginx/sites-available/ibshit
fi
ln -sf /etc/nginx/sites-available/ibshit /etc/nginx/sites-enabled/ibshit
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# --- firewall --------------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
    log "Allowing SSH and HTTP through ufw"
    ufw allow OpenSSH >/dev/null 2>&1 || true
    ufw allow 'Nginx Full' >/dev/null 2>&1 || true
fi

HOST="${SERVER_NAME:-$(hostname -I | awk '{print $1}')}"
cat <<SUMMARY

$(log "Done")

  URL            http://${HOST}/
  Service        systemctl status ibshit
  Logs           journalctl -u ibshit -f
  Config         ${ENV_FILE}
  App directory  ${APP_DIR}

Next steps:
  1. Point your DNS record at this server.
  2. Add HTTPS:
       sudo apt install certbot python3-certbot-nginx
       sudo certbot --nginx -d ${SERVER_NAME:-your.domain}
  3. Back up ${APP_DIR}/instance (the database) and ${ENV_FILE}.
SUMMARY
