#!/usr/bin/env bash
# Bring the app up behind nginx for a DuckDNS domain, and enable HTTPS.
#
#   sudo bash deploy/fix-duckdns.sh gromas.duckdns.org
#
# Idempotent: safe to run repeatedly. It fixes the causes of a 502 in order,
# then obtains a Let's Encrypt certificate. Nothing is deleted.

set -euo pipefail

DOMAIN="${1:-}"
APP_USER="ibshit"
APP_DIR="/srv/ibshit"
ENV_FILE="/etc/ibshit.env"
SOCKET="/run/ibshit/gunicorn.sock"
FLASK_WRAPPER="/usr/local/sbin/ibshit-flask"
SERVICE="ibshit"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -n "$DOMAIN" ]] || die "usage: sudo bash $0 yourname.duckdns.org"

# ---------------------------------------------------------------- preconditions
[[ -f "$APP_DIR/wsgi.py" ]] || die "$APP_DIR/wsgi.py not found. Run deploy/bootstrap-debian.sh first."
id -u "$APP_USER" >/dev/null 2>&1 || die "user '$APP_USER' does not exist. Run deploy/bootstrap-debian.sh first."

# ---------------------------------------------------------------- 1. secret key
if [[ ! -f "$ENV_FILE" ]]; then
    log "Creating $ENV_FILE"
    install -m 0640 -o root -g "$APP_USER" "$APP_DIR/deploy/ibshit.env.example" "$ENV_FILE"
fi

if ! grep -q '^SECRET_KEY=.\+' "$ENV_FILE" || grep -q '^SECRET_KEY=replace-me' "$ENV_FILE"; then
    log "Generating a SECRET_KEY"
    sed -i '/^SECRET_KEY=/d' "$ENV_FILE"
    printf 'SECRET_KEY=%s\n' "$(openssl rand -hex 32)" >> "$ENV_FILE"
else
    log "SECRET_KEY already set"
fi
grep -q '^FLASK_ENV=' "$ENV_FILE" || echo 'FLASK_ENV=production' >> "$ENV_FILE"

# A quoted or spaced value works for systemd but breaks the shell-based CLI
# wrapper, so warn rather than silently producing two different configurations.
if grep -qE '^[A-Z_]+=.*['"'"'"]' "$ENV_FILE"; then
    warn "$ENV_FILE contains quotes; systemd strips them but a shell does not"
fi

# ------------------------------------------------------- 2. ownership and CLI
log "Ensuring $APP_USER owns $APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$APP_DIR/instance"

# The CLI must run as the app user, otherwise Flask cannot create instance/.
if [[ ! -x "$FLASK_WRAPPER" ]]; then
    log "Installing $FLASK_WRAPPER"
    cat > "$FLASK_WRAPPER" <<WRAPPER
#!/bin/sh
set -a
. "$ENV_FILE"
set +a
cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/flask" --app "$APP_DIR/wsgi.py" "\$@"
WRAPPER
    chmod 0750 "$FLASK_WRAPPER"
    chown root:"$APP_USER" "$FLASK_WRAPPER"
fi

# ------------------------------------------------------------------ 3. service
log "Installing the systemd unit"
install -m 0644 "$APP_DIR/deploy/ibshit.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE"

# Give it a moment, then check rather than assuming.
sleep 2
if [[ "$(systemctl is-active "$SERVICE")" != "active" ]]; then
    warn "The service did not start. Last log lines:"
    journalctl -u "$SERVICE" -n 20 --no-pager | sed 's/^/    /'
    die "fix the error above, then re-run this script"
fi
log "Service is active"

# ---------------------------------------------------- 4. socket reachable by nginx
# nginx workers run as www-data and must be able to open the app's socket.
if [[ -S "$SOCKET" ]]; then
    if ! sudo -u www-data test -w "$SOCKET" 2>/dev/null; then
        log "Granting nginx access to the socket"
        # RuntimeDirectoryMode=0750 in the unit only admits the app group, so add
        # nginx's user to that group. The permission on /run/<user> is the cause.
        usermod -aG "$APP_USER" www-data
        chmod 0755 "/run/$APP_USER"
        systemctl restart nginx
    fi
    log "Socket present and usable: $SOCKET"
