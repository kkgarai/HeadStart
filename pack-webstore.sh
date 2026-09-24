#!/bin/sh
# Zip for the Chrome Web Store. Strips manifest "key" (keep the PEM in the
# developer dashboard so the ID stays ojpfakkcgmefanbomdfpglbioapoabfh).
# Does not upload. Excludes .git, bytecode, and generated skill/out files.
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/edp-webstore.XXXXXX")"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT
rsync -a \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'skill/out/*' \
  "$ROOT/" "$STAGE/extension/"
python3 -c '
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text(encoding="utf-8"))
data.pop("key", None)
p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
' "$STAGE/extension/manifest.json"
ZIP="${TMPDIR:-/tmp}/engineer-day-planner-webstore.zip"
rm -f "$ZIP"
( cd "$STAGE/extension" && zip -qr "$ZIP" . )
echo "$ZIP"
