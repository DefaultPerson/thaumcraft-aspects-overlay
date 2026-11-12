import colorsys
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from aspects_overlay.assets import Assets
from aspects_overlay.automation import Palette
from aspects_overlay.matching import fit_rainbow_icon
from aspects_overlay.recognition import RecognitionCache, recognize


@pytest.fixture(scope='module')
def assets():
    root = os.getenv('ASPECTS_GAME_DIR')
    if not root:
        pytest.skip('Rainbow aspect regression uses locally installed artwork')
    return Assets(Path(root))


@pytest.fixture(scope='module')
def captures():
    root = os.getenv('ASPECTS_TINCTUREM_DIR')
    if not root:
        pytest.skip('Rainbow aspect regression uses private live captures')
    return Path(root)


def composite(icon, background, hue=None):
    rgb = icon[..., :3].astype(float)
    if hue is not None:
        rgb *= colorsys.hsv_to_rgb(hue/360, 1, 1)
    alpha = icon[..., 3:]/255
    return (rgb[..., ::-1]*alpha+background*(1-alpha)).astype(np.uint8)


def test_tincturem_uses_alfheim_texture_recipe_and_dynamic_colour(assets):
    assert assets.icons['tincturem'].shape == (32, 32, 4)
    assert assets.recipes['tincturem'] == ('lux', 'ordo')
    assert 'tincturem' in assets.dynamic_colors


def test_rainbow_shape_survives_all_hues_and_rejects_other_aspects(assets):
    icon = cv2.resize(assets.icons['tincturem'], (48, 48))
    background = np.array([100, 165, 205])
    blank = np.tile(background, (54, 54, 1)).astype(np.uint8)
    for hue in range(0, 360, 10):
        crop = blank.copy()
        crop[3:51, 3:51] = composite(icon, background, hue)
        match = fit_rainbow_icon(crop, icon, background)
        assert match is not None, hue
        assert match[0] > .99 and match[2] == (3, 3)
    assert fit_rainbow_icon(blank, icon, background) is None
    for name, other in assets.icons.items():
        if name == 'tincturem':
            continue
        crop = blank.copy()
        crop[3:51, 3:51] = composite(cv2.resize(other, (48, 48)), background)
        assert fit_rainbow_icon(crop, icon, background) is None, name


def test_animated_tincturem_keeps_the_whole_live_board(assets, captures):
    cache = RecognitionCache()
    paths = sorted(captures.glob('frame-??.png'))
    assert len(paths) == 18
    expected = {(-3, 0): 'superbia', (-3, 3): 'tutamen', (0, -3): 'desidia',
                (0, 3): 'praecantatio', (3, -3): 'tincturem', (3, 0): 'iter'}
    signatures = set()
    for path in paths:
        board = recognize(cv2.imread(str(path)), assets, cache)
        assert board.anchors == expected, path.name
        assert len(board.cells) == 33
        signatures.add(board.signature())
    assert len(signatures) == 1


def test_palette_finds_tincturem_through_the_colour_cycle(assets, captures):
    frame = cv2.imread(str(captures/'before.png'))
    palette = Palette(assets, recognize(frame, assets))
    original = palette.read(frame)
    cell = (0, 0)
    left, top, background, _, _ = palette.templates[cell]
    for hue in range(0, 360, 10):
        tinted = frame.copy()
        tinted[top:top+palette.height, left:left+palette.size] = composite(
            palette.icons['tincturem'], background, hue)
        slots = palette.read(tinted)
        assert slots[cell].aspect == 'tincturem', hue
        assert slots[cell].confidence > .99
        assert {c: s.aspect for c, s in slots.items() if c != cell} == {
            c: s.aspect for c, s in original.items() if c != cell}
    # Fitting must not change the reusable palette templates.
    assert palette.read(frame) == original
