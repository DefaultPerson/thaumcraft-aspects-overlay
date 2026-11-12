"""Capture the game's X11 window, including a nested Gamescope display."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from Xlib import X, display


@dataclass(frozen=True)
class GameTarget:
    display: str
    window: int
    pid: int
    game_dir: Path
    title: str
    gamescope: bool
    output_size: tuple[int, int]


def _windows(window, depth=0):
    if depth > 4:
        return
    try:
        for child in window.query_tree().children:
            yield child
            yield from _windows(child, depth + 1)
    except Exception:
        return


def _inspect_window(conn, window, pid, directory):
    if window.get_attributes().map_state != X.IsViewable:
        return None
    geom = window.get_geometry()
    if geom.width < 400 or geom.height < 300:
        return None
    title = window.get_wm_name() or ""
    if isinstance(title, bytes):
        title = title.decode(errors="replace")
    root = conn.screen().root
    owner_atom = conn.intern_atom("GAMESCOPE_PID", only_if_exists=True)
    owner = root.get_full_property(owner_atom, X.AnyPropertyType) if owner_atom else None
    nested = owner is not None
    output_size = geom.width, geom.height
    if nested:
        try:
            argv = (Path("/proc") / str(int(owner.value[0])) / "cmdline").read_bytes().decode().split("\0")
            def dimension(short, long, default):
                for key in (short, long):
                    if key in argv:
                        return int(argv[argv.index(key)+1])
                    for argument in argv:
                        if argument.startswith(key+"="):
                            return int(argument.split("=", 1)[1])
                return default
            output_size = (dimension("-W", "--output-width", geom.width),
                           dimension("-H", "--output-height", geom.height))
        except (OSError, ValueError, IndexError):
            pass
    return GameTarget(conn.get_display_name(), window.id, pid, directory, title, nested, output_size)


def _instance_dir(args, cwd, configured=None):
    if configured is not None:
        candidates = [Path(configured).expanduser()]
    else:
        candidates = []
        if "--gameDir" in args and args.index("--gameDir")+1 < len(args):
            supplied = Path(args[args.index("--gameDir")+1])
            candidates.append(supplied if supplied.is_absolute() else cwd/supplied)
        candidates.append(cwd)
    return next((path.resolve() for path in candidates if (path/"mods").is_dir()), None)


def discover(display_name: str | None = None, game_dir: Path | None = None,
             window_id: int | None = None) -> GameTarget:
    if window_id is not None:
        if not display_name or game_dir is None:
            raise ValueError("--window-id requires --display and --game-dir")
        directory = _instance_dir([], Path.cwd(), game_dir)
        if directory is None:
            raise ValueError("--game-dir must contain mods/")
        conn = display.Display(display_name)
        try:
            window = conn.create_resource_object('window', window_id)
            prop = window.get_full_property(conn.intern_atom('_NET_WM_PID'), X.AnyPropertyType)
            target = _inspect_window(conn, window, int(prop.value[0]) if prop is not None else 0, directory)
            if target is None:
                raise RuntimeError("Selected window is hidden or too small; reopen Minecraft and check --window-id")
            return target
        finally:
            conn.close()
    candidates = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if "java" not in (proc/"comm").read_text().lower():
                continue
            args = (proc/"cmdline").read_bytes().decode(errors="replace").split("\0")
            env = dict(part.split("=", 1) for part in
                       (proc/"environ").read_bytes().decode(errors="replace").split("\0") if "=" in part)
            name = env.get("DISPLAY", "")
            if not name or (display_name and name != display_name):
                continue
            directory = _instance_dir(args, (proc/"cwd").resolve(), game_dir)
            if directory is None:
                continue
            conn = display.Display(name)
            try:
                for window in _windows(conn.screen().root):
                    title = window.get_wm_name() or ""
                    if isinstance(title, bytes):
                        title = title.decode(errors="replace")
                    if "minecraft" not in title.lower():
                        continue
                    pid_prop = window.get_full_property(conn.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
                    if pid_prop is not None and int(pid_prop.value[0]) != int(proc.name):
                        continue
                    target = _inspect_window(conn, window, int(proc.name), directory)
                    if target is not None:
                        candidates.append(target)
            finally:
                conn.close()
        except (OSError, ValueError, IndexError):
            continue
    unique = {(t.display, t.window): t for t in candidates}
    if not unique:
        raise RuntimeError("Не найдено видимое окно Minecraft. Откройте игру или задайте --display, --window-id и --game-dir.")
    if len(unique) > 1:
        raise RuntimeError("Найдено несколько окон Minecraft; выберите --display или --window-id вместе с --game-dir.")
    return next(iter(unique.values()))


def capture(target: GameTarget, connection=None) -> np.ndarray:
    conn = connection if connection is not None else display.Display(target.display)
    try:
        window = conn.create_resource_object("window", target.window)
        if window.get_attributes().map_state != X.IsViewable:
            raise RuntimeError("Окно Minecraft скрыто; откройте игру и повторите расчёт.")
        geom = window.get_geometry()
        if geom.depth not in (24, 32):
            raise RuntimeError("Неподдерживаемая глубина цвета окна.")
        frame = window.get_image(0, 0, geom.width, geom.height, X.ZPixmap, 0xFFFFFFFF)
        if frame is None or len(frame.data) != geom.width * geom.height * 4:
            raise RuntimeError("X11 не вернул полный кадр окна Minecraft.")
        return np.frombuffer(frame.data, dtype=np.uint8).reshape(geom.height, geom.width, 4)[..., :3].copy()
    finally:
        if connection is None:
            conn.close()
