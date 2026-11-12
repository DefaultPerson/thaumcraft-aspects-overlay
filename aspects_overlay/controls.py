"""Clickable controls above Gamescope's game surface.

Wine can hold an active pointer grab even in a Minecraft GUI. While the cursor
is over our panel, use Gamescope's interactive-overlay focus; return input to the
game on leaving it. A passive button grab consumes panel clicks and replays all
other clicks. No game or compositor configuration is modified.
"""
import logging
import time

from PySide6.QtCore import QSocketNotifier, QTimer
from Xlib import X, Xatom, display


class PointerControls:
    def __init__(self, target, window_id, factor, hit_test, activate, redraw):
        self.target, self.factor = target, factor
        self.hit_test, self.activate, self.redraw = hit_test, activate, redraw
        self.conn = display.Display(target.display)
        self.root = self.conn.screen().root
        self.window = self.conn.create_resource_object('window', window_id)
        self.pressed = self.hovered = None
        self.closed = self.interactive = False
        self.transition = None
        self.return_callback = None
        self.ignore_until = 0
        failures = []
        self.conn.set_error_handler(lambda exc, request: failures.append(str(exc)))
        self.root.grab_button(1, X.AnyModifier, False, X.ButtonPressMask | X.ButtonReleaseMask,
                              X.GrabModeSync, X.GrabModeAsync, X.NONE, X.NONE)
        self.conn.sync()
        if failures:
            self.conn.close()
            raise RuntimeError("Не удалось включить кнопки оверлея: "+"; ".join(failures))
        self.notifier = QSocketNotifier(self.conn.fileno(), QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self.read)
        self.poll = QTimer()
        self.poll.timeout.connect(self.refresh)
        self.poll.start(25)

    def focused_id(self):
        return getattr(self.conn.get_input_focus().focus, 'id', None)

    def logical(self, x, y):
        return (x/self.factor, y/self.factor) if self.interactive else (x, y)

    def set_interactive(self, enabled, point):
        if not self.target.gamescope:
            return
        self.interactive = enabled
        values = [('GAMESCOPE_EXTERNAL_OVERLAY', int(not enabled)),
                  ('STEAM_OVERLAY', int(enabled)), ('STEAM_INPUT_FOCUS', int(enabled))]
        for key, value in values:
            self.window.change_property(self.conn.intern_atom(key), Xatom.CARDINAL, 32, [value])
        self.conn.flush()
        self.transition = (point, time.monotonic()+.8)

    def return_to_game(self, callback, point):
        self.return_callback = callback
        self.set_interactive(False, point)

    def read(self, *_):
        while not self.closed and self.conn.pending_events():
            event = self.conn.next_event()
            if event.type == X.ButtonPress:
                focused = self.focused_id() in (self.target.window, self.window.id)
                hit = self.hit_test(*self.logical(event.root_x, event.root_y)) if focused else None
                if self.transition:
                    hit = 'panel'
                if hit is None:
                    self.conn.allow_events(X.ReplayPointer, event.time)
                else:
                    self.pressed = hit
                    self.conn.allow_events(X.AsyncPointer, event.time)
                    self.redraw()
                self.conn.flush()
            elif event.type == X.ButtonRelease:
                pressed, self.pressed = self.pressed, None
                hit = self.hit_test(*self.logical(event.root_x, event.root_y))
                self.conn.ungrab_pointer(event.time)
                self.conn.flush()
                self.redraw()
                if pressed is not None and hit == pressed and not self.transition:
                    self.activate(pressed)

    def refresh(self):
        self.read()
        if self.closed:
            return
        focus = self.focused_id()
        if self.transition:
            point, deadline = self.transition
            expected = self.window.id if self.interactive else self.target.window
            if focus == expected:
                scale = self.factor if self.interactive else 1
                self.root.warp_pointer(round(point[0]*scale), round(point[1]*scale))
                self.conn.sync()
                self.transition = None
                self.ignore_until = time.monotonic()+.12
                if self.return_callback:
                    callback, self.return_callback = self.return_callback, None
                    QTimer.singleShot(150, callback)
            elif time.monotonic() > deadline:
                logging.warning('Panel focus transition timed out')
                self.transition = None
                if self.return_callback:
                    self.activate('stop')
                self.return_callback = None
                self.set_interactive(False, point) if self.interactive else None
            return
        if time.monotonic() < self.ignore_until:
            return
        point = self.root.query_pointer()
        logical = self.logical(point.root_x, point.root_y)
        hovered = self.hit_test(*logical) if focus in (self.target.window, self.window.id) else None
        # Stop before taking focus away from a running mouse operation. The
        # user need not race an automated drag to press and release the button.
        if hovered == 'stop' and self.hovered != hovered:
            self.activate('stop')
        if self.target.gamescope and not point.mask & (X.Button1Mask | X.Button2Mask | X.Button3Mask):
            if hovered is not None and not self.interactive and focus == self.target.window:
                self.set_interactive(True, logical)
            elif hovered is None and self.interactive:
                self.set_interactive(False, logical)
        if self.hovered != hovered:
            self.hovered = hovered
            self.redraw()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.poll.stop()
        self.notifier.setEnabled(False)
        if self.interactive:
            point = self.root.query_pointer()
            self.set_interactive(False, self.logical(point.root_x, point.root_y))
        self.root.ungrab_button(1, X.AnyModifier)
        self.conn.ungrab_pointer(X.CurrentTime)
        self.conn.close()
