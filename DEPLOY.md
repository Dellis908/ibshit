# Deploying on Debian 13 (trixie)

A step-by-step guide to running this app on a home server. Written for a fresh
Debian 13 install; adapt paths if yours differ.

The end result is:

- the app running under **gunicorn**, supervised by **systemd**
- **nginx** in front of it on port 80/443
- the database and image cache on disk, owned by a dedicated service account
- automatic restart on crash and on boot

Everything below assumes you have `sudo` and can SSH into the box.

> **Shortcut:** `deploy/bootstrap-debian.sh` automates every step below. Read the
> manual steps anyway — you will want to understand what it did.
>
> **Do not install this under `/tmp`.** On Debian, systemd can give a service its
> own private `/tmp` (`PrivateTmp`), so the app and nginx would see different
> filesystems, and `/tmp` is cleared on boot. Use `/srv/ibshit` (the default) or
> any directory under `/srv`, `/opt` or `/home`. If the app already lives in
> `/tmp`, move it with `deploy/move-app.sh` — see
> [Moving an existing install](#moving-an-existing-install).

---

## 0. Before you start

**Expose this to the internet only deliberately.** The app has no login: anyone
who can reach it can read and reset the revision progress. On a home network
that is usually fine; if you forward a port, add authentication (see
[Securing it](#9-securing-it) near the end).

**Python version:** Debian 13 ships Python 3.13, which this app supports
(tested on 3.11 and 3.12; the code uses nothing newer than 3.9 syntax).

---

## 1. Install the system packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx ca-certificates curl
```

`python3-venv` is essential — Debian separates it from the base Python package,
and `python3 -m venv` fails without it.

Check what you have:

```bash
python3 --version     # expect 3.13.x on Debian 13
nginx -v
```

---

## 2. Create a service account

The app should not run as root or as your login user. A system account with no
shell and no home login is the right shape:

```bash
sudo useradd --system --home /srv/ibshit --shell /usr/sbin/nologin ibshit
sudo mkdir -p /srv/ibshit
sudo chown ibshit:ibshit /srv/ibshit
```

---

## 3. Deploy the code

```bash
sudo -u ibshit git clone https://github.com/Dellis908/ibshit.git /srv/ibshit
```

If the repository is private, use a deploy key or a token instead:

```bash
sudo -u ibshit git clone https://<token>@github.com/Dellis908/ibshit.git /srv/ibshit
```

Then create the virtualenv and install dependencies:

```bash
sudo -u ibshit python3 -m venv /srv/ibshit/.venv
sudo -u ibshit /srv/ibshit/.venv/bin/pip install --upgrade pip
sudo -u ibshit /srv/ibshit/.venv/bin/pip install -r /srv/ibshit/requirements.txt
```

This installs Flask, Flask-SQLAlchemy, python-dotenv and gunicorn. It does **not**
install the scraper dependencies (selenium, pdfplumber) — those are only needed
if you intend to run the importers on the server.

> **Note for Python 3.13:** `requirements.txt` pins `Flask==3.0.3` with
> `SQLAlchemy>=2.0`, which supports 3.13. If pip reports a conflict, install
> unpinned: `pip install flask flask-sqlalchemy python-dotenv gunicorn`.

---

## 4. Configure secrets

The app reads configuration from environment variables. Keep them outside the
repository, owned by root, readable only by the service:

```bash
sudo install -m 0640 -o root -g ibshit \
    /srv/ibshit/deploy/ibshit.env.example /etc/ibshit.env

# Generate a real SECRET_KEY and write it in
sudo sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" /etc/ibshit.env

sudo nano /etc/ibshit.env      # review, set FLASK_ENV=production
```

Write values as plain `KEY=value` — **no quotes and no spaces**. systemd strips
quotes when it reads the file but a shell does not, so a quoted value would work
for the service and break the command-line tools.

`SECRET_KEY` signs session cookies. The default in the repository is a
development placeholder — replacing it here is required, not optional. Changing
it later invalidates existing sessions (people just re-answer; no data is lost).

Check it looks right:

```bash
sudo grep -v '^#' /etc/ibshit.env | grep -v '^$'
```

---

## 5. Create the database and load the questions

**Run these as the `ibshit` user, not as root.** `/srv/ibshit` is owned by
`ibshit`, so root cannot create `instance/` inside it — and if you force it, the
database ends up root-owned and the service cannot read it.

The supplied bootstrap script installs a small wrapper at
`/usr/local/sbin/ibshit-flask` that loads `/etc/ibshit.env` and runs the CLI as
the service user. Create the same thing manually:

```bash
sudo tee /usr/local/sbin/ibshit-flask >/dev/null <<'WRAPPER'
#!/bin/sh
set -a
. /etc/ibshit.env
set +a
cd /srv/ibshit
exec /srv/ibshit/.venv/bin/flask --app /srv/ibshit/wsgi.py "$@"
WRAPPER

sudo chmod 0750 /usr/local/sbin/ibshit-flask
sudo chown root:ibshit /usr/local/sbin/ibshit-flask
```

Then load the questions:

```bash
sudo -u ibshit /usr/local/sbin/ibshit-flask quiz seed
sudo -u ibshit /usr/local/sbin/ibshit-flask quiz import /srv/ibshit/seed/additional.json
```

Or, if you prefer one-off commands, the equivalent without a wrapper is:

```bash
cd /srv/ibshit
sudo -u ibshit sh -c 'set -a; . /etc/ibshit.env; set +a; \
    exec /srv/ibshit/.venv/bin/flask --app /srv/ibshit/wsgi.py quiz seed'
```

The wrapper exists because `env $(grep -v "^#" /etc/ibshit.env | xargs)` breaks on
values containing spaces and exposes secrets in the process list.

The first command creates `instance/quiz.db` and loads the 154 built-in
questions; the second adds the 2,857 imported ones (including the 613 Village
questions and their diagrams under `app/static/img/`). Check:

```bash
sudo -u ibshit /usr/local/sbin/ibshit-flask quiz stats
```

Make sure the service account owns what it needs to write:

```bash
sudo chown -R ibshit:ibshit /srv/ibshit/instance
sudo -u ibshit mkdir -p /srv/ibshit/instance/images
```

> `wsgi.py` also creates missing tables on boot, so a fresh deploy works without
> this step — but importing explicitly means you find any problem now rather
> than in the logs later.

---

## 6. Run it under systemd

```bash
sudo install -m 0644 /srv/ibshit/deploy/ibshit.service /etc/systemd/system/ibshit.service
sudo systemctl daemon-reload
sudo systemctl enable --now ibshit
sudo systemctl status ibshit
```

It should report `active (running)`. Follow the logs while you test:

```bash
sudo journalctl -u ibshit -f
```

Useful commands:

```bash
sudo systemctl restart ibshit     # after a config or code change
sudo systemctl stop ibshit
sudo journalctl -u ibshit --since "10 min ago"
```

The unit binds gunicorn to a **unix socket** at `/run/ibshit/gunicorn.sock`, not
a TCP port. That keeps the app off the network entirely and leaves nginx as the
only thing listening. `RuntimeDirectory=ibshit` makes systemd create and clean up
that directory; it also means the socket does not survive a reboot, which is
correct.

If it fails to start:

```bash
sudo journalctl -u ibshit -n 50 --no-pager
```

The usual causes are a typo in `/etc/ibshit.env` (systemd is strict about
`EnvironmentFile` syntax — no quotes, no spaces around `=`) or a missing write
permission on `instance/`.

---

## 7. Put nginx in front

```bash
sudo install -m 0644 /srv/ibshit/deploy/nginx-ibshit.conf \
    /etc/nginx/sites-available/ibshit

# Set your hostname (or the LAN IP you will use)
sudo sed -i 's|server_name bio.example.com;|server_name bio.lan;|' \
    /etc/nginx/sites-available/ibshit

sudo ln -sf /etc/nginx/sites-available/ibshit /etc/nginx/sites-enabled/ibshit
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

The site config serves `/static/` directly from disk (faster, and it takes load
off Python) and proxies everything else to the socket.

Test it:

```bash
curl -I http://localhost/
curl -s http://localhost/healthz
```

`/healthz` returns `{"status":"ok"}` when the app is up — useful for monitoring.

> **nginx version note:** Debian 13 ships nginx 1.26, where `listen 443 ssl http2;`
> is deprecated in favour of a separate `http2 on;` directive. The supplied
> config uses plain `listen 80;` and lets `certbot` add the TLS block, so you do
> not hit this.

---

## 8. Firewall

```bash
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

You do **not** need to open any port for gunicorn — it talks over the socket.

---

## 9. Securing it

### If it is only on your LAN

The app's own accounts are enough. Bind nginx to the LAN interface if you want it
unreachable from elsewhere, or just do not forward ports on your router.

### If you expose it to the internet

Accounts are required to reach any progress, so a stranger cannot read another
user's history. Two things still deserve attention:

* **Registration.** Anyone who can reach the site can create an account. Set
  `ALLOW_REGISTRATION=0` once your accounts exist, or `REGISTRATION_CODE` to gate
  it.
* **Defence in depth.** If you want the whole app behind one gate as well, pick
  one of:

1. **Basic auth at nginx** (quickest):
   ```bash
   sudo apt install -y apache2-utils
   sudo htpasswd -c /etc/nginx/.htpasswd yourname
   ```
   Then inside the `location /` block:
   ```nginx
   auth_basic "IB Biology";
   auth_basic_user_file /etc/nginx/.htpasswd;
   ```

2. **A VPN or tunnel** (WireGuard, Tailscale, Cloudflare Tunnel) so the app is
   never directly on the public internet. Best option for a home server.

3. **Put it behind an SSO proxy** such as Authelia, OAuth2 Proxy or
   Cloudflare Access if you want per-user accounts.

### Accounts

Sign-in is built in. The first account created becomes the administrator; it is
the only one that may import or export the question bank. Everyone else can study
and sees only their own progress.

Create the first account in the browser, or from the shell:

```bash
sudo -u ibshit /usr/local/sbin/ibshit-flask auth create you@example.com --admin
sudo -u ibshit /usr/local/sbin/ibshit-flask auth list
```

Once your accounts exist, close registration so strangers cannot create their own:

```bash
# /etc/ibshit.env
ALLOW_REGISTRATION=0
sudo systemctl restart ibshit
```

Or leave it open but require a code you share privately:

```bash
# /etc/ibshit.env
REGISTRATION_CODE=some-shared-secret
```

Sign-in is rate limited per client address (8 attempts per 5 minutes). That limit
is tracked in each worker's memory, so if you run more than a couple of gunicorn
workers the effective limit multiplies — put the app behind a proxy that can do
the rate limiting itself if that matters to you.

Also set `API_KEY` in `/etc/ibshit.env` if the server is reachable by anyone you
do not trust — that closes the import/export endpoints:

```bash
sudo sed -i 's|^# API_KEY=.*|API_KEY='"$(openssl rand -hex 24)"'|' /etc/ibshit.env
sudo systemctl restart ibshit
```

And remember: **anyone who can reach the app can delete the answer history** via
`/api/reset`. Keep that in mind before exposing it.

---

## 10. HTTPS

Only meaningful if you have a real domain pointing at the server. With a home
connection, use a tunnel (Cloudflare Tunnel, Tailscale) instead of opening port
443 — it avoids exposing your home IP and handles certificates for you.

If you do have a domain:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d bio.example.com
```

Certbot edits the nginx config and sets up renewal. Verify:

```bash
sudo certbot renew --dry-run
```

Then tell the app it is behind HTTPS, so session cookies get the `Secure` flag
and generated links use the right scheme. Uncomment these in `/etc/ibshit.env`:

```
SESSION_COOKIE_SECURE=1
PREFERRED_URL_SCHEME=https
TRUST_PROXY_HEADERS=1
```

Apply with `sudo systemctl restart ibshit`. `TRUST_PROXY_HEADERS` makes the app
read `X-Forwarded-For`/`-Proto` from nginx; leave it **off** if the app is also
reachable directly, because otherwise a client could spoof those headers.

Certificates renew via a systemd timer; nothing else to configure.

---

## 11. Optional: PostgreSQL instead of SQLite

SQLite is genuinely fine for a home server — one user, one writer. Move to
Postgres only if you want concurrent access from several people or you are
uncomfortable keeping the database in a file.

```bash
sudo apt install -y postgresql
sudo -u postgres psql <<'SQL'
CREATE USER ibshit WITH PASSWORD 'choose-a-strong-password';
CREATE DATABASE ibshit OWNER ibshit;
SQL

sudo apt install -y python3-psycopg
sudo -u ibshit /srv/ibshit/.venv/bin/pip install "psycopg[binary]"
```

Add to `/etc/ibshit.env`:

```
DATABASE_URL=postgresql+psycopg://ibshit:choose-a-strong-password@localhost/ibshit
```

Then restart and reload the questions:

```bash
sudo systemctl restart ibshit
sudo -u ibshit /usr/local/sbin/ibshit-flask quiz seed
sudo -u ibshit /usr/local/sbin/ibshit-flask quiz import /srv/ibshit/seed/additional.json
```

Back up with `pg_dump ibshit > backup.sql` instead of copying a file.

---

## 12. Backups

Two things matter: the database and `/etc/ibshit.env`.

```bash
sudo crontab -e
```

```cron
# Daily database backup, keeping 14 days
0 3 * * * cp /srv/ibshit/instance/quiz.db /srv/backups/quiz-$(date +\%F).db && find /srv/backups -name 'quiz-*.db' -mtime +14 -delete
```

For a consistent copy with SQLite, prefer the built-in backup command over `cp`
if the app is busy:

```bash
sqlite3 /srv/ibshit/instance/quiz.db ".backup '/srv/backups/quiz.db'"
```

Back up `/etc/ibshit.env` too — losing `SECRET_KEY` only logs people out, but if
you set `API_KEY` you would need to redistribute it.

---

## 13. Updating

There are two ways, and both are safe.

### The bootstrap script (simplest)

```bash
sudo bash /srv/ibshit/deploy/bootstrap-debian.sh
```

It handles all four states the code directory can be in:

| State of `/srv/ibshit` | What it does |
| --- | --- |
| A git checkout | `git fetch` + `git reset --hard origin/main`, then restarts |
| Empty, or absent | clones it |
| Has files but no `.git` (interrupted run) | moves it to `/srv/ibshit.bak.<timestamp>`, clones fresh, and **restores your `instance/` database** |
| Already running the latest | no changes; reinstalls deps and restarts |

It never prompts for git credentials (`GIT_TERMINAL_PROMPT=0`), so it cannot hang
waiting for a password.

### Manually

```bash
cd /srv/ibshit
sudo -u ibshit git pull
sudo -u ibshit .venv/bin/pip install -r requirements.txt
sudo systemctl restart ibshit
```

There are no migrations to run: `wsgi.py` creates missing tables on startup and
`sync_topics()` keeps the topic list current.

If a release ever adds a column to an existing table, export and re-import rather
than trying to migrate by hand:

```bash
sudo -u ibshit /usr/local/sbin/ibshit-flask quiz export /tmp/bank.json
sudo systemctl stop ibshit
sudo mv /srv/ibshit/instance/quiz.db /srv/ibshit/instance/quiz.db.old
sudo systemctl start ibshit
sudo -u ibshit /usr/local/sbin/ibshit-flask quiz import /tmp/bank.json
```

## 14. Monitoring the image cache

Imported questions may reference diagrams on a third-party CDN. The app mirrors
them into `instance/images/` the first time they are requested, so the directory
grows slowly and is safe to delete (it repopulates on demand):

```bash
du -sh /srv/ibshit/instance/images
```

If you want to trim it: `sudo rm -rf /srv/ibshit/instance/images/*`.

As of the current import, only 19 of roughly 440 upstream image URLs still
resolve — the rest return 404 from the CDN, which is why most diagram-dependent
questions were dropped during import rather than shipped broken.

---

## 15. A quick public deployment (VPS + DuckDNS)

For a short-lived public demo on a VPS with a DuckDNS domain, two scripts do the
work. Both are idempotent.

```bash
# 1. Bring the app up behind nginx for your domain, then enable HTTPS
sudo bash /srv/ibshit/deploy/fix-duckdns.sh yourname.duckdns.org

# 2. If anything still fails, find out exactly why (read-only)
sudo bash /srv/ibshit/deploy/diagnose.sh
```

`fix-duckdns.sh` walks the causes of a 502 in the order they actually occur:

| Step | Why it matters |
| --- | --- |
| Generate `SECRET_KEY` if absent | production refuses to start without it, which is the most common 502 cause |
| `chown` the app directory | otherwise the CLI made root-owned files the service cannot read |
| Install the flask wrapper | lets you run the CLI as the app user safely |
| Restart the service and check it | rather than assuming it started |
| Grant nginx access to the socket | `RuntimeDirectoryMode=0750` only admits the app group, so `www-data` is added to it |
| Install the nginx site for your domain | replaces the placeholder `server_name` |
| Verify `healthz` locally | proves the app answers before you blame DNS |
| Open the firewall, run certbot | gets you HTTPS, and then sets `SESSION_COOKIE_SECURE`, `PREFERRED_URL_SCHEME` and `TRUST_PROXY_HEADERS` |

A 502 from nginx with the app running almost always means the socket is not
reachable by `www-data`, or the service never started. `diagnose.sh` tells you
which, and prints the fix.

### Authentication for a private repository

`git clone` over HTTPS needs a credential if the repository is private, which is
what makes an installer appear to hang or fail. Two ways to avoid making the
repository public:

```bash
# Option A: a deploy key (recommended). Read-only, scoped to this repo.
sudo -u ibshit ssh-keygen -t ed25519 -N '' -f /srv/ibshit/.ssh/id_ed25519
sudo cat /srv/ibshit/.ssh/id_ed25519.pub
# add that key in GitHub -> repo -> Settings -> Deploy keys (leave write off)
sudo -u ibshit git -C /srv/ibshit remote set-url origin git@github.com:Dellis908/ibshit.git
```

```bash
# Option B: a token in the URL for one clone
sudo -u ibshit git clone https://<token>@github.com/Dellis908/ibshit.git /srv/ibshit
```

This repository contains no secrets (configuration comes from `/etc/ibshit.env`,
which is not tracked), so keeping it public is also safe if that is simpler.

## 16. Moving an existing install

The scripts, the systemd unit and the nginx site all assume `/srv/ibshit`. If the
app is somewhere else — notably `/tmp`, which systemd sandboxes and which is
cleared on boot — move it rather than editing paths by hand:

```bash
# See what would happen first
sudo bash /srv/ibshit/deploy/move-app.sh /tmp/ibshit

# Then do it
sudo bash /srv/ibshit/deploy/move-app.sh /tmp/ibshit --apply
```

It stops the service, moves the checkout to `/srv/ibshit`, keeps `instance/` (so
your database and progress survive), recreates `.venv`, repoints
`WorkingDirectory`, `ExecStart`, `ReadWritePaths`, the nginx static alias and the
`ibshit-flask` wrapper, then restarts and checks `/healthz`.

To install somewhere other than `/srv/ibshit`, set `APP_DIR` for the helper
scripts and change the same paths in the unit by hand:

```bash
sudo APP_DIR=/opt/ibshit bash /opt/ibshit/deploy/fix-duckdns.sh your.domain
```

### Why `.venv` keeps disappearing

`.venv/` is in `.gitignore`, so `git clone` and `git pull` never create it. After
a fresh clone it must be built once:

```bash
sudo -u ibshit python3 -m venv /srv/ibshit/.venv
sudo -u ibshit /srv/ibshit/.venv/bin/pip install -r /srv/ibshit/requirements.txt
```

`bootstrap-debian.sh` and `fix-duckdns.sh` now do this automatically when the
environment is missing or incomplete, and `diagnose.sh` reports it.

## 17. Troubleshooting

**`flask` says "Could not locate a Flask application"**
`FLASK_APP` is not set. Pass it explicitly, as every command above does, or
export it: `export FLASK_APP=/srv/ibshit/wsgi.py`.

**502 Bad Gateway from nginx**
gunicorn is not running or the socket path is wrong. Check
`sudo systemctl status ibshit`, then `sudo ls -l /run/ibshit/`.

**`fatal: destination path '/srv/ibshit' already exists and is not an empty directory`**
A previous run created the directory but did not finish (often because it stopped
at the permission error below), so there is no `.git` for the installer to update.
Re-running the fixed installer handles this: it moves the old directory to
`/srv/ibshit.bak.<timestamp>`, clones fresh and puts your `instance/` database
back. To do it by hand:

```bash
sudo mv /srv/ibshit /srv/ibshit.bak
sudo -u ibshit git clone https://github.com/Dellis908/ibshit.git /srv/ibshit
sudo cp -a /srv/ibshit.bak/instance /srv/ibshit/ 2>/dev/null || true
sudo chown -R ibshit:ibshit /srv/ibshit
```

**`PermissionError: [Errno 13] Permission denied: '/srv/ibshit/instance'`**
`flask` was run as root, and `/srv/ibshit` belongs to `ibshit`, so root cannot
create `instance/` inside it. Run the CLI as the service account instead — see
step 5, or use the wrapper: `sudo -u ibshit /usr/local/sbin/ibshit-flask quiz seed`.
If an earlier run left root-owned files behind, fix them with
`sudo chown -R ibshit:ibshit /srv/ibshit`.

**The service starts but cannot read the database**
An earlier run as root created a root-owned `instance/quiz.db`. Fix with
`sudo chown -R ibshit:ibshit /srv/ibshit/instance && sudo systemctl restart ibshit`.

**`/srv/ibshit/.venv/bin/flask: not found` or similar**
`.venv` is not tracked by git, so a clone or a move leaves it missing. Recreate it:
```bash
sudo -u ibshit python3 -m venv /srv/ibshit/.venv
sudo -u ibshit /srv/ibshit/.venv/bin/pip install -r /srv/ibshit/requirements.txt
```
Or just re-run `deploy/bootstrap-debian.sh`, which does it for you.

**The app is installed in `/tmp`**
Move it: `sudo bash /srv/ibshit/deploy/move-app.sh /tmp/ibshit --apply`. Then
confirm nothing still points at the old location:
```bash
grep -rn "/tmp/ibshit" /etc/systemd/system /etc/nginx /usr/local/sbin 2>/dev/null
```

**`status=200/CHDIR` in the journal**
The systemd unit's `WorkingDirectory` does not exist or is not readable by the
service account.

**Locked out of an account**
Reset the password from the shell:
`sudo -u ibshit /usr/local/sbin/ibshit-flask auth passwd you@example.com`

**Nobody can administer the bank (import/export returns 403)**
Administrator rights belong to an account, not to the server. Grant them with
`sudo -u ibshit /usr/local/sbin/ibshit-flask auth promote you@example.com`.

**Progress recorded before accounts existed is missing**
That history has no owner, so it is shown to nobody. Report it with
`... auth list` (it is logged on startup too), then assign it:
`sudo -u ibshit /usr/local/sbin/ibshit-flask auth adopt-attempts you@example.com`

**Timezone shows UTC**
Set it: `sudo timedatectl set-timezone Europe/London` (or your zone). Times in
the progress page follow the server clock.

**Sessions reset on every request**
`SECRET_KEY` is missing or changing. Confirm it is set in `/etc/ibshit.env` and
that `EnvironmentFile` in the unit points at that exact path.

**Questions show "Diagram unavailable"**
That is the client-side fallback for an image the CDN no longer serves. The
question text is unaffected; you can delete such questions with the
`flask quiz reset` and re-import path, or leave them.

**Port 80 is already in use**
Something else (often Apache) is bound to it. `sudo ss -ltnp | grep :80` to find
it, then stop and disable that service.

---

## Quick reference

| Task | Command |
| --- | --- |
| Status | `sudo systemctl status ibshit` |
| Restart | `sudo systemctl restart ibshit` |
| Logs | `sudo journalctl -u ibshit -f` |
| Bank stats | `flask --app /srv/ibshit/wsgi.py quiz stats` |
| Add questions | `flask --app /srv/ibshit/wsgi.py quiz import FILE.json` |
| Export bank | `flask --app /srv/ibshit/wsgi.py quiz export /tmp/bank.json` |
| Health check | `curl -s localhost/healthz` |
| Update | `sudo bash /srv/ibshit/deploy/bootstrap-debian.sh` |
| Run CLI as the app | `sudo -u ibshit /usr/local/sbin/ibshit-flask quiz stats` |
| nginx config test | `sudo nginx -t` |
| Code location | `/srv/ibshit` (override with `APP_DIR=`) |
| Move the install | `sudo bash /srv/ibshit/deploy/move-app.sh /tmp/ibshit --apply` |
| Rebuild `.venv` | `sudo bash /srv/ibshit/deploy/bootstrap-debian.sh` |
| Database | `/srv/ibshit/instance/quiz.db` |
| Secrets | `/etc/ibshit.env` |
| Image cache | `/srv/ibshit/instance/images/` |
