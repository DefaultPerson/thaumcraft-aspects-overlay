#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
mkdir -p .runtime
if ./run.sh "$@" >> .runtime/launcher.log 2>&1; then
    exit 0
else
    result=$?
    notify-send --app-name='Aspects Overlay' 'Aspects Overlay' 'Не удалось запустить оверлей. Откройте Minecraft; подробности — в .runtime/launcher.log.' || true
    exit "$result"
fi
