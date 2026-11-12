"""Transparent Qt overlay with mouse controls and optional X11 hotkeys."""
from __future__ import annotations

from collections import Counter
import json
import logging
import math
from pathlib import Path
import time
import threading

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal, QSocketNotifier
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget
from Xlib import X, XK, Xatom, display, error

from .assets import Assets
from .automation import AutoPlacer
from .capture import GameTarget, capture
from .controls import PointerControls
from .recognition import RecognitionCache, RecognitionError, Board, find_parchment, read_board
from .solver import NEIGHBORS, solve, validate_solution


class Worker(QThread):
    completed = Signal(object)
    already_completed = Signal()
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, target, assets, budget, inventory, output_dir, inventory_known):
        super().__init__()
        self.target, self.assets, self.budget = target, assets, budget
        self.inventory, self.output_dir = inventory, output_dir
        self.inventory_known = inventory_known
        self.cancel = threading.Event()

    def check(self):
        if self.cancel.is_set():
            raise RecognitionError("Остановлено пользователем.")

    def wait_capture(self, seconds):
        self.cancel.wait(seconds)
        self.check()

    def read(self, cache, expected=None):
        return read_board(lambda: capture(self.target), self.assets, cache=cache,
                          expected=expected, allow_complete=expected is None,
                          check=self.check, wait=self.wait_capture,
                          on_retry=lambda _: self.progress.emit("Перепроверяю поле — повторно нажимать не нужно…"))

    def run(self):
        try:
            recognition_cache = RecognitionCache()
            board, frame = self.read(recognition_cache)
            if board is None:
                self.already_completed.emit()
                return
            self.progress.emit("Поле подтверждено. Ищу решение…")
            result = solve(board.cells, board.anchors, self.assets.recipes,
                           inventory=self.inventory, timeout_s=self.budget)
            self.check()
            if result.status not in ("optimal", "feasible"):
                messages = {"infeasible": "С этими клетками и запасами решения нет.",
                            "unknown": "Не удалось найти решение за отведённое время.",
                            "invalid": "Некорректная сетка или список доступных аспектов."}
                raise ValueError(messages.get(result.status, result.message))
            valid, reason = validate_solution(board.cells, board.anchors, self.assets.recipes,
                                              result.placements, self.inventory)
            if not valid:
                raise ValueError("Проверка решения не пройдена: " + reason)
            # Do not display a result for a note that changed during solving.
            _, current = self.read(recognition_cache, expected=board)
            report = {"board": board.to_dict(), "status": result.status,
                      "elapsed_s": round(result.elapsed_s, 3),
                      "inventory_checked": self.inventory_known,
                      "placements": [{"q": q, "r": r, "aspect": aspect}
                                     for (q, r), aspect in sorted(result.placements.items())]}
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "last-solution.json").write_text(json.dumps(report, indent=2) + "\n")
            self.completed.emit((board, result, current))
        except Exception as exc:
            if self.cancel.is_set():
                logging.info("Capture/solve cancelled")
            else:
                logging.exception("Capture/solve failed")
            self.failed.emit(str(exc))


class ApplyWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(object)

    def __init__(self, target, assets, board, placements, output_dir):
        super().__init__()
        self.cancel = threading.Event()
        self.output_dir = output_dir
        self.placer = AutoPlacer(target, assets, board, placements, self.cancel, self.progress.emit)

    def run(self):
        problem = None
        try:
            self.placer.run()
        except Exception as exc:
            problem = "Остановлено пользователем." if self.cancel.is_set() else str(exc)
            if self.cancel.is_set():
                logging.info("Auto-placement cancelled")
            else:
                logging.exception("Auto-placement stopped")
        report = {"status": "stopped" if problem else ("completed" if self.placer.completed else "placed"), "message": problem,
                  "confirmed": [{"q": q, "r": r, "aspect": a}
                                for (q, r), a in self.placer.confirmed.items()],
                  "requested": len(self.placer.placements),
                  "elapsed_s": round(self.placer.elapsed_s, 3),
                  "timings": self.placer.metrics}
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "last-placement.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
            if self.placer.last_frame is not None:
                cv2.imwrite(str(self.output_dir / "last-placement.png"), self.placer.last_frame)
        except OSError:
            logging.exception("Could not save placement report")
        self.completed.emit(report)


