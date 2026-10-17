#!/usr/bin/env bash
# Wipe every trace of this app from the machine and install it again from
# scratch, ending with a working site behind nginx.
#
#   sudo bash fresh-install.sh yourname.duckdns.org
#   sudo bash fresh-install.sh yourname.duckdns.org /opt/ibshit   # custom path
#
# Destructive: it removes the service, the unit file, the nginx site, the app
# directory, the flask wrapper and the dedicated user. Any existing database is
# copied to /root/ibshit-backups before it is deleted, so progress is recoverable.
#
# Safe to re-run: each removal tolerates the thing already being gone.

set -euo pipefail

# Every stage is numbered and printed, so a log always shows how far the install
# got — the previous attempt failed silently between two stages.
trap 'echo "[x] błąd w linii $LINENO — instalacja przerwana"; exit 1' ERR

# Every command in this script is guarded by `set -e`; the trap above reports where
# it stopped. Note: never pipe an app command into `head` — SIGPIPE plus
# `pipefail` looks like a failure and aborts an otherwise successful install.

DOMAIN="${1:-}"
PREFIX="${2:-/srv/ibshit}"
APP_USER="ibshit"
SERVICE="ibshit"
SITE="ibshit"
ENV_FILE="/etc/ibshit.env"
WRAPPER="/usr/local/sbin/ibshit-flask"
REPO="${REPO:-https://github.com/Dellis908/ibshit.git}"
SOCKET="/run/$APP_USER/gunicorn.sock"
BACKUP_DIR="/root/ibshit-backups"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

as_app_user() {
    if [[ $EUID -eq 0 && "$(id -un)" != "$APP_USER" ]]; then
        sudo -u "$APP_USER" "$@"
    else
        "$@"
    fi
}

