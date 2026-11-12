from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import os
import threading

import cv2
import numpy as np
import pytest

from aspects_overlay import automation as auto
from aspects_overlay.assets import Assets
from aspects_overlay.recognition import Board, recognize


def board():
    return Board({(0, 0), (1, 0), (2, 0)}, {(0, 0): 'a', (2, 0): 'a'}, (100, 100), 3, (0, 0, 444, 444))


def test_changed_board_or_geometry_is_rejected():
    b = board()
    for changed in [replace(b, anchors={(0, 0): 'b', (2, 0): 'a'}),
                    replace(b, cells=b.cells | {(3, 0)}),
                    replace(b, parchment=(1, 0, 444, 444))]:
        with pytest.raises(auto.PlacementStopped):
            auto.assert_board(b, changed)
    auto.assert_board(b, replace(b))


def test_order_connects_every_new_aspect_to_an_existing_one():
    b = Board({(q, 0) for q in range(5)}, {(0, 0): 'a', (4, 0): 'a'}, (0, 0), 3, (0, 0, 444, 444))
    order = auto.placement_order(b, {(1, 0): 'b', (2, 0): 'a', (3, 0): 'b'}, {'a': (), 'b': ('a',)})
    assert order == [((1, 0), 'b'), ((2, 0), 'a'), ((3, 0), 'b')]


def test_order_prefers_visible_aspects_without_breaking_connections():
    b = Board({(q, 0) for q in range(5)}, {(0, 0): 'a', (4, 0): 'a'}, (0, 0), 3, (0, 0, 444, 444))
    recipes = {'a': (), 'b': ('a',), 'c': ('a',)}
    placements = {(1, 0): 'b', (2, 0): 'a', (3, 0): 'c'}
    order = auto.placement_order(b, placements, recipes, lambda name: 0 if name == 'c' else 1)
    assert order == [((3, 0), 'c'), ((1, 0), 'b'), ((2, 0), 'a')]


@pytest.mark.parametrize('when', ['before', 'held'])
def test_cancel_releases_mouse_and_stops_further_input(monkeypatch, when):
    cancelled = threading.Event()
    events = []
    class Mouse:
        def __init__(self, target): self.held = False
        def guard(self): pass
        def move(self, point): events.append('move')
        def down(self):
            events.append('down'); self.held = True; cancelled.set()
        def up(self):
            if self.held: events.append('up'); self.held = False
        def close(self): self.up(); events.append('close')
    class Palette:
        def __init__(self, *_): pass
        def parking(self): return (0, 0)
        def read(self, frame): return {(0, 0): auto.Slot('b', (20, 20), 1)}
    monkeypatch.setattr(auto, 'Palette', Palette)
    monkeypatch.setattr(auto, 'recognize', lambda *_: board())
    placer = auto.AutoPlacer(None, SimpleNamespace(recipes={'a': (), 'b': ('a',)}), board(),
                            {(1, 0): 'b'}, cancelled, mouse_factory=Mouse, capture_fn=lambda _: None)
    if when == 'before': cancelled.set()
    with pytest.raises(auto.PlacementStopped, match='пользователем'):
        placer.run()
    assert not placer.confirmed
    assert events == (['close'] if when == 'before' else ['move', 'move', 'down', 'move', 'up', 'close'])


def test_palette_on_live_fixture():
    root, image = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_PALETTE_IMAGE')
    if not root or not image:
        pytest.skip('Local game artwork and palette fixture are optional')
    assets = Assets(Path(root))
    frame = cv2.imread(image)
    b = recognize(frame, assets)
    slots = auto.Palette(assets, b).read(frame)
    assert slots[(0, 1)].aspect == 'motus'
    assert slots[(3, 1)].aspect == 'tenebrae'
    assert slots[(3, 1)].confidence > .9
    assert slots[(4, 4)].aspect == 'vinculum'


def test_terra_visible_under_bonus_sparkle():
    root, image = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_TERRA_IMAGE')
    if not root or not image:
        pytest.skip('Terra regression uses a private live capture')
    assets = Assets(Path(root))
    frame = cv2.imread(image)
    slots = auto.Palette(assets, recognize(frame, assets)).read(frame)
    assert slots[(2, 3)].aspect == 'terra'
    assert slots[(2, 3)].confidence > .86
    # Avaritia's Terminus uses a separate texture path and must not become Terra.
    assert slots[(2, 2)].aspect == 'terminus'


def test_animation_is_not_a_page_change():
    before = {(i, 0): auto.Slot(name, (i*48, 0), .9)
              for i, name in enumerate(['ordo', 'motus', 'terra', 'pannus'])}
    obscured = {c: s for c, s in before.items() if s.aspect != 'ordo'}
    assert not auto.AutoPlacer.page_changed(before, obscured)
    assert not auto.AutoPlacer.page_changed(obscured, before)
    shifted = {(i, 0): auto.Slot(name, (i*48, 0), .9)
               for i, name in enumerate(['mortuus', 'perditio', 'terra', 'vitreus'])}
    assert auto.AutoPlacer.page_changed(before, shifted)


def test_search_waits_for_animation_before_scrolling():
    calls = []
    class AnimatedPalette:
        def read(self, frame):
            calls.append('frame')
            names = ['mortuus', 'motus', 'pannus', 'perditio', 'perfodio', 'permutatio']
            slots = {(i, 0): auto.Slot(name, (i*48, 0), .9) for i, name in enumerate(names)}
            if len(calls) >= 3:
                slots[(0, 2)] = auto.Slot('ordo', (630, 456), .81)
            return slots
    placer = auto.AutoPlacer(None, None, None, {})
    placer.palette = AnimatedPalette()
    placer.frame = lambda: None
    placer.wait = lambda _: None
    placer.fresh = lambda: pytest.fail('Scrolled before waiting for an unobscured frame')
    assert placer.find_source('ordo').point == (630, 456)
    assert len(calls) == 3


def test_search_uses_alphabetical_direction_and_detects_unordered_pages():
    slots = {(i, 0): auto.Slot(name, (0, 0), .9) for i, name in enumerate(
        ['mortuus', 'motus', 'pannus', 'perditio', 'perfodio', 'permutatio'])}
    assert auto.AutoPlacer.search_direction('aer', slots) == -1
    assert auto.AutoPlacer.search_direction('volatus', slots) == 1
    assert auto.AutoPlacer.search_direction('ordo', slots) == 0
    slots[(0, 0)], slots[(1, 0)] = slots[(1, 0)], slots[(0, 0)]
    assert auto.AutoPlacer.search_direction('aer', slots) is None
