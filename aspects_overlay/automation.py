"""Place a validated plan through visible Minecraft mouse interactions."""
from __future__ import annotations

from dataclasses import dataclass, replace
import time
import threading
import logging

import cv2
import numpy as np
from Xlib import X, display, protocol
from Xlib.ext import xinput

from .capture import capture
from .matching import fit_rainbow_icon
from .recognition import RecognitionCache, recognize, read_board
from .solver import NEIGHBORS, validate_solution


class PlacementStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class Slot:
    aspect: str
    point: tuple[int, int]
    confidence: float


class Palette:
    """Subtract the installed table artwork, then compare visible icon pixels.

    The bottom of each slot is excluded because it contains the changing count.
    In particular, subtracting wood preserves the very dark Tenebrae silhouette.
    """
    def __init__(self, assets, board):
        self.board = board
        s = board.scale
        self.size = round(16*s)
        self.height = round(self.size*.70)
        self.background = cv2.resize(assets.table[..., :3][..., ::-1],
                                    (round(256*s), round(256*s)), interpolation=cv2.INTER_NEAREST)
        self.icons = {name: cv2.resize(icon, (self.size, self.size))[:self.height].astype(float)
                      for name, icon in assets.icons.items()}

        # Background and tinted templates are fixed for this table geometry.
        # Cache them once; all candidates then share one vectorized comparison.
        self.names = sorted(self.icons)
        self.dynamic_icons = {self.names.index(name): self.icons[name]
                              for name in getattr(assets, 'dynamic_colors', ())}
        icons = np.stack([self.icons[name] for name in self.names]).astype(np.float32)
        self.opaque = (icons[..., 3] > 64).reshape(len(icons), -1)
        self.opaque_counts = self.opaque.sum(axis=1)
        self.templates = {}
        px, py, _, _ = board.parchment
        origin_x, origin_y = round(px-95*s), round(py-9*s)
        for col in range(5):
            for row in range(5):
                x, y = self.point(col, row)
                left, top = x-self.size//2, y-self.size//2
                bg = self.background[top-origin_y:top-origin_y+self.height,
                                     left-origin_x:left-origin_x+self.size].astype(np.float32)
                expected = ((icons[..., :3][..., ::-1]-bg)*(icons[..., 3:]/255)).reshape(len(icons), -1)
                norms = np.sqrt(np.einsum('ij,ij->i', expected, expected))
                self.templates[col, row] = (left, top, bg, expected, norms)

    def point(self, col, row):
        x, y, _, _ = self.board.parchment
        s = self.board.scale
        return round(x + (-77+16*col)*s), round(y + (39+16*row)*s)

    def read(self, frame):
        found = {}
        for cell, (left, top, bg, expected, norms) in self.templates.items():
            crop = frame[top:top+self.height, left:left+self.size]
            if crop.shape != bg.shape or not crop.size:
                raise PlacementStopped("Список аспектов находится за краем снимка.")
            actual = (crop.astype(np.float32)-bg).ravel()
            norm = np.linalg.norm(actual)
            if norm < 20:
                continue
            scores = np.einsum('ij,j->i', expected, actual)/(norms*norm+1e-9)
            dynamic_valid = set()
            if self.dynamic_icons:
                expected = expected.copy()
                for index, icon in self.dynamic_icons.items():
                    match = fit_rainbow_icon(crop, icon, bg)
                    scores[index] = -1
                    if match is not None:
                        _, tinted, _ = match
                        candidate = ((tinted[..., :3][..., ::-1]-bg)*(tinted[..., 3:]/255)).ravel()
                        expected[index] = candidate
                        scores[index] = np.dot(candidate, actual)/(np.linalg.norm(candidate)*norm+1e-9)
                        dynamic_valid.add(index)
            order = np.argsort(scores)
            best, runner = order[-1], order[-2]
            if scores[best] >= .78 and scores[best]-scores[runner] >= .09:
                found[cell] = Slot(self.names[best], self.point(*cell), float(scores[best]))
                continue
            # One observation-derived sparkle mask for every candidate.
            usable = ~((crop[..., 0] > 40) & (crop[..., 1] > 60))
            if not .45 <= float(usable.mean()) <= .90:
                continue
            selected = np.repeat(usable.ravel(), 3)
            visible = actual[selected]
            visible_norm = np.linalg.norm(visible)
            if visible_norm < 100:
                continue
            valid = ((self.opaque_counts > 0) &
                     ((self.opaque & usable.ravel()).sum(axis=1) >= .30*self.opaque_counts))
            for index in self.dynamic_icons:
                if index not in dynamic_valid:
                    valid[index] = False
            indices = np.flatnonzero(valid)
            if len(indices) < 2:
                continue
            masked = expected[indices][:, selected]
            masked_norms = np.sqrt(np.einsum('ij,ij->i', masked, masked))
            scores = np.einsum('ij,j->i', masked, visible)/(masked_norms*visible_norm+1e-9)
            order = np.argsort(scores)
            best, runner = order[-1], order[-2]
            if scores[best] >= .86 and scores[best]-scores[runner] >= .18:
                found[cell] = Slot(self.names[indices[best]], self.point(*cell), float(scores[best]))
        return found

    def arrow(self, direction):
        x, y, _, _ = self.board.parchment
        return round(x+(-56 if direction < 0 else -32)*self.board.scale), round(y+115*self.board.scale)

    def parking(self):
        x, y, _, _ = self.board.parchment
        return round(x-47*self.board.scale), round(y+9*self.board.scale)


