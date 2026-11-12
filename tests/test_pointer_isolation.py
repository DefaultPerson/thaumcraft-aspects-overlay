from types import SimpleNamespace as NS

import pytest
from Xlib import X
from Xlib.ext import xinput

from aspects_overlay import automation as auto


class Connection:
    def __init__(self, failure=None, nested=True):
        self.failure, self.nested = failure, nested
        self.grabbed, self.released = [], []

    def screen(self): return NS(root=self)
    def intern_atom(self, *a, **kw): return 1
    def get_full_property(self, *a): return NS(value=[123]) if self.nested else None
    def xinput_query_version(self): pass
    def sync(self): pass

    def xinput_query_device(self, *_):
        return NS(devices=[NS(deviceid=i, use=use, name=name, enabled=enabled)
                           for i, use, name, enabled in [
                               (2, xinput.MasterPointer, 'Virtual core pointer', 1),
                               (4, xinput.SlavePointer, 'Virtual core XTEST pointer', 1),
                               (6, xinput.SlavePointer, 'xwayland-pointer:21', 1),
                               (7, xinput.SlavePointer, 'xwayland-relative-pointer:21', 1),
                               (9, xinput.SlaveKeyboard, 'xwayland-keyboard:21', 1),
                               (10, xinput.SlavePointer, 'xwayland-touch:21', 0)]])

    def xinput_grab_device(self, device, *_):
        self.grabbed.append(device)
        return NS(status=X.AlreadyGrabbed if device == self.failure else X.GrabSuccess)

    def xinput_ungrab_device(self, device, *_): self.released.append(device)


def test_only_nested_physical_pointers_are_leased():
    conn = Connection()
    lease = auto.IsolatedPointer(conn, NS(gamescope=True))
    assert conn.grabbed == [6, 7]
    lease.close()
    lease.close()
    assert conn.released == [6, 7]


def test_partial_failure_releases_earlier_grabs():
    conn = Connection(failure=7)
    with pytest.raises(auto.PlacementStopped):
        auto.IsolatedPointer(conn, NS(gamescope=True))
    assert conn.released == [6]


@pytest.mark.parametrize('flag,property_present', [(False, True), (True, False)])
def test_desktop_server_cannot_be_grabbed(flag, property_present):
    conn = Connection(nested=property_present)
    with pytest.raises(auto.PlacementStopped):
        auto.IsolatedPointer(conn, NS(gamescope=flag))
    assert not conn.grabbed


def test_release_precedes_unblocking_even_after_send_error():
    events = []
    mouse = auto.Mouse.__new__(auto.Mouse)
    mouse.held = True
    mouse.conn = NS(close=lambda: events.append('disconnect'))
    mouse.isolation = NS(close=lambda: events.append('unblock'))
    def send(*_):
        events.append('release')
        raise RuntimeError('connection lost')
    mouse.send = send
    with pytest.raises(RuntimeError):
        mouse.close()
    assert events == ['release', 'unblock', 'disconnect']


def test_game_guard_ignores_detached_physical_mouse_but_checks_focus():
    mouse = auto.Mouse.__new__(auto.Mouse)
    mouse.target = NS(window=123)
    mouse.isolation = object()
    mouse.position, mouse.held = (20, 20), False
    mouse.root = NS(query_pointer=lambda: NS(root_x=500, root_y=500, mask=X.Button1Mask))
    mouse.conn = NS(get_input_focus=lambda: NS(focus=NS(id=123)), pending_events=lambda: 0)
    mouse.guard()
    mouse.conn.get_input_focus = lambda: NS(focus=NS(id=456))
    with pytest.raises(auto.PlacementStopped, match='фокус'):
        mouse.guard()


def test_only_grabbed_right_click_requests_stop():
    from collections import deque
    import time
    mouse = auto.Mouse.__new__(auto.Mouse)
    mouse.target = NS(window=123)
    mouse.isolation = NS(devices=[6, 7])
    mouse.position, mouse.held, mouse.stop_requested = (20, 20), True, False
    mouse.root = NS(query_pointer=lambda: NS(root_x=500, root_y=500, mask=0))
    events = deque([NS(type=35, evtype=xinput.ButtonPress, data=NS(deviceid=6, detail=1)),
                    NS(type=35, evtype=xinput.ButtonPress, data=NS(deviceid=4, detail=3))])
    mouse.conn = NS(get_input_focus=lambda: NS(focus=NS(id=123)),
                    pending_events=lambda: len(events), next_event=events.popleft)
    mouse.guard()
    assert not mouse.stop_requested
    events.append(NS(type=35, evtype=xinput.ButtonPress, data=NS(deviceid=7, detail=3)))
    placer = auto.AutoPlacer(None, None, None, {})
    placer.mouse, placer.deadline = mouse, time.monotonic()+10
    with pytest.raises(auto.PlacementStopped, match='пользователем'):
        placer.check()
    assert placer.cancel.is_set()
    # Cleanup must still be able to move a held aspect off the grid before
    # releasing it. The stop flag is acted on by AutoPlacer, not Mouse.guard.
    mouse.guard()
