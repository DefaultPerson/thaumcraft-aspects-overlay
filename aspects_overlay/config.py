"""Portable TOML settings; explicit command-line options take precedence."""
import argparse
import math
from pathlib import Path
import tomllib


def window_id(value):
    try:
        number = int(str(value), 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('window-id must be a decimal or 0x hexadecimal integer') from exc
    if number <= 0:
        raise argparse.ArgumentTypeError('window-id must be positive')
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Thaumcraft 4 research solver and Linux overlay')
    parser.add_argument('--config', type=Path, help='TOML settings; defaults to ./config.toml when present')
    parser.add_argument('--game-dir', type=Path, help='Minecraft instance directory containing mods/')
    parser.add_argument('--display', help='Minecraft X11 display; normally autodetected')
    parser.add_argument('--window-id', type=window_id, help='Explicit X11 window, e.g. 0xe00003; requires display and game-dir')
    parser.add_argument('--image', type=Path, help='Solve a screenshot without X11 or an overlay')
    parser.add_argument('--output', type=Path, help='Private logs and results directory (default: .runtime)')
    parser.add_argument('--timeout', type=float, help='Solver budget in seconds, 0.25..60 (default: 8)')
    parser.add_argument('--inventory', type=Path, help='JSON aspect -> count; omitted aspects have zero stock')
    parser.add_argument('--exclude', help='Comma-separated unavailable aspects')
    parser.add_argument('--diagnose', action='store_true', help='Print non-secret target details and exit')
    args = parser.parse_args(argv)
    path = args.config.expanduser() if args.config else Path('config.toml')
    data = {}
    if args.config or path.is_file():
        try:
            with path.open('rb') as stream:
                data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            parser.error(f'Cannot read configuration {path}: {exc}')
    defaults = {'game_dir': None, 'display': None, 'window_id': None, 'output': Path('.runtime'),
                'timeout': 8.0, 'inventory': None, 'exclude': ''}
    unknown = data.keys()-defaults.keys()
    if unknown:
        parser.error('Unknown configuration keys: '+', '.join(sorted(unknown)))
    for name, value in data.items():
        if name == 'timeout':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                parser.error('timeout must be a number')
        elif name == 'exclude':
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                parser.error('exclude must be an array of aspect names')
            value = ','.join(value)
        elif not isinstance(value, str) or not value.strip():
            parser.error(f'{name} must be a non-empty string')
        if name in ('game_dir', 'output', 'inventory'):
            value = Path(value).expanduser()
            if not value.is_absolute():
                value = path.resolve().parent/value
        if name == 'window_id':
            try:
                value = window_id(value)
            except argparse.ArgumentTypeError as exc:
                parser.error(str(exc))
        defaults[name] = value
    for name, value in defaults.items():
        if getattr(args, name) is None:
            setattr(args, name, value)
    for name in ('game_dir', 'image', 'inventory', 'output'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.expanduser())
    args.exclude = ','.join(part.strip().lower() for part in args.exclude.split(',') if part.strip())
    if not math.isfinite(args.timeout) or not .25 <= args.timeout <= 60:
        parser.error('--timeout must be between 0.25 and 60 seconds')
    if args.image and not args.game_dir:
        parser.error('--image requires --game-dir for local aspect textures')
    if args.window_id and not (args.display and args.game_dir):
        parser.error('--window-id requires --display and --game-dir')
    if args.image and args.diagnose:
        parser.error('--image and --diagnose are separate modes')
    return args
