#!/usr/bin/env bash
# TN160 GUI launcher — prepends isolated libs dir to PYTHONPATH so no other
# project's Python env is touched. Source stays on 9p/DrvFs; deps live on ext4.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LIBS="$HOME/.local/share/thermocam_gui/libs"

if [ ! -d "$LIBS" ]; then
    echo "依赖目录不存在: $LIBS"
    echo "请先运行: bash $HERE/setup.sh"
    exit 1
fi

export PYTHONPATH="$LIBS:${PYTHONPATH:-}"
exec python3 "$HERE/app.py" "$@"