def assert_board(expected, current):
    if expected.signature() != current.signature() or expected.parchment != current.parchment:
        logging.warning('Board mismatch: missing=%s added=%s cells_removed=%s cells_added=%s geometry=%s',
                        set(expected.anchors.items())-set(current.anchors.items()),
                        set(current.anchors.items())-set(expected.anchors.items()),
                        expected.cells-current.cells, current.cells-expected.cells,
                        expected.parchment != current.parchment)
        raise PlacementStopped("Поле изменилось. Автозаполнение остановлено; нажмите «Рассчитать».")


def placement_order(board, placements, recipes, prefer=None):
    fixed = dict(board.anchors)
    remaining = dict(placements)
    result = []
    while remaining:
        candidates = []
        for cell, aspect in remaining.items():
            q, r = cell
            for dq, dr in NEIGHBORS:
                other = fixed.get((q+dq, r+dr))
                if other and (other in recipes[aspect] or aspect in recipes[other]):
                    candidates.append(cell)
                    break
        if not candidates:
            raise PlacementStopped("В схеме есть недоступный для соединения фрагмент.")
        cell = min(candidates, key=lambda cell: (prefer(remaining[cell]) if prefer else 0, cell))
        aspect = remaining.pop(cell)
        result.append((cell, aspect))
        fixed[cell] = aspect
    return result


class IsolatedPointer:
    """Lease physical pointer devices on the nested server until disconnect."""
    def __init__(self, conn, target):
        self.conn, self.devices = conn, []
        root = conn.screen().root
        atom = conn.intern_atom('GAMESCOPE_PID', only_if_exists=True)
        prop = root.get_full_property(atom, X.AnyPropertyType) if atom else None
        if not target.gamescope or prop is None:
            raise PlacementStopped("Изоляция мыши доступна только внутри Gamescope.")
        conn.xinput_query_version()
        candidates = [d for d in conn.xinput_query_device(xinput.AllDevices).devices
                      if d.use == xinput.SlavePointer and d.enabled
                      and d.name.startswith('xwayland-')]
        if not candidates:
            raise PlacementStopped("Не найдены отдельные устройства мыши Gamescope.")
        try:
            for device in candidates:
                reply = root.xinput_grab_device(device.deviceid, X.CurrentTime,
                                               xinput.GrabModeAsync, xinput.GrabModeAsync, False,
                                               xinput.ButtonPressMask)
                if reply.status != X.GrabSuccess:
                    raise PlacementStopped("Не удалось заблокировать игровую мышь. Повторите расстановку.")
                self.devices.append(device.deviceid)
            conn.sync()
        except Exception:
            self.close()
            raise

    def close(self):
        for device in self.devices:
            self.conn.xinput_ungrab_device(device, X.CurrentTime)
        self.devices.clear()
        self.conn.sync()


