#!/usr/bin/env bash
# Diagnose a 502 from nginx for the IB Biology app.
#
#   sudo bash deploy/diagnose.sh
#
# Read-only: it inspects the service, the socket, permissions and logs, and
# prints the likely cause plus the fix. It changes nothing.

set -uo pipefail

APP_USER="ibshit"
APP_DIR="/srv/ibshit"
ENV_FILE="/etc/ibshit.env"
SOCKET="/run/ibshit/gunicorn.sock"
SERVICE="ibshit"

ok()   { printf '  \033[1;32mOK\033[0m    %s\n' "$*"; }
bad()  { printf '  \033[1;31mFAIL\033[0m  %s\n' "$*"; }
warn() { printf '  \033[1;33mWARN\033[0m  %s\n' "$*"; }
head_() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

head_ "The most likely cause, checked first"

# 1. Is the service running? Flask refuses to start in production without a
#    usable SECRET_KEY, which is the single most common reason for a 502 here.
state=$(systemctl is-active "$SERVICE" 2>/dev/null || true)
if [[ "$state" == "active" ]]; then
    ok "systemd service '$SERVICE' is active"
else
    bad "systemd service '$SERVICE' is '$state' (should be 'active')"
fi

# 2. Does the socket nginx proxies to exist?
if [[ -S "$SOCKET" ]]; then
    ok "socket exists: $SOCKET"
    perms=$(stat -c '%A %U:%G' "$SOCKET")
    printf '        %s\n' "$perms"
    # nginx workers run as a different user and must be able to connect.
    if sudo -u www-data test -r "$SOCKET" 2>/dev/null && sudo -u www-data test -w "$SOCKET" 2>/dev/null; then
        ok "nginx user (www-data) can read and write the socket"
    else
        bad "nginx user (www-data) cannot access the socket"
        echo "        fix: the unit sets RuntimeDirectoryMode=0750, which only allows"
        echo "        the app group. Either add www-data to the '$APP_USER' group:"
        echo "            sudo usermod -aG $APP_USER www-data && sudo systemctl restart nginx"
        echo "        or relax the directory:  sudo chmod 0755 /run/$APP_USER"
    fi
else
    bad "socket missing: $SOCKET (gunicorn is not listening)"
fi

# 3. Can we reach the app through the socket, bypassing nginx?
if [[ -S "$SOCKET" ]]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --unix-socket "$SOCKET" http://localhost/healthz 2>/dev/null || echo 000)
    if [[ "$code" == "200" ]]; then
        ok "app answers 200 through the socket (so the app itself is fine)"
        warn "then the fault is in nginx: check 'nginx -T' and the error log below"
    else
        bad "app did not answer through the socket (got '$code')"
    fi
fi

head_ "Configuration"

if [[ -f "$ENV_FILE" ]]; then
    ok "$ENV_FILE exists"
    if grep -q '^SECRET_KEY=' "$ENV_FILE"; then
        len=$(grep '^SECRET_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'" | wc -c)
        if [[ "$len" -gt 20 ]]; then
            ok "SECRET_KEY is set ($((len - 1)) characters)"
        else
            warn "SECRET_KEY looks too short ($((len - 1)) characters)"
        fi
    else
        bad "SECRET_KEY is missing from $ENV_FILE  <-- app cannot start in production"
        echo "        fix: echo \"SECRET_KEY=\$(openssl rand -hex 32)\" | sudo tee -a $ENV_FILE"
    fi
    if grep -qE '^[A-Z_]+=.*['"'"'"]' "$ENV_FILE"; then
        warn "values are quoted; systemd strips quotes but a shell does not"
    fi
    if grep -qE '^[A-Z_]+=[^=]* ' "$ENV_FILE"; then
        warn "a value contains a space; keep values free of spaces"
    fi
else
    bad "$ENV_FILE is missing"
fi

if [[ -r "$APP_DIR/wsgi.py" ]]; then
    ok "$APP_DIR/wsgi.py is readable"
else
    bad "$APP_DIR/wsgi.py is missing or unreadable"
fi

if id -u "$APP_USER" >/dev/null 2>&1; then
    ok "user '$APP_USER' exists"
    if sudo -u "$APP_USER" test -w "$APP_DIR/instance" 2>/dev/null; then
        ok "$APP_USER can write to $APP_DIR/instance"
    else
        bad "$APP_USER cannot write to $APP_DIR/instance"
        echo "        fix: sudo chown -R $APP_USER:$APP_USER $APP_DIR"
    fi
else
    bad "user '$APP_USER' does not exist"
fi

head_ "Last service errors"

# This is usually where the real reason is printed.
journalctl -u "$SERVICE" -n 25 --no-pager 2>/dev/null | sed 's/^/  /' || echo "  (no journal entries)"

head_ "nginx"

nginx -t 2>&1 | sed 's/^/  /'
echo
echo "  --- upstream blocks referencing the app socket ---"
grep -rn "gunicorn.sock" /etc/nginx/ 2>/dev/null | sed 's/^/  /' || echo "  none found: is deploy/nginx-ibshit.conf installed?"
echo
echo "  --- last nginx errors ---"
for log in /var/log/nginx/error.log /var/log/nginx/ibshit.error.log; do
    [[ -f "$log" ]] && { echo "  [$log]"; tail -6 "$log" | sed 's/^/    /'; }
done

head_ "Summary"

cat <<'SUMMARY'
  Work through these in order; the first failure is the one to fix:

  1. If SECRET_KEY is missing  -> add it, then: systemctl restart ibshit
  2. If the service is failed  -> journalctl -u ibshit -n 50 --no-pager
  3. If the socket is missing  -> the service never started; fix (2) first
  4. If the socket exists but nginx cannot use it -> the RuntimeDirectoryMode
     permission note above
  5. If the app answers 200 through the socket but nginx still 502s -> the fault
     is nginx's upstream path or its config

  After any change:
      sudo systemctl restart ibshit
      sudo systemctl reload nginx
      curl -s localhost/healthz
SUMMARY
