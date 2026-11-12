#!/usr/bin/env bash
# Install/update this checkout's launcher without starting the overlay.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python3 - "$project_dir" <<'PY'
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
apps = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'applications'
apps.mkdir(parents=True, exist_ok=True)
# Escape desktop-entry values and the Exec argument, including spaces and %.
def value(text):
    return str(text).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
argument = str(root/'launch.sh').replace('%', '%%')
for char in ('\\', '"', '`', '$'):
    argument = argument.replace(char, '\\'+char)
entry = '\n'.join([
    '[Desktop Entry]', 'Type=Application', 'Name=Aspects Overlay',
    'Comment=Thaumcraft research solver and auto-placement',
    'Comment[ru]=Решатель исследований Thaumcraft и автоматическая расстановка',
    'Exec="'+value(argument)+'"', 'Path='+value(root),
    'Icon='+value(root/'aspects-overlay.svg'), 'Terminal=false',
    'Categories=Game;', 'StartupNotify=false', '',
])
path = apps/'aspects-overlay.desktop'
path.write_text(entry, encoding='utf-8')
print(path)
PY