class Hotkeys:
    def __init__(self, target, solve_callback, toggle_callback, apply_callback, stop_callback, quit_callback):
        self.conn = display.Display(target.display)
        self.root = self.conn.screen().root
        self.bindings = {}
        failures = []
        self.conn.set_error_handler(lambda exc, request: failures.append(str(exc)))
        for key, modifiers, callback in [("F8", 0, solve_callback), ("F9", 0, toggle_callback),
                                         ("F10", 0, apply_callback),
                                         ("q", X.ControlMask | X.Mod1Mask, quit_callback)]:
            code = self.conn.keysym_to_keycode(XK.string_to_keysym(key))
            self.bindings[(code, modifiers)] = callback
            for locks in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                self.root.grab_key(code, modifiers | locks, False, X.GrabModeAsync, X.GrabModeAsync)
        self.conn.sync()
        if failures:
            self.conn.close()
            raise RuntimeError("Не удалось занять F8/F9/F10/Ctrl+Alt+Q; возможно, оверлей уже запущен.")
        self.notifier = QSocketNotifier(self.conn.fileno(), QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self.read)
        self.last_key = 0.0
        self.closed = False
        self.stop_callback = stop_callback

    def escape(self, enabled):
        code = self.conn.keysym_to_keycode(XK.string_to_keysym("Escape"))
        if enabled:
            self.bindings[(code, 0)] = self.stop_callback
            for locks in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                self.root.grab_key(code, locks, False, X.GrabModeAsync, X.GrabModeAsync)
        else:
            self.bindings.pop((code, 0), None)
            for locks in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                self.root.ungrab_key(code, locks)
        self.conn.sync()

    def read(self, *_):
        while not self.closed and self.conn.pending_events():
            event = self.conn.next_event()
            if event.type != X.KeyPress:
                continue
            mods = event.state & ~(X.LockMask | X.Mod2Mask)
            callback = self.bindings.get((event.detail, mods))
            if callback and time.monotonic() - self.last_key > .3:
                self.last_key = time.monotonic()
                callback()

    def close(self):
        self.closed = True
        self.notifier.setEnabled(False)
        self.conn.close()  # X11 automatically releases this client's grabs.


