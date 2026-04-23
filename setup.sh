#!/usr/bin/env bash
# Installs isolated deps for the TN160 GUI into a WSL-native ext4 location,
# so the project dir (which lives on 9p/DrvFs) stays free of package files
# and no other project's env is affected.
set -euo pipefail
LIBS="$HOME/.local/share/thermocam_gui/libs"
mkdir -p "$LIBS"
echo "Installing to: $LIBS"
pip3 install --target="$LIBS" --no-warn-script-location --upgrade PySide6 matplotlib
echo
echo "done. run:  bash $(cd "$(dirname "$0")" && pwd)/run.sh"
