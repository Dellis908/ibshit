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

# --chmod keeps the service account able to read them without a chown afterwards.
rsync -az --info=progress2 --chmod=D755,F644 \
  --exclude '.*' \
  "$LOCAL_NOTES/" "$HOST:$REMOTE_DIR/"

echo
echo "verifying on the server:"
ssh "$HOST" "ls '$REMOTE_DIR' | wc -l; ls -l '$REMOTE_DIR/manifest.json'"

echo
echo "done. The application reads the manifest on each request, so no restart is"
echo "needed — but make sure the service account owns $REMOTE_DIR:"
echo "  ssh $HOST 'chown -R ibshit:ibshit $REMOTE_DIR'"