class Mouse:
    def __init__(self, target):
        self.target = target
        self.conn = display.Display(target.display)
        self.root = self.conn.screen().root
        self.window = self.conn.create_resource_object('window', target.window)
        self.held = False
        self.stop_requested = False
        self.position = None
        self.isolation = None
        try:
            self.guard()
            self.isolation = IsolatedPointer(self.conn, target)
        except Exception:
            self.conn.close()
            raise

    def guard(self):
        if self.isolation:
            # Physical clicks stay consumed by the slave grab. Only RMB is
            # interpreted as a stop request; it never reaches Minecraft.
            while self.conn.pending_events():
                event = self.conn.next_event()
                if (event.type == 35 and getattr(event, 'evtype', None) == xinput.ButtonPress
                        and getattr(getattr(event, 'data', None), 'deviceid', None) in self.isolation.devices
                        and event.data.detail == 3):
                    self.stop_requested = True
        focused = self.conn.get_input_focus().focus
        if getattr(focused, "id", None) != self.target.window:
            raise PlacementStopped("Minecraft потерял фокус. Автозаполнение остановлено.")
        pointer = self.root.query_pointer()
        state = pointer.mask
        if not self.isolation and self.position is not None and max(abs(pointer.root_x-self.position[0]), abs(pointer.root_y-self.position[1])) > 12:
            logging.info('Pointer changed: expected=%s actual=%s', self.position, (pointer.root_x, pointer.root_y))
            raise PlacementStopped("Курсор перемещён вручную. Расстановка остановлена; нажмите «Рассчитать» для продолжения.")
        if not self.isolation and not self.held and state & (X.Button1Mask | X.Button2Mask | X.Button3Mask):
            raise PlacementStopped("Нажата кнопка мыши. Автозаполнение остановлено.")

    def move(self, point):
        self.guard()
        self.position = tuple(map(round, point))
        self.send(protocol.event.MotionNotify, 0)

    def send(self, kind, detail):
        # XTEST/warping is routed back through Gamescope's physical devices and
        # would be blocked by our XI2 grabs. Wine accepts addressed X events;
        # they update LWJGL's mouse state without moving the physical cursor.
        if self.position is None:
            raise PlacementStopped("Не задана позиция игрового курсора.")
        x, y = self.position
        root_point = self.root.translate_coords(self.window, x, y)
        self.window.send_event(kind(
            time=X.CurrentTime, root=self.root, window=self.window, child=X.NONE,
            root_x=root_point.x, root_y=root_point.y, event_x=x, event_y=y,
            state=X.Button1Mask if self.held else 0, same_screen=1, detail=detail), event_mask=0)
        self.conn.sync()

    def down(self):
        self.guard()
        self.send(protocol.event.ButtonPress, 1)
        self.held = True

    def up(self):
        if self.held:
            self.send(protocol.event.ButtonRelease, 1)
            self.held = False

    def close(self):
        try:
            self.up()
        finally:
            try:
                if self.isolation:
                    self.isolation.close()
            finally:
                self.conn.close()