[[ $EUID -eq 0 ]] || die "uruchom przez sudo"
[[ -n "$DOMAIN" ]] || die "użycie: sudo bash $0 twoja-domena.duckdns.org [katalog]"
[[ "$PREFIX" == /tmp/* ]] && die "nie instaluj w /tmp — systemd daje usłudze prywatny /tmp, a /tmp jest czyszczony przy restarcie. Użyj /srv, /opt albo /home."

echo
echo "  To usunie aplikację i zainstaluje ją od nowa."
echo
echo "    domena:        $DOMAIN"
echo "    katalog:       $PREFIX"
echo "    usługa:        $SERVICE"
echo "    użytkownik:    $APP_USER"
echo "    konfiguracja:  $ENV_FILE"
echo "    backup bazy:   $BACKUP_DIR"
echo

# ─────────────────────────────────────────────────────── 1. zatrzymaj i usuń
log "Zatrzymuję usługę"
systemctl stop "$SERVICE" 2>/dev/null || true
systemctl disable "$SERVICE" 2>/dev/null || true
rm -f "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload 2>/dev/null || true
systemctl reset-failed "$SERVICE" 2>/dev/null || true

log "Usuwam konfigurację nginx"
rm -f "/etc/nginx/sites-enabled/$SITE" "/etc/nginx/sites-available/$SITE"

log "Usuwam wrapper CLI"
rm -f "$WRAPPER"

# ─────────────────────────────────────────────────────────── 2. backup bazy
if [[ -f "$PREFIX/instance/quiz.db" ]]; then
    install -d -m 0700 "$BACKUP_DIR"
    stamp=$(date +%Y%m%d%H%M%S)
    cp "$PREFIX/instance/quiz.db" "$BACKUP_DIR/quiz-$stamp.db"
    log "Baza zachowana: $BACKUP_DIR/quiz-$stamp.db"
fi
# A database left by an earlier failed attempt elsewhere is worth keeping too.
for stray in /tmp/ibshit/instance/quiz.db "$PREFIX.bak."*/instance/quiz.db; do
    if [[ -f "$stray" ]]; then
        install -d -m 0700 "$BACKUP_DIR"
        cp "$stray" "$BACKUP_DIR/$(basename "$(dirname "$(dirname "$stray")")")-$(date +%s).db" 2>/dev/null || true
        warn "znaleziono inną bazę i zachowano ją: $stray"
    fi
done

# ────────────────────────────────────────────────────── 3. usuń katalog i user
log "Usuwam $PREFIX"
rm -rf "$PREFIX"

# A leftover directory from the old /tmp attempt.
[[ -d /tmp/ibshit ]] && { warn "usuwam też /tmp/ibshit"; rm -rf /tmp/ibshit; }

log "Usuwam użytkownika i grupę $APP_USER"
if id -u "$APP_USER" >/dev/null 2>&1; then
    pkill -u "$APP_USER" 2>/dev/null || true
    userdel "$APP_USER" 2>/dev/null || warn "nie udało się usunąć użytkownika (może być w użyciu)"
fi
# `userdel` leaves the group behind, and `useradd --system` then refuses with
# "group <name> exists", which under `set -e` aborts the whole install. Removing
# the group as well is what makes re-running this script actually work.
if getent group "$APP_USER" >/dev/null 2>&1; then
    groupdel "$APP_USER" 2>/dev/null && log "Usunięto grupę $APP_USER" \
        || warn "nie udało się usunąć grupy $APP_USER (może mieć członków)"
fi

# ───────────────────────────────────────────────────────────── 4. zależności
log "Instaluję pakiety systemowe"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git python3 python3-venv python3-pip nginx ca-certificates curl sqlite3 rsync

log "Tworzę użytkownika $APP_USER"
if id -u "$APP_USER" >/dev/null 2>&1; then
    log "Użytkownik $APP_USER już istnieje, zostawiam"
elif getent group "$APP_USER" >/dev/null 2>&1; then
    # A previous run left the group (or www-data was added to it).
    useradd --system --home "$PREFIX" --shell /usr/sbin/nologin -g "$APP_USER" "$APP_USER" \
        || die "nie udało się utworzyć użytkownika $APP_USER"
else
    useradd --system --home "$PREFIX" --shell /usr/sbin/nologin "$APP_USER" \
        || die "nie udało się utworzyć użytkownika $APP_USER"
fi

# ──────────────────────────────────────────────────────────────── 5. klonuj
log "Klonuję $REPO"
git clone --quiet "$REPO" "$PREFIX"

# Ownership must be set BEFORE the environment is built: git clones as root, so
# the tree starts root-owned and `python3 -m venv` (run as the service account)
# would fail with "Permission denied". This ordering was the bug.
log "Ustawiam właściciela $PREFIX na $APP_USER"
chown -R "$APP_USER:$APP_USER" "$PREFIX"
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$PREFIX/instance"
if ! as_app_user test -w "$PREFIX"; then
    die "$APP_USER nie może pisać do $PREFIX
  napraw: chown -R $APP_USER:$APP_USER $PREFIX"
fi

# ──────────────────────────────────────────────────────────── 6. środowisko
log "Tworzę środowisko Python"
rm -rf "$PREFIX/.venv"   # a partial venv from a failed run would confuse the next
as_app_user python3 -m venv "$PREFIX/.venv" || die "nie udało się utworzyć $PREFIX/.venv"
# Zawsze przez `python -m pip`: skrypt .venv/bin/pip ma zaszyty shebang, który
# przestaje działać po przeniesieniu katalogu.
as_app_user "$PREFIX/.venv/bin/python" -m pip install --quiet --upgrade pip
as_app_user "$PREFIX/.venv/bin/python" -m pip install --quiet -r "$PREFIX/requirements.txt"

# ────────────────────────────────────────────────────────────── 7. konfiguracja
if [[ ! -f "$ENV_FILE" ]]; then
    log "Tworzę $ENV_FILE"
    echo "# Konfiguracja IB Biology Revision" > "$ENV_FILE"
    echo "FLASK_ENV=production" >> "$ENV_FILE"
fi

if grep -q '^SECRET_KEY=.\+' "$ENV_FILE"; then
    log "SECRET_KEY już jest, zostawiam"
else
    log "Generuję SECRET_KEY"
    printf 'SECRET_KEY=%s\n' "$(openssl rand -hex 32)" >> "$ENV_FILE"
fi

# Zamykam rejestrację, żeby nikt obcy nie zakładał kont po wystawieniu na świat.
if ! grep -q '^ALLOW_REGISTRATION=' "$ENV_FILE"; then
    echo "ALLOW_REGISTRATION=1   # pierwsze konto utwórz w przeglądarce, potem zmień na 0" >> "$ENV_FILE"
fi

chown root:"$APP_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

if ! as_app_user test -w "$PREFIX/instance"; then
    die "$APP_USER nie może pisać do $PREFIX/instance
  napraw: chown -R $APP_USER:$APP_USER $PREFIX"
fi
if ! as_app_user sh -n "$ENV_FILE" 2>/dev/null; then
    die "$ENV_FILE ma złą składnię. Wartości muszą być w formie KLUCZ=wartosc,
  bez cudzysłowów i bez spacji w środku."
fi

# ─────────────────────────────────────────────────────────────────── 8. wrapper
log "Instaluję wrapper CLI"
cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/sh
# Wygenerowane przez fresh-install.sh. Uruchamia CLI aplikacji z jej środowiskiem.
set -a
. "$ENV_FILE"
set +a
cd "$PREFIX"
exec "$PREFIX/.venv/bin/flask" --app "$PREFIX/wsgi.py" "\$@"
WRAPPER_EOF
chmod 0750 "$WRAPPER"
chown root:"$APP_USER" "$WRAPPER"

# ───────────────────────────────────────────────────────────────── 9. baza pytań
log "Ładuję pytania"
as_app_user "$WRAPPER" quiz seed
as_app_user "$WRAPPER" quiz import "$PREFIX/seed/additional.json"
if [ -f "$PREFIX/seed/chemistry.json" ]; then
    as_app_user "$WRAPPER" quiz import "$PREFIX/seed/chemistry.json"
fi
# Nie używaj `| head` na poleceniach aplikacji: `head` zamyka potok, aplikacja
# ginie od SIGPIPE, a `set -o pipefail` zgłasza to jako błąd i przerywa instalację.
as_app_user "$WRAPPER" quiz stats >/tmp/quiz-stats.txt 2>&1 || true
sed -n '1,4p' /tmp/quiz-stats.txt

# ──────────────────────────────────────────────────────────────────── 10. systemd
log "Instaluję usługę systemd"
cat > "/etc/systemd/system/$SERVICE.service" <<UNIT_EOF
[Unit]
Description=IB Biology revision app (gunicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$PREFIX
EnvironmentFile=$ENV_FILE
ExecStart=$PREFIX/.venv/bin/gunicorn \\
    --bind unix:/run/$APP_USER/gunicorn.sock \\
    --workers 3 --threads 2 --timeout 60 --graceful-timeout 30 \\
    --access-logfile - --error-logfile - \\
    wsgi:app
RuntimeDirectory=$APP_USER
RuntimeDirectoryMode=0770
Restart=on-failure
RestartSec=3
KillMode=mixed
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
MemoryDenyWriteExecute=true
ReadWritePaths=$PREFIX/instance

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE"
sleep 3

if [[ "$(systemctl is-active "$SERVICE")" != "active" ]]; then
    warn "Usługa nie wstała. Ostatnie linie logu:"
    journalctl -u "$SERVICE" -n 25 --no-pager | sed 's/^/    /'
    die "napraw błąd powyżej i uruchom skrypt ponownie"
fi
log "Usługa działa"

# ────────────────────────────────────────────────────────────────────── 11. nginx
log "Konfiguruję nginx dla $DOMAIN"
cat > "/etc/nginx/sites-available/$SITE" <<NGINX_EOF
upstream ibshit_app {
    server unix:/run/$APP_USER/gunicorn.sock fail_timeout=0;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    access_log /var/log/nginx/ibshit.access.log;
    error_log  /var/log/nginx/ibshit.error.log;
    client_max_body_size 25m;

    location /static/ {
        alias $PREFIX/app/static/;
        access_log off;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        include proxy_params;
        proxy_pass http://ibshit_app;
        proxy_redirect off;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 75s;
        proxy_connect_timeout 15s;
    }

    location = /healthz {
        include proxy_params;
        proxy_pass http://ibshit_app;
        access_log off;
    }
}
NGINX_EOF

ln -sf "/etc/nginx/sites-available/$SITE" "/etc/nginx/sites-enabled/$SITE"
rm -f /etc/nginx/sites-enabled/default
nginx -t || die "konfiguracja nginx jest błędna (patrz wyżej)"
systemctl reload nginx

# nginx (www-data) musi móc wejść do /run/<user> i otworzyć socket.
if ! sudo -u www-data test -w "$SOCKET" 2>/dev/null; then
    log "Dodaję www-data do grupy $APP_USER"
    usermod -aG "$APP_USER" www-data
    chmod 0770 "/run/$APP_USER" 2>/dev/null || true
    systemctl restart nginx
fi

# ──────────────────────────────────────────────────────────────────────── 12. test
log "Sprawdzam działanie"
code=""
for attempt in 1 2 3 4 5; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        --unix-socket "$SOCKET" http://localhost/healthz 2>/dev/null || true)
    [[ "$code" == "200" ]] && break
    sleep 1
done
[[ "$code" == "200" ]] && log "healthz przez socket: 200" || warn "healthz przez socket: $code"

local_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Host: $DOMAIN" http://127.0.0.1/ 2>/dev/null || true)
log "strona przez nginx (Host: $DOMAIN): $local_code"

# ────────────────────────────────────────────────────────────────────── 13. firewall
if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH >/dev/null 2>&1 || true
    ufw allow 'Nginx Full' >/dev/null 2>&1 || true
    log "ufw: przepuszczam SSH i HTTP/HTTPS"
fi

# ───────────────────────────────────────────────────────────────────────── 14. HTTPS
if ! command -v certbot >/dev/null 2>&1; then
    log "Instaluję certbot"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends certbot python3-certbot-nginx
fi

log "Wystawiam certyfikat TLS dla $DOMAIN (limit 3 minuty)"
# --non-interactive alone can still block on DNS or the ACME handshake, and this
# script must never hang: a stuck install is what broke the previous attempt.
if timeout 180 certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
        --register-unsafely-without-email --redirect; then
    log "HTTPS włączone"
    sed -i '/^SESSION_COOKIE_SECURE=/d; /^PREFERRED_URL_SCHEME=/d; /^TRUST_PROXY_HEADERS=/d' "$ENV_FILE"
    {
        echo "SESSION_COOKIE_SECURE=1"
        echo "PREFERRED_URL_SCHEME=https"
        echo "TRUST_PROXY_HEADERS=1"
    } >> "$ENV_FILE"
    systemctl restart "$SERVICE"
    sleep 2
else
    warn "certbot nie powiódł się — strona działa po HTTP."
    warn "Zwykle znaczy to, że port 80 nie był osiągalny z internetu w tym momencie."
    warn "Sprawdź firewall dostawcy VPS i uruchom: sudo certbot --nginx -d $DOMAIN"
fi

# ───────────────────────────────────────────────────────────────────────── 15. podsumowanie
http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$DOMAIN/healthz" 2>/dev/null || echo 000)
https_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$DOMAIN/healthz" 2>/dev/null || echo 000)

echo
log "GOTOWE"
cat <<SUMMARY

  http://$DOMAIN/healthz    -> $http_code
  https://$DOMAIN/healthz   -> $https_code

  Otwórz w przeglądarce:  https://$DOMAIN/
  PIERWSZE konto, które utworzysz, zostaje administratorem.

  Potem zamknij rejestrację, żeby nikt obcy nie zakładał kont:
      sed -i 's/^ALLOW_REGISTRATION=.*/ALLOW_REGISTRATION=0/' $ENV_FILE
      systemctl restart $SERVICE

  Przydatne:
      systemctl status $SERVICE
      journalctl -u $SERVICE -f
      as_app_user  ->  sudo -u $APP_USER $WRAPPER auth list
      sudo -u $APP_USER $WRAPPER quiz stats
      sudo -u $APP_USER $WRAPPER auth passwd twoj@email.com
      sudo -u $APP_USER $WRAPPER auth create-admin ty@example.com

  Pliki:
      kod        $PREFIX
      config     $ENV_FILE
      baza       $PREFIX/instance/quiz.db
      backup     $BACKUP_DIR

  Minione bazy (jeśli były) leżą w $BACKUP_DIR i możesz je podmienić na
  $PREFIX/instance/quiz.db po zatrzymaniu usługi, żeby odzyskać postęp.
SUMMARY
