#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 extension/skill/scripts/uninstall-native-host.py
status=$?
echo
if [ "$status" -eq 0 ]; then
  echo "Host removed. Remove the extension in chrome://extensions."
else
  echo "Uninstall did not finish. Read the message above."
fi
echo
read -r -p "Press Return to close..."
exit "$status"
