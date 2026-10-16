#!/bin/sh
# Copy the topic notes to the server.
#
#   ./deploy/sync-notes.sh user@host [remote-dir]
#
# The notes are ~290 MB of PDFs, so they are deliberately not in git: the code
# deploys with `git pull`, and the notes ride alongside it over rsync. The
# manifest travels with them, because it is what the app reads to decide which
# notes exist.
#
# rsync resumes and skips unchanged files, so re-running after adding a few notes
# only transfers those.
set -eu

HOST="${1:-}"
REMOTE_DIR="${2:-/srv/ibshit/data/notes}"

# An explicit identity, when there is one: rsync hands the transport to ssh(1),
# which does not inherit `-i` from anywhere else. Set SSH_KEY to point at the key
# used for the server, or leave it unset to use the agent and ~/.ssh/config.
SSH_KEY="${SSH_KEY:-$HOME/.ssh/ibshit-vps}"
SSH_OPTS="-o BatchMode=yes"
if [ -f "$SSH_KEY" ]; then
  SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

if [ -z "$HOST" ]; then
  echo "usage: $0 user@host [remote-dir]" >&2
  exit 2
fi

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_NOTES="$APP_DIR/data/notes"

if [ ! -f "$LOCAL_NOTES/manifest.json" ]; then
  echo "no notes installed locally: $LOCAL_NOTES/manifest.json is missing" >&2
  echo "install them first:  python scripts/install_notes.py --source <folder>" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is not installed locally; install it and try again" >&2
  exit 1
fi

count=$(find "$LOCAL_NOTES" -name '*.pdf' | wc -l | tr -d ' ')
size=$(du -sh "$LOCAL_NOTES" | cut -f1)
echo "sending $count note(s) ($size) to $HOST:$REMOTE_DIR"

# macOS ships openrsync, which is not GNU rsync: it has no --info and no --chmod,
# and it reports success even when it prints an error. So the flags stay minimal
# and the result is verified by counting files on the far side rather than by
# trusting the exit status.
ssh $SSH_OPTS "$HOST" "mkdir -p '$REMOTE_DIR'"

rsync -az --progress --exclude '.*' -e "ssh $SSH_OPTS" \
  "$LOCAL_NOTES/" "$HOST:$REMOTE_DIR/"

echo
echo "verifying on the server:"
remote_count=$(ssh $SSH_OPTS "$HOST" "find '$REMOTE_DIR' -name '*.pdf' | wc -l" | tr -d ' ')
echo "  local:  $count"
echo "  remote: $remote_count"
if [ "$remote_count" != "$count" ]; then
  echo "MISMATCH: the transfer is incomplete" >&2
  exit 1
fi

# The files arrive owned by whoever ran rsync; hand them to the service account.
ssh $SSH_OPTS "$HOST" "chown -R ibshit:ibshit '$REMOTE_DIR' && ls -l '$REMOTE_DIR/manifest.json'"

echo
echo "done: $remote_count notes in place, owned by ibshit. The manifest is read on"
echo "each request, so no restart is needed."
