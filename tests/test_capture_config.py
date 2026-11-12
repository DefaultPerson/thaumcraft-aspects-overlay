from types import SimpleNamespace as NS

import pytest
from Xlib import X

from aspects_overlay import capture as cap


class Window:
    id = 0x123
    visible = True

    def get_attributes(self):
        return NS(map_state=X.IsViewable if self.visible else X.IsUnmapped)

    def get_geometry(self):
        return NS(width=1280, height=720)

    def get_wm_name(self):
        return 'Custom launcher title'

    def get_full_property(self, *args):
        return NS(value=[345])


class Connection:
    def __init__(self, nested=False):
        self.window, self.nested, self.closed = Window(), nested, False

    def intern_atom(self, name, **kwargs):
        return name if name != 'GAMESCOPE_PID' or self.nested else 0

    def create_resource_object(self, kind, xid):
        assert kind == 'window' and xid == self.window.id
        return self.window

    def get_display_name(self):
        return ':5'

    def screen(self):
        return NS(root=NS(get_full_property=lambda *args: NS(value=[987])))

    def close(self):
        self.closed = True


def test_explicit_window_accepts_custom_launcher_and_releases_connection(tmp_path, monkeypatch):
    (tmp_path/'mods').mkdir()
    conn = Connection()
    monkeypatch.setattr(cap.display, 'Display', lambda name: conn)
    target = cap.discover(':5', tmp_path, 0x123)
    assert target.window == 0x123 and target.game_dir == tmp_path
    assert target.title == 'Custom launcher title' and target.pid == 345
    assert target.output_size == (1280, 720) and not target.gamescope
    assert conn.closed


def test_hidden_explicit_window_is_rejected_and_releases_connection(tmp_path, monkeypatch):
    (tmp_path/'mods').mkdir()
    conn = Connection()
    conn.window.visible = False
    monkeypatch.setattr(cap.display, 'Display', lambda name: conn)
    with pytest.raises(RuntimeError, match='hidden'):
        cap.discover(':5', tmp_path, 0x123)
    assert conn.closed


@pytest.mark.parametrize('arguments', [b'\0'.join([b'gamescope', b'-W', b'2560', b'-H', b'1440']),
                                     b'gamescope\0--output-width=2560\0--output-height=1440\0'])
def test_custom_gamescope_output_size_is_kept(tmp_path, monkeypatch, arguments):
    monkeypatch.setattr(cap.Path, 'read_bytes', lambda _: arguments)
    conn = Connection(nested=True)
    target = cap._inspect_window(conn, conn.window, 345, tmp_path)
    assert target.gamescope and target.output_size == (2560, 1440)