class Overlay(QWidget):
    def __init__(self, target: GameTarget, assets: Assets, budget: float,
                 inventory: dict | None, output_dir: Path, inventory_known: bool):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                 Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput |
                 Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Aspects Overlay")
        self.target, self.assets, self.budget = target, assets, budget
        self.inventory, self.output_dir = inventory, output_dir
        self.inventory_known = inventory_known
        self.board = self.result = self.worker = None
        self.apply_worker = None
        self.pending_apply = False
        self.pending_solve = False
        self.auto_apply = False
        self.confirmed_cells = set()
        self.visible_scheme = True
        self.message = "Откройте записку и нажмите «Решить и расставить»"
        self.detail = "Управление мышью  ·  Для работы с игрой уведите курсор с панели"
        self.pixmaps = {}
        for name, icon in assets.icons.items():
            icon = np.ascontiguousarray(icon)
            image = QImage(icon.data, icon.shape[1], icon.shape[0], icon.strides[0], QImage.Format.Format_RGBA8888)
            self.pixmaps[name] = QPixmap.fromImage(image.copy())
        self.conn = display.Display(target.display)
        root_geom = self.conn.screen().root.get_geometry()
        self.logical_size = (root_geom.width, root_geom.height)
        self.output_size = target.output_size if target.gamescope else self.logical_size
        self.setGeometry(0, 0, *self.output_size)
        window = self.conn.create_resource_object("window", int(self.winId()))
        if target.gamescope:
            # Gamescope explicitly composites this ARGB surface above the game.
            window.change_property(self.conn.intern_atom("GAMESCOPE_EXTERNAL_OVERLAY"), Xatom.CARDINAL, 32, [1])
            window.change_property(self.conn.intern_atom("_NET_WM_WINDOW_OPACITY"), Xatom.CARDINAL, 32, [0xFFFFFFFF])
        self.conn.sync()
        self.keys = None
        try:
            self.keys = Hotkeys(target, self.start_solve, self.toggle, self.apply_or_stop, self.stop_apply, self.close)
        except RuntimeError as exc:
            logging.warning("Hotkeys unavailable; mouse controls remain enabled: %s", exc)
        factor = min(self.output_size[0]/self.logical_size[0], self.output_size[1]/self.logical_size[1])
        self.pointer = PointerControls(target, int(self.winId()), factor, self.control_at, self.activate_control, self.update)
        self.watch = QTimer(self)
        self.watch.timeout.connect(self.check_note)
        self.watch.start(900)
        self.anchor_patches = {}
        self.note_mismatches = 0
        self.show()
        # Gamescope can initially configure a new surface to the game resolution.
        # Request the physical output dimensions after that first ConfigureNotify.
        QTimer.singleShot(150, lambda: self.resize(*self.output_size))
        logging.info("READY display=%s game_window=%s overlay_window=%s gamescope=%s",
                     target.display, hex(target.window), hex(int(self.winId())), target.gamescope)

    def start_auto(self):
        self.start_solve(auto_apply=True)

    def start_solve(self, auto_apply=False):
        if self.applying() or self.pending_apply or self.pending_solve or (self.worker and self.worker.isRunning()):
            return
        self.auto_apply = auto_apply
        if self.keys:
            self.keys.escape(True)
        if self.pointer.interactive or self.pointer.transition:
            self.pending_solve = True
            self.pointer.return_to_game(self.resume_solve, (self.logical_size[0]/2, self.logical_size[1]*.18))
            self.update()
            return
        self.confirmed_cells = set()
        self.board = self.result = None
        self.anchor_patches = {}
        self.visible_scheme = True
        self.message = "Распознаю поле и ищу решение…"
        self.detail = (f"Лимит поиска: {self.budget:g} с  ·  " +
                       ("Затем автоматически расставлю аспекты · Стоп / Esc — отмена"
                        if self.auto_apply else "Дождитесь схемы, затем нажмите «Расставить»"))
        self.update()
        self.worker = Worker(self.target, self.assets, self.budget, self.inventory, self.output_dir, self.inventory_known)
        self.worker.completed.connect(self.solved)
        self.worker.already_completed.connect(self.research_done)
        self.worker.failed.connect(self.failed)
        self.worker.progress.connect(self.recognition_progress)
        self.worker.finished.connect(self.resume_auto)
        self.worker.finished.connect(self.update)
        self.worker.start()
        logging.info("ACTION solve")

    def resume_solve(self):
        if self.pending_solve:
            self.pending_solve = False
            self.start_solve(auto_apply=self.auto_apply)

    def resume_auto(self):
        # completed is emitted before QThread finishes. Wait for finished so
        # the placement guard cannot discard this automatic continuation.
        if self.auto_apply:
            self.auto_apply = False
            if self.board and self.result:
                self.apply_or_stop()

    def solved(self, payload):
        if self.keys and not self.auto_apply:
            self.keys.escape(False)
        self.board, self.result, frame = payload
        count = len(self.result.placements)
        label = "Минимум доказан" if self.result.status == "optimal" else "Найдено решение; минимум не доказан"
        self.message = f"{label}  ·  {count} клеток  ·  {self.result.elapsed_s:.1f} с"
        self.detail = "Нажмите «Расставить» для автоматического заполнения или используйте схему вручную"
        self.anchor_patches = {}
        self.note_mismatches = 0
        for cell in self.board.anchors:
            x, y = map(round, self.board.pixel(cell))
            radius = round(7 * self.board.scale)
            self.anchor_patches[cell] = frame[y-radius:y+radius, x-radius:x+radius].copy()
        self.update()
        logging.info("SOLUTION status=%s cells=%d anchors=%d placements=%d elapsed=%.3f",
                     self.result.status, len(self.board.cells), len(self.board.anchors),
                     count, self.result.elapsed_s)

    def recognition_progress(self, message):
        self.message = message
        self.update()

    def research_done(self):
        self.auto_apply = False
        if self.keys:
            self.keys.escape(False)
        self.board = self.result = None
        self.anchor_patches = {}
        self.message = "Исследование завершено — подтверждено в Minecraft"
        self.detail = "Заберите записку, откройте следующую и нажмите «Решить и расставить»"
        self.update()
        logging.info("RESEARCH_COMPLETED native_copy_button=True")

    def applying(self):
        return self.apply_worker is not None and self.apply_worker.isRunning()

    def apply_or_stop(self):
        if self.applying() or self.auto_apply:
            self.stop_apply()
            return
        if not self.board or not self.result or (self.worker and self.worker.isRunning()):
            self.message = "Сначала нажмите «Рассчитать» для построения схемы."
            self.update()
            return
        if self.pointer.interactive or self.pointer.transition:
            x, y, _, _ = self.board.parchment
            parking = (x-47*self.board.scale, y+9*self.board.scale)
            self.pending_apply = True
            self.pointer.return_to_game(self.resume_apply, parking)
            self.update()
            return
        self.confirmed_cells = set()
        if self.keys:
            self.keys.escape(True)
        self.apply_worker = ApplyWorker(self.target, self.assets, self.board, self.result.placements, self.output_dir)
        self.apply_worker.progress.connect(self.placement_progress)
        self.apply_worker.completed.connect(self.placement_finished)
        self.apply_worker.finished.connect(self.update)
        self.visible_scheme = True
        self.detail = "Мышь в игре заблокирована  ·  Остановка: правая кнопка мыши, Esc или F10"
        self.message = "Проверяю поле перед автозаполнением…"
        self.update()
        self.apply_worker.start()
        logging.info("ACTION auto-place")

    def resume_apply(self):
        if self.pending_apply:
            self.pending_apply = False
            self.apply_or_stop()

    def stop_apply(self):
        if self.auto_apply:
            self.auto_apply = False
            if self.keys:
                self.keys.escape(False)
            self.message = "Автоматическая расстановка отменена."
            self.detail = "Автоматическая расстановка не начнётся."
            self.update()
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            if self.keys:
                self.keys.escape(False)
            self.message = "Останавливаю распознавание и расчёт…"
            self.update()
        if self.pending_solve:
            self.pending_solve = False
            self.message = "Расчёт отменён до запуска."
            self.update()
        if self.pending_apply:
            self.pending_apply = False
            if self.keys:
                self.keys.escape(False)
            self.message = "Расстановка отменена до запуска."
            self.update()
        if self.applying():
            self.apply_worker.cancel.set()
            self.message = "Останавливаю автозаполнение…"
            self.update()

    def placement_progress(self, done, total, aspect):
        self.confirmed_cells = set(self.apply_worker.placer.confirmed)
        self.message = f"Размещено {done}/{total}  ·  {aspect}"
        self.update()
        logging.info("PLACEMENT %d/%d %s", done, total, aspect)

    def placement_finished(self, report):
        if self.keys:
            self.keys.escape(False)
        count = len(report["confirmed"])
        self.message = (f"Размещено {count}/{report['requested']}. " +
                        (report["message"] if report["status"] == "stopped" else
                         "Исследование завершено!" if report["status"] == "completed" else
                         "Схема расставлена, но Minecraft ещё не подтвердил завершение исследования."))
        self.detail = "Для следующего запуска нажмите «Решить и расставить»"
        self.board = self.result = None
        self.anchor_patches = {}
        self.update()
        logging.info("PLACEMENT_RESULT %s", json.dumps(report, ensure_ascii=False))

    def failed(self, message):
        self.auto_apply = False
        if self.keys:
            self.keys.escape(False)
        self.board = self.result = None
        self.message = message
        self.detail = "Исправьте причину и нажмите «Решить и расставить» или «Рассчитать»"
        self.update()

    def toggle(self):
        self.visible_scheme = not self.visible_scheme
        self.update()
        logging.info("TOGGLE visible=%s", self.visible_scheme)

    def panel_rect(self):
        return QRectF(30, 65, min(1040, self.logical_size[0]-60), 146)

    def controls(self):
        solving = self.pending_solve or (self.worker is not None and self.worker.isRunning())
        applying = self.applying() or self.pending_apply
        rows = [("auto", "Решить и расставить", 240, not solving and not applying and not self.auto_apply),
                ("solve", "Рассчитать", 140, not solving and not applying),
                ("apply", "Расставить", 150, bool(self.board and self.result) and not solving and not applying),
                ("stop", "Стоп: ПКМ" if self.applying() else "Стоп", 112, applying or solving or self.auto_apply),
                ("toggle", "Скрыть схему" if self.visible_scheme else "Показать схему", 170, True),
                ("close", "Закрыть", 112, True)]
        left = 48
        result = []
        for key, text, width, enabled in rows:
            result.append((key, text, QRectF(left, 160, width, 36), enabled))
            left += width+12
        return result

    def control_at(self, x, y):
        for key, _, rect, _ in self.controls():
            if rect.contains(x, y):
                return key
        return "panel" if self.panel_rect().contains(x, y) else None

    def activate_control(self, key):
        enabled = {name: state for name, _, _, state in self.controls()}
        if not enabled.get(key, False):
            return
        logging.info("BUTTON %s", key)
        {"auto": self.start_auto, "solve": self.start_solve, "apply": self.apply_or_stop, "stop": self.stop_apply,
         "toggle": self.toggle, "close": self.close}[key]()

    def check_note(self):
        if not self.board or self.applying() or self.auto_apply or self.pending_apply or self.pointer.interactive or self.pointer.transition or (self.worker and self.worker.isRunning()):
            return
        try:
            frame = capture(self.target)
            if find_parchment(frame) != self.board.parchment:
                raise ValueError("Поле закрыто или перемещено. нажмите «Рассчитать».")
            for cell, previous in self.anchor_patches.items():
                x, y = map(round, self.board.pixel(cell))
                radius = previous.shape[0] // 2
                current = frame[y-radius:y+radius, x-radius:x+radius]
                if current.shape != previous.shape or np.mean(np.abs(current.astype(float)-previous)) > 35:
                    raise ValueError("Записка изменилась. нажмите «Рассчитать» для нового поля.")
            self.note_mismatches = 0
        except Exception as exc:
            self.note_mismatches += 1
            if self.note_mismatches >= 2:
                self.failed(str(exc))
                logging.info("INVALIDATED %s", exc)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # Gamescope's external-overlay plane uses output pixels, while captured
        # game pixels use its nested resolution. Preserve the game's aspect ratio.
        factor = min(self.output_size[0]/self.logical_size[0], self.output_size[1]/self.logical_size[1])
        painter.translate((self.output_size[0]-self.logical_size[0]*factor)/2,
                          (self.output_size[1]-self.logical_size[1]*factor)/2)
        painter.scale(factor, factor)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel = self.panel_rect()
        painter.setPen(QPen(QColor("#a9bd92"), 1))
        painter.setBrush(QColor(17, 24, 25, 235))
        painter.drawRoundedRect(panel, 12, 12)
        painter.setPen(QColor("#f2eed9"))
        painter.setFont(QFont("DejaVu Sans", 13, QFont.Weight.DemiBold))
        painter.drawText(QRectF(48, 73, panel.width()-36, 49), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                         self.message)
        painter.setFont(QFont("DejaVu Sans", 10))
        painter.setPen(QColor("#c2cec7"))
        painter.drawText(QRectF(48, 123, panel.width()-36, 27), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.detail)
        pointer = getattr(self, "pointer", None)
        for key, text, rect, enabled in self.controls():
            hovered = pointer is not None and pointer.hovered == key
            pressed = pointer is not None and pointer.pressed == key
            background = "#29413b" if enabled else "#20292a"
            if enabled and key == "auto":
                background = "#3c6250"
            if enabled and hovered:
                background = "#405e53" if not pressed else "#526f60"
            if enabled and key == "stop":
                background = "#8d423b" if not hovered else "#b25449"
            painter.setBrush(QColor(background))
            painter.setPen(QPen(QColor("#91ab91" if enabled else "#46534c"), 1))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(QColor("#f2eed9" if enabled else "#78827b"))
            painter.setFont(QFont("DejaVu Sans", 11, QFont.Weight.DemiBold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if not self.visible_scheme or not self.board or not self.result:
            return
        board, result = self.board, self.result
        fixed = {**board.anchors, **result.placements}
        painter.setPen(QPen(QColor(56, 244, 191, 205), max(2, board.scale)))
        for (q, r), aspect in sorted(fixed.items()):
            for dq, dr in NEIGHBORS:
                other_cell = q+dq, r+dr
                if other_cell <= (q, r) or other_cell not in fixed:
                    continue
                other = fixed[other_cell]
                if aspect in self.assets.recipes[other] or other in self.assets.recipes[aspect]:
                    painter.drawLine(QPointF(*board.pixel((q, r))), QPointF(*board.pixel(other_cell)))
        for cell, aspect in sorted(result.placements.items()):
            if cell in self.confirmed_cells:
                continue
            x, y = board.pixel(cell)
            radius = board.scale * 7.8
            polygon = QPolygonF([QPointF(x + radius*math.cos(a*math.pi/3),
                                        y + radius*math.sin(a*math.pi/3)) for a in range(6)])
            painter.setPen(QPen(QColor("#52efb9"), 2))
            painter.setBrush(QColor(229, 219, 178, 245))
            painter.drawPolygon(polygon)
            size = board.scale * 13
            painter.drawPixmap(QRectF(x-size/2, y-size/2, size, size), self.pixmaps[aspect],
                               QRectF(self.pixmaps[aspect].rect()))
        for cell in board.anchors:
            x, y = board.pixel(cell)
            painter.setPen(QPen(QColor("#f2cf78"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(x, y), board.scale*8.5, board.scale*8.5)
        # A readable bill of materials, outside the parchment, avoids tiny labels in hexes.
        stock = Counter(result.placements.values())
        legend_x, legend_y = 30, 225
        legend_w = 300
        height = 62 + 36 * len(stock)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(17, 24, 25, 235))
        painter.drawRoundedRect(QRectF(legend_x, legend_y, legend_w, height), 10, 10)
        painter.setFont(QFont("DejaVu Sans", 11, QFont.Weight.DemiBold))
        painter.setPen(QColor("#f2eed9"))
        painter.drawText(legend_x+15, legend_y+25, "Нужно разместить")
        for i, (aspect, count) in enumerate(sorted(stock.items())):
            top = legend_y+37+i*36
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(229, 219, 178))
            painter.drawRoundedRect(QRectF(legend_x+13, top-2, 32, 32), 5, 5)
            painter.drawPixmap(legend_x+15, top, 28, 28, self.pixmaps[aspect])
            painter.setPen(QColor("#f2eed9"))
            painter.setFont(QFont("DejaVu Sans", 11))
            painter.drawText(legend_x+55, top+21, f"{aspect.capitalize()} × {count}")
        painter.setFont(QFont("DejaVu Sans", 9))
        painter.setPen(QColor("#c2cec7"))
        note = "Запасы учтены" if self.inventory_known else "Запасы не проверены"
        painter.drawText(legend_x+15, legend_y+height-10, note)

    def closeEvent(self, event):
        self.auto_apply = False
        self.pending_apply = False
        self.pending_solve = False
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
        self.watch.stop()
        if self.applying():
            self.apply_worker.cancel.set()
            self.apply_worker.wait(5000)
            if self.apply_worker.isRunning():
                event.ignore()
                QTimer.singleShot(500, self.close)
                return
        self.pointer.close()
        if self.keys:
            self.keys.close()
        self.conn.close()
        if self.worker and self.worker.isRunning():
            # The solver has a bounded deadline; don't destroy a running QThread.
            self.hide()
            self.worker.wait(int((self.budget + 10)*1000))
        logging.info("EXIT")
        event.accept()
        QApplication.instance().quit()


def run(target, assets, budget, inventory, output_dir, inventory_known=False):
    app = QApplication.instance() or QApplication([])
    window = Overlay(target, assets, budget, inventory, output_dir, inventory_known)
    return app.exec()