else
    die "socket $SOCKET is missing even though the service is active. Check:
    journalctl -u $SERVICE -n 30 --no-pager"
fi

# ------------------------------------------------------------------ 5. nginx site
log "Installing the nginx site for $DOMAIN"
install -m 0644 "$APP_DIR/deploy/nginx-ibshit.conf" /etc/nginx/sites-available/ibshit
sed -i "s|server_name .*;|server_name $DOMAIN;|" /etc/nginx/sites-available/ibshit
ln -sf /etc/nginx/sites-available/ibshit /etc/nginx/sites-enabled/ibshit
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# ------------------------------------------------------------------ 6. verify
log "Checking the app through nginx"
for attempt in 1 2 3 4 5; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Host: $DOMAIN" http://127.0.0.1/healthz || true)
    [[ "$code" == "200" ]] && break
    sleep 1
done
if [[ "$code" != "200" ]]; then
    warn "localhost returned '$code' instead of 200 — last nginx errors:"
    tail -10 /var/log/nginx/error.log 2>/dev/null | sed 's/^/    /'
    die "the app is not answering through nginx yet"
fi
log "healthz returns 200 through nginx"

# ------------------------------------------------------------------ 7. firewall
if command -v ufw >/dev/null 2>&1; then
    log "Allowing HTTP/HTTPS through ufw"
    ufw allow OpenSSH >/dev/null 2>&1 || true
    ufw allow 'Nginx Full' >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------------ 8. HTTPS
if ! command -v certbot >/dev/null 2>&1; then
    log "Installing certbot"
    apt-get update -qq
    apt-get install -y --no-install-recommends certbot python3-certbot-nginx
fi

log "Requesting a certificate for $DOMAIN"
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
        --register-unsafely-without-email --redirect; then
    log "HTTPS enabled"
    # Tell the app it is behind TLS so session cookies get the Secure flag.
    grep -q '^SESSION_COOKIE_SECURE=' "$ENV_FILE" \
        && sed -i 's|^SESSION_COOKIE_SECURE=.*|SESSION_COOKIE_SECURE=1|' "$ENV_FILE" \
        || echo 'SESSION_COOKIE_SECURE=1' >> "$ENV_FILE"
    grep -q '^PREFERRED_URL_SCHEME=' "$ENV_FILE" \
        && sed -i 's|^PREFERRED_URL_SCHEME=.*|PREFERRED_URL_SCHEME=https|' "$ENV_FILE" \
        || echo 'PREFERRED_URL_SCHEME=https' >> "$ENV_FILE"
    grep -q '^TRUST_PROXY_HEADERS=' "$ENV_FILE" \
        && sed -i 's|^TRUST_PROXY_HEADERS=.*|TRUST_PROXY_HEADERS=1|' "$ENV_FILE" \
        || echo 'TRUST_PROXY_HEADERS=1' >> "$ENV_FILE"
    systemctl restart "$SERVICE"
else
    warn "certbot failed — the site still works over plain HTTP."
    warn "DuckDNS domains are on the public suffix list, so this usually means"
    warn "port 80 was not reachable from the internet when certbot ran. Check your"
    warn "VPS firewall and any provider-level firewall, then re-run this script."
fi

# ------------------------------------------------------------------ summary
echo
log "Done"
https_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$DOMAIN/healthz" || echo 000)
http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$DOMAIN/healthz" || echo 000)
cat <<SUMMARY

  http://$DOMAIN/healthz    -> $http_code
  https://$DOMAIN/healthz   -> $https_code

  If both are 200, open https://$DOMAIN/ in a browser.

  Useful next:
      sudo bash $APP_DIR/deploy/diagnose.sh     # if anything still fails
      sudo -u $APP_USER $FLASK_WRAPPER quiz stats
      sudo journalctl -u $SERVICE -f

  WARNING: the app has no login. Anyone who can reach it can read and reset the
  revision history. For a quick look that is fine; to leave it up, put basic auth
  in front of it or use a tunnel. See DEPLOY.md section 9.
SUMMARY
