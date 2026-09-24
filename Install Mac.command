#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 skill/scripts/install-native-host.py
status=$?
echo
if [ "$status" -eq 0 ]; then
  echo "Installed. Go back to Chrome and click the toolbar icon."
else
  echo "Install did not finish. Read the message above."
fi
echo
read -r -p "Press Return to close..."
exit "$status"