class AutoPlacer:
    def __init__(self, target, assets, board, placements, cancel=None, progress=None,
                 timeout=180, mouse_factory=Mouse, capture_fn=capture):
        self.target, self.assets, self.board = target, assets, board
        self.placements = dict(placements)
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda *_: None)
        self.timeout, self.mouse_factory, self.capture_fn = timeout, mouse_factory, capture_fn
        self.confirmed = {}
        self.last_frame = None
        self.completed = False
        self.recognition_cache = RecognitionCache()
        self.metrics = {"scrolls": 0, "search_s": 0.0, "verify_s": 0.0, "recognition_rechecks": 0}
        self.elapsed_s = 0.0

    def check(self):
        if self.cancel.is_set():
            raise PlacementStopped("Остановлено пользователем.")
        if time.monotonic() > self.deadline:
            raise PlacementStopped("Истёк лимит автозаполнения (180 с).")
        self.mouse.guard()
        if getattr(self.mouse, 'stop_requested', False):
            self.cancel.set()
            raise PlacementStopped("Остановлено пользователем.")

    def wait(self, seconds):
        if self.cancel.wait(seconds):
            raise PlacementStopped("Остановлено пользователем.")
        self.check()

    def frame(self):
        self.check()
        self.last_frame = (capture(self.target, self.mouse.conn)
                           if self.capture_fn is capture and isinstance(self.mouse, Mouse)
                           else self.capture_fn(self.target))
        return self.last_frame

    def observe(self, expected, *, previous=None, allow_complete=False):
        started = time.monotonic()
        def retry(message):
            self.metrics["recognition_rechecks"] += 1
            self.progress(len(self.confirmed), len(self.placements), "Перепроверяю поле…")
        try:
            return read_board(self.frame, self.assets, cache=self.recognition_cache,
                              expected=expected, previous=previous, allow_complete=allow_complete,
                              check=self.check, wait=self.wait, on_retry=retry, recognizer=recognize)[0]
        finally:
            self.metrics["verify_s"] += time.monotonic()-started

    def fresh(self):
        expected = replace(self.board, anchors={**self.board.anchors, **self.confirmed})
        current = self.observe(expected)
        assert_board(expected, current)
        return current

    def click(self, point):
        self.mouse.move(point)
        self.wait(.06)
        try:
            self.mouse.down()
            self.wait(.08)
        finally:
            self.mouse.up()
        # Keep the addressed cursor over the arrow until a captured frame
        # acknowledges the page change (or the bounded retry expires).
        self.wait(.06)
        self.metrics["scrolls"] += 1

    @staticmethod
    def fingerprint(slots):
        return tuple(sorted((cell, slot.aspect) for cell, slot in slots.items()))

    @staticmethod
    def page_changed(before, after):
        # Sparkles hide/reveal symbols without moving the page. Scrolling
        # replaces several confidently identified symbols at fixed positions.
        return sum(before[cell].aspect != after[cell].aspect
                   for cell in before.keys() & after.keys()) >= 3

    @staticmethod
    def search_direction(aspect, slots):
        names = [slot.aspect for _, slot in sorted(slots.items())]
        if len(names) < 6 or names != sorted(names) or len(set(names)) != len(names):
            return None  # Unknown ordering: retain the bounded exhaustive search.
        if aspect < names[0]:
            return -1
        if aspect > names[-1]:
            return 1
        return 0  # It may be on this page, hidden by a sparkle.

    def find_source(self, aspect):
        started = time.monotonic()
        try:
            return self._find_source(aspect)
        finally:
            self.metrics["search_s"] += time.monotonic()-started

    def _find_source(self, aspect):
        slots = self.palette.read(self.frame())
        fallback_direction = -1
        ends = set()
        for _ in range(36):
            direction = self.search_direction(aspect, slots)
            visible_deadline = time.monotonic() + (1.2 if direction in (0, None) else 0)
            while True:
                matches = [slot for slot in slots.values() if slot.aspect == aspect]
                if len(matches) == 1:
                    return matches[0]
                if time.monotonic() >= visible_deadline:
                    break
                self.wait(.10)
                slots = self.palette.read(self.frame())
            if len(slots) < 6:
                raise PlacementStopped("Список аспектов распознан неуверенно; клики остановлены.")
            direction = self.search_direction(aspect, slots)
            if direction == 0:
                break  # Target's alphabetical interval is visible but unavailable.
            direction = direction or fallback_direction
            if direction in ends:
                break
            before = slots
            changed = False
            for attempt in range(2):
                self.fresh()
                self.click(self.palette.arrow(direction))
                end = time.monotonic()+.65
                while time.monotonic() < end:
                    slots = self.palette.read(self.frame())
                    if self.page_changed(before, slots):
                        changed = True
                        break
                    self.wait(.06)
                if changed:
                    break
            self.mouse.move(self.palette.parking())
            if not changed:
                ends.add(direction)
                fallback_direction = -direction
        raise PlacementStopped(f"Не найден доступный {aspect.capitalize()}. нажмите «Рассчитать» после пополнения или исключения аспекта.")

    def run(self):
        valid, reason = validate_solution(self.board.cells, self.board.anchors,
                                          self.assets.recipes, self.placements)
        if not valid:
            raise PlacementStopped("Некорректная схема: "+reason)
        started = time.monotonic()
        self.deadline = started+self.timeout
        self.mouse = self.mouse_factory(self.target)
        try:
            self.palette = Palette(self.assets, self.board)
            self.fresh()
            self.mouse.move(self.palette.parking())
            self.wait(.25)
            total = len(self.placements)
            remaining = dict(self.placements)
            ranks = {name: i for i, name in enumerate(sorted(self.assets.recipes))}
            for _ in range(total):
                visible = {slot.aspect for slot in self.palette.read(self.frame()).values()}
                visible_ranks = [ranks[name] for name in visible if name in ranks]
                def preference(name):
                    if name in visible:
                        return 0
                    return 1 + min((abs(ranks[name]-rank) for rank in visible_ranks), default=0)
                current_board = replace(self.board, anchors={**self.board.anchors, **self.confirmed})
                cell, aspect = placement_order(current_board, remaining, self.assets.recipes, preference)[0]
                self.progress(len(self.confirmed), total, f"Ищу {aspect.capitalize()}")
                source = self.find_source(aspect)
                self.fresh()
                # Re-read immediately before pressing; never trust an old page index.
                source_confirmed = False
                confirm_deadline = time.monotonic()+2
                while time.monotonic() < confirm_deadline:
                    slots = self.palette.read(self.frame())
                    if any(s.aspect == aspect and s.point == source.point for s in slots.values()):
                        source_confirmed = True
                        break
                    # A sparkle can briefly obscure even a previously identified
                    # icon. Wait for another frame without clicking or scrolling.
                    self.wait(.15)
                if not source_confirmed:
                    raise PlacementStopped("Список аспектов изменился перед перетаскиванием.")
                source_point = (source.point[0], source.point[1]-round(2*self.board.scale))
                self.mouse.move(source_point)
                self.wait(.08)
                dragged = False
                try:
                    self.mouse.down()
                    self.wait(.08)
                    target = self.board.pixel(cell)
                    for step in range(1, 4):
                        point = tuple(a+(b-a)*step/3 for a, b in zip(source_point, target))
                        self.mouse.move(point)
                        self.wait(.025)
                    self.wait(.08)
                    dragged = True
                finally:
                    if not dragged and self.mouse.held:
                        # A cancelled drag must not drop into an intermediate hex.
                        try:
                            self.mouse.move(self.palette.parking())
                        except PlacementStopped:
                            pass
                    self.mouse.up()
                self.wait(.12)
                self.mouse.move(self.palette.parking())
                before = replace(self.board, anchors={**self.board.anchors, **self.confirmed})
                expected = replace(before, anchors={**before.anchors, cell: aspect})
                # A delayed server acknowledgement may still show the exact
                # pre-drop board. Re-read it without repeating the drag.
                current = self.observe(expected, previous=before,
                                       allow_complete=len(self.confirmed)+1 == total)
                if current is None:
                    self.completed = True
                self.confirmed[cell] = aspect
                remaining.pop(cell)
                self.progress(len(self.confirmed), total, aspect.capitalize())
            return dict(self.confirmed)
        finally:
            self.elapsed_s = time.monotonic()-started
            self.mouse.close()
