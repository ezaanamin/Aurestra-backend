#!/usr/bin/env bash
# Run from anywhere after unpacking the bundle. See README.md.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED_PY="$DIR/seed_from_sqlite_backup.py"
if [[ ! -f "$SEED_PY" ]]; then
  SEED_PY="$DIR/../db/seed_from_sqlite_backup.py"
fi

SRC="${SEED_SOURCE:-$DIR/backups/aurestra.db}"
BACKEND="${AURESTRA_BACKEND:-$(dirname "$DIR")}"
TGT="${SEED_TARGET:-$BACKEND/aurestra.db}"

if [[ ! -f "$SEED_PY" ]]; then
  echo "Missing seed_from_sqlite_backup.py (expected under this folder or ../db/)." >&2
  exit 1
fi
if [[ ! -f "$SRC" ]]; then
  echo "Missing source DB: $SRC" >&2
  echo "Copy your backup SQLite file to backups/aurestra.db (see backups/README.txt) or set SEED_SOURCE." >&2
  echo "Note: use this folder's script — not db/seed_from_sqlite_backup.py (that path is only in a full repo checkout)." >&2
  exit 1
fi
if [[ ! -f "$TGT" ]]; then
  echo "Missing target DB: $TGT" >&2
  echo "Set AURESTRA_BACKEND to your backend directory (parent of this folder) or set SEED_TARGET." >&2
  exit 1
fi

exec python3 "$SEED_PY" --source "$SRC" --target "$TGT" "$@"
