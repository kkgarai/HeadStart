#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ -f "$PWD/manifest.json" ] && [ -f "$PWD/setup-headstart.py" ]; then
  python3 "$PWD/setup-headstart.py"
  status=$?
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required."
    status=1
  elif ! command -v git >/dev/null 2>&1; then
    echo "Git is required."
    status=1
  else
    dest="$HOME/HeadStart"
    if [ ! -d "$dest/.git" ]; then
      git clone --branch main --single-branch https://github.com/kkgarai/HeadStart.git "$dest" || status=$?
    fi
    if [ -z "${status:-}" ]; then
      python3 "$dest/setup-headstart.py"
      status=$?
    fi
  fi
fi
echo
if [ "$status" -eq 0 ]; then
  echo "Setup finished. Load unpacked the folder printed above, then click the toolbar icon."
else
  echo "Setup did not finish. Read the message above."
fi
echo
read -r -p "Press Return to close..."
exit "$status"
