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
# Where the application lives. Override by exporting APP_DIR before running:
#   sudo APP_DIR=/tmp/ibshit bash deploy/fix-duckdns.sh your.domain
APP_DIR="${APP_DIR:-/srv/ibshit}"
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
# Three situations are possible, and all must work:
#   1. $APP_DIR is a git checkout            -> update it in place
#   2. $APP_DIR does not exist or is empty   -> clone into it
#   3. $APP_DIR exists with files but no .git-> recover, since `git clone` refuses
#      a non-empty target and would abort the whole installer
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

if [[ -d "$APP_DIR/.git" ]]; then
    log "Updating existing checkout in $APP_DIR"
    # Never prompt: the installer must not hang waiting for credentials.
    GIT_TERMINAL_PROMPT=0 git -C "$APP_DIR" fetch --quiet origin
    if ! git -C "$APP_DIR" reset --hard --quiet origin/main; then
        die "could not update $APP_DIR to origin/main.
  Check the remote and your access:
    git -C $APP_DIR remote -v
    GIT_TERMINAL_PROMPT=0 git -C $APP_DIR fetch origin"
    fi
    # Keep the checked-out files owned by the service account.
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
elif [[ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then
    # A previous interrupted run left a partial tree. Move it aside rather than
    # deleting anything, then start clean.
    BACKUP="${APP_DIR}.bak.$(date +%Y%m%d%H%M%S)"
    warn "$APP_DIR exists but is not a git checkout; moving it to $BACKUP"

    # The database lives in instance/, so carry it across: resetting the code
    # must never discard a user's answers.
    KEEP_INSTANCE=""
    if [[ -d "$APP_DIR/instance" ]]; then
        KEEP_INSTANCE="$(mktemp -d)"
        cp -a "$APP_DIR/instance/." "$KEEP_INSTANCE/" 2>/dev/null || true
        log "Preserving the existing instance/ directory (database and image cache)"
    fi

    mv "$APP_DIR" "$BACKUP"
    log "Cloning $REPO"
    git clone --quiet "$REPO" "$APP_DIR"

    if [[ -n "$KEEP_INSTANCE" ]]; then
        # Creating this directory must never abort the restore below, or the
        # preserved database would be stranded in the temporary directory.
        install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$APP_DIR/instance" 2>/dev/null \
            || mkdir -p "$APP_DIR/instance"

        if cp -a "$KEEP_INSTANCE/." "$APP_DIR/instance/" 2>/dev/null; then
            chown -R "$APP_USER:$APP_USER" "$APP_DIR/instance" 2>/dev/null || true
            log "Restored instance/ (your existing database has been kept)"
        else
            warn "Could not restore instance/ automatically."
            warn "Your previous database is still intact at: $KEEP_INSTANCE"
            warn "Copy it into place with: cp -a $KEEP_INSTANCE/. $APP_DIR/instance/"
        fi
        rm -rf "$KEEP_INSTANCE" 2>/dev/null || true
    fi

    log "The previous contents are still in $BACKUP (remove it once you are happy)"
else
    log "Cloning $REPO"
    git clone --quiet "$REPO" "$APP_DIR"
fi

# ---------------------------------------------------------------- virtualenv
# .venv is in .gitignore, so `git pull` never restores it: it has to exist on
# disk. Any script that runs the app's CLI must be able to recreate it, or the
# first `git pull` after a fresh clone breaks every command with "not found".
# Run a command as the service account.
#
# `sudo -u <user>` fails when the target is the user already running the script
# ("sudo: unknown user", or a password prompt), which is what happens on a small
# host where the operator works as the app user. Only switch user when we are
# root and the account actually differs.
as_app_user() {
    if [[ $EUID -eq 0 && "$(id -un)" != "$APP_USER" ]]; then
        sudo -u "$APP_USER" "$@"
    else
        "$@"
    fi
}


ensure_venv() {
    if [[ -x "$APP_DIR/.venv/bin/python" ]] \
       && "$APP_DIR/.venv/bin/python" -c 'import flask, flask_login' 2>/dev/null; then
        log "Python environment present"
        return 0
    fi

    if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
        warn "The Python environment exists but is missing dependencies; repairing"
    else
        warn "$APP_DIR/.venv is missing (it is not tracked by git, so it must be recreated)"
    fi

    apt-get install -y --no-install-recommends python3-venv >/dev/null 2>&1 || true

    # Build it as the service account so the files are not root-owned.
    log "Creating the virtual environment"
    rm -rf "$APP_DIR/.venv"
    as_app_user python3 -m venv "$APP_DIR/.venv"
    sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
    log "Installing Python dependencies"
    sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
    log "Python environment ready"
}

# --- virtualenv ------------------------------------------------------------
ensure_venv

# --- ownership -------------------------------------------------------------
# The application must run as APP_USER, not root: otherwise it cannot create
# instance/, and anything it does create (the database) ends up root-owned and
# unreadable by the systemd service.
log "Setting ownership of $APP_DIR to $APP_USER"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
# An earlier run as root may have left root-owned files behind.
chown -R "$APP_USER:$APP_USER" "$APP_DIR/instance" 2>/dev/null || true
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$APP_DIR/instance"

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
# A small wrapper loads /etc/ibshit.env as APP_USER. Sourcing the file with
# `set -a` handles values containing spaces and keeps secrets out of the
# process list, unlike `env $(...)`.
FLASK_WRAPPER=/usr/local/sbin/ibshit-flask
log "Installing the flask wrapper at $FLASK_WRAPPER"
cat > "$FLASK_WRAPPER" <<WRAPPER
#!/bin/sh
# Generated by deploy/bootstrap-debian.sh. Runs the app's flask CLI with the
# service environment loaded.
set -a
. "$ENV_FILE"
set +a
cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/flask" --app "$APP_DIR/wsgi.py" "\$@"
WRAPPER
chmod 0750 "$FLASK_WRAPPER"
chown root:"$APP_USER" "$FLASK_WRAPPER"

run_flask() {
    as_app_user "$FLASK_WRAPPER" "$@"
}

# The wrapper sources the env file, so a syntax error there would surface as a
# confusing failure later. Check it now, as the service account would see it.
if ! as_app_user sh -n "$ENV_FILE" 2>/dev/null; then
    die "$ENV_FILE is not valid shell syntax, so the app cannot read it.
  Values must be plain KEY=value with no quotes and no spaces, e.g.
    SECRET_KEY=abc123
  See deploy/ibshit.env.example."
fi

log "Preparing the database and importing questions"
run_flask quiz seed
run_flask quiz import "$APP_DIR/seed/rdojo.json"

# The service account must own the database and image cache it just created.
chown -R "$APP_USER:$APP_USER" "$APP_DIR/instance"

# --- sanity check ----------------------------------------------------------
# Confirm the service account can actually write, with a message that says what
# to do rather than a traceback from inside Flask.
if ! as_app_user test -w "$APP_DIR/instance"; then
    die "$APP_USER cannot write to $APP_DIR/instance. Check ownership with:
    ls -ld $APP_DIR $APP_DIR/instance
  and fix it with:
    chown -R $APP_USER:$APP_USER $APP_DIR"
fi
log "Verified: $APP_USER can write to $APP_DIR/instance"

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
