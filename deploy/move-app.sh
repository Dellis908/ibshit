#!/usr/bin/env bash
# Move the application to a proper location, keeping the database.
#
#   sudo bash deploy/move-app.sh /tmp/ibshit            # dry run: report only
#   sudo bash deploy/move-app.sh /tmp/ibshit --apply    # do it
#
# /tmp is the wrong place for a service on Debian:
#   * systemd can give a service a private /tmp, so the app and nginx would see
#     different filesystems
#   * /tmp is cleaned on boot, so the app disappears after a reboot
#   * anything world-writable there is a security problem
#
# This script moves the checkout to /srv/ibshit, preserves instance/ (the
# database and image cache), repoints the systemd unit and the nginx alias, and
# verifies the result.

set -euo pipefail

SOURCE="${1:-}"
APPLY="${2:-}"
TARGET="${APP_DIR:-/srv/ibshit}"
APP_USER="ibshit"
SERVICE="ibshit"
ENV_FILE="/etc/ibshit.env"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -n "$SOURCE" ]] || die "usage: sudo bash $0 /path/to/current/app [--apply]"
[[ -d "$SOURCE" ]] || die "$SOURCE does not exist"
[[ -f "$SOURCE/wsgi.py" ]] || die "$SOURCE does not look like the app (no wsgi.py)"
[[ "$SOURCE" == "$TARGET" ]] && die "source and target are both $TARGET"

DRY_RUN=1
[[ "$APPLY" == "--apply" ]] && DRY_RUN=0

log "Source: $SOURCE"
log "Target: $TARGET"
[[ $DRY_RUN -eq 1 ]] && warn "report only — re-run with --apply to make changes"

# ------------------------------------------------------------------ inventory
log "Checking what is there"
for item in wsgi.py app requirements.txt seed/questions.json .git; do
    if [[ -e "$SOURCE/$item" ]]; then
        printf '    present  %s\n' "$item"
    else
        printf '    MISSING  %s\n' "$item"
    fi
done

DB="$SOURCE/instance/quiz.db"
if [[ -f "$DB" ]]; then
    answers=$(sqlite3 "$DB" "SELECT COUNT(*) FROM attempt;" 2>/dev/null || echo "?")
    log "Database found: $DB ($answers recorded answers)"
else
    warn "No instance/quiz.db found; progress will start empty"
fi

if [[ -e "$TARGET" ]]; then
    warn "$TARGET already exists; it will be moved aside, not deleted"
fi
if [[ $DRY_RUN -eq 1 ]]; then
    echo
    log "Would run:"
    echo "      1. stop the $SERVICE service"
    echo "      2. move $TARGET aside if present"
    echo "      3. move $SOURCE -> $TARGET"
    echo "      4. chown -R $APP_USER:$APP_USER $TARGET"
    echo "      5. recreate .venv and install requirements"
    echo "      6. repoint WorkingDirectory/ExecStart/ReadWritePaths in the unit"
    echo "      7. repoint the static alias in the nginx site"
    echo "      8. restart the service and verify /healthz"
    exit 0
fi

# --------------------------------------------------------------------- execute
log "Stopping $SERVICE"
systemctl stop "$SERVICE" 2>/dev/null || true

if [[ -e "$TARGET" ]]; then
    backup="$TARGET.bak.$(date +%Y%m%d%H%M%S)"
    log "Moving the existing $TARGET to $backup"
    mv "$TARGET" "$backup"
fi

log "Moving $SOURCE to $TARGET"
mv "$SOURCE" "$TARGET"

log "Setting ownership"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$TARGET" --shell /usr/sbin/nologin "$APP_USER"
chown -R "$APP_USER:$APP_USER" "$TARGET"
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$TARGET/instance"

# .venv is gitignored and may not have survived; rebuild it.
log "Creating the virtual environment"
apt-get install -y --no-install-recommends python3-venv >/dev/null 2>&1 || true
rm -rf "$TARGET/.venv"
sudo -u "$APP_USER" python3 -m venv "$TARGET/.venv"
sudo -u "$APP_USER" "$TARGET/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$TARGET/.venv/bin/pip" install --quiet -r "$TARGET/requirements.txt"

# ------------------------------------------------------------------- systemd
UNIT=/etc/systemd/system/$SERVICE.service
if [[ -f "$UNIT" ]]; then
    log "Repointing the systemd unit"
    sed -i "s|^WorkingDirectory=.*|WorkingDirectory=$TARGET|" "$UNIT"
    sed -i "s|^ReadWritePaths=.*|ReadWritePaths=$TARGET/instance|" "$UNIT"
    # ExecStart may span several continuation lines; replace the binary path only.
    sed -i "s|$SOURCE/.venv/bin/gunicorn|$TARGET/.venv/bin/gunicorn|g" "$UNIT"
    sed -i "s|WorkingDirectory=.*|WorkingDirectory=$TARGET|" "$UNIT"
else
    warn "$UNIT not found; install it from deploy/ibshit.service and edit the paths"
fi

# --------------------------------------------------------------------- nginx
SITE=/etc/nginx/sites-available/ibshit
if [[ -f "$SITE" ]]; then
    log "Repointing the nginx static alias"
    sed -i "s|alias [^;]*/app/static/;|alias $TARGET/app/static/;|" "$SITE"
else
    warn "$SITE not found; nothing to repoint"
fi

# The flask wrapper points at the old location too.
WRAPPER=/usr/local/sbin/ibshit-flask
if [[ -f "$WRAPPER" ]]; then
    log "Updating the flask wrapper"
    sed -i "s|$SOURCE|$TARGET|g" "$WRAPPER"
fi

log "Reloading systemd and nginx"
systemctl daemon-reload
nginx -t && systemctl reload nginx

log "Starting $SERVICE"
systemctl restart "$SERVICE"
sleep 3

# --------------------------------------------------------------------- verify
if [[ "$(systemctl is-active "$SERVICE")" != "active" ]]; then
    warn "The service is not active. Recent log lines:"
    journalctl -u "$SERVICE" -n 25 --no-pager | sed 's/^/    /'
    die "fix the error above, then re-run with --apply"
fi

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 --unix-socket "/run/$APP_USER/gunicorn.sock" http://localhost/healthz 2>/dev/null || echo 000)
[[ "$code" == "200" ]] && log "healthz returns 200 through the socket" || warn "healthz returned $code"

echo
log "Done"
cat <<SUMMARY

  App now lives in:  $TARGET
  Database:          $TARGET/instance/quiz.db
  Service:           $SERVICE (active)
  CLI:               sudo -u $APP_USER /usr/local/sbin/ibshit-flask quiz stats

  Check it in the browser, then remove the old copy if there was one. Also
  confirm that nothing still references the old path:
      grep -rn "$SOURCE" /etc/systemd/system /etc/nginx 2>/dev/null
SUMMARY
