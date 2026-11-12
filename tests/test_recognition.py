import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from aspects_overlay.assets import Assets
from aspects_overlay.recognition import RecognitionError, find_parchment, recognize


def test_desktop_without_parchment_is_rejected():
    with pytest.raises(RecognitionError, match="Поле исследований не найдено"):
        find_parchment(np.zeros((600, 800, 3), dtype=np.uint8))


def test_grayscale_is_not_silently_misread():
    with pytest.raises(RecognitionError, match="цветной"):
        find_parchment(np.zeros((600, 800), dtype=np.uint8))


@pytest.mark.skipif(not os.environ.get("ASPECTS_TEST_IMAGE") or not os.environ.get("ASPECTS_GAME_DIR"),
                    reason="Live regression uses locally installed game assets and a private screenshot")
def test_user_note_regression():
    assets = Assets(Path(os.environ["ASPECTS_GAME_DIR"]))
    image = cv2.imread(os.environ["ASPECTS_TEST_IMAGE"])
    board = recognize(image, assets)
    expected = {(q, r) for q in range(-3, 4) for r in range(-3, 4)
                if max(abs(q), abs(r), abs(q+r)) <= 3} - {(0, -3), (0, -1), (0, 2), (-1, 3)}
    assert board.cells == expected
    assert board.anchors == {(-2, -1): "aer", (3, -3): "alienis", (-3, 3): "iter", (2, 1): "perditio"}


@pytest.mark.parametrize('filename, anchor_count', [('mortuus.png', 9), ('current-full.png', 10)])
def test_filled_neighbours_preserve_empty_cells(filename, anchor_count):
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_PROGRESS_DIR')
    if not root or not fixtures:
        pytest.skip('Partial-note regression uses private live captures')
    assets = Assets(Path(root))
    board = recognize(cv2.imread(str(Path(fixtures)/filename)), assets)
    expected = {(q, r) for q in range(-3, 4) for r in range(-3, 4)
                if max(abs(q), abs(r), abs(q+r)) <= 3} - {(0, -3), (0, -1), (0, 2), (-1, 3)}
    assert board.cells == expected
    assert len(board.anchors) == anchor_count
    assert board.anchors[(0, 1)] == 'mortuus'
    assert (0, 0) in board.cells and (0, 0) not in board.anchors
    assert (1, 1) in board.cells and (1, 1) not in board.anchors


@pytest.mark.parametrize('filename, completed', [('before.png', False), ('completed-game.png', True)])
def test_native_research_completion_marker(filename, completed):
    from aspects_overlay.recognition import research_complete
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_PROGRESS_DIR')
    if not root or not fixtures:
        pytest.skip('Completion regression uses private live captures')
    assets = Assets(Path(root))
    frame = cv2.imread(str(Path(fixtures)/filename))
    assert research_complete(frame, assets, (861, 243, 444, 444)) is completed
    assert not research_complete(frame, assets, (0, 0, 444, 444))


def test_cached_recognition_detects_new_cells_and_replaced_notes():
    from aspects_overlay.recognition import RecognitionCache
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_PROGRESS_DIR')
    if not root or not fixtures:
        pytest.skip('Recognition cache regression uses private live captures')
    assets = Assets(Path(root))
    cache = RecognitionCache()
    # Repeated image, new placements, and a return to the original note must
    # all match independent recognition, including neighbour masking.
    for filename in ['before.png', 'before.png', 'mortuus.png', 'current-full.png', 'before.png']:
        frame = cv2.imread(str(Path(fixtures)/filename))
        actual = recognize(frame, assets, cache)
        expected = recognize(frame, assets)
        assert actual.to_dict() == expected.to_dict()
        assert len(cache.cells) == 61
    with pytest.raises(RecognitionError):
        recognize(np.zeros_like(frame), assets, cache)


def test_translucent_spiritus_is_not_an_empty_destination():
    from aspects_overlay.recognition import RecognitionCache
    from aspects_overlay.solver import solve
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_SPIRITUS_DIR')
    if not root or not fixtures:
        pytest.skip('Translucent Spiritus regression uses private live captures')
    assets = Assets(Path(root))
    cache = RecognitionCache()
    files = sorted(Path(fixtures).glob('frame-*.png'))
    assert len(files) == 12
    signatures = set()
    for path in files:
        board = recognize(cv2.imread(str(path)), assets, cache)
        assert board.anchors[(1, -1)] == 'spiritus'
        assert len(board.anchors) == 13
        signatures.add(board.signature())
    assert len(signatures) == 1
    result = solve(board.cells, board.anchors, assets.recipes)
    assert (1, -1) not in result.placements
    assert result.placements == {(0, 0): 'desidia'}


def test_link_glows_preserve_vinculum_and_thin_vacuos():
    from aspects_overlay.recognition import RecognitionCache
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_LINK_GLOW_DIR')
    if not root or not fixtures:
        pytest.skip('Animated link regression uses private live captures')
    assets = Assets(Path(root))
    cache = RecognitionCache()
    files = sorted(Path(fixtures).glob('frame-*.png'))
    assert len(files) >= 24
    expected = {(-4, 2): 'aer', (-4, 3): 'vacuos', (-4, 4): 'tempus',
                (-3, -1): 'motus', (-3, 0): 'aer', (-3, 1): 'lux',
                (-2, 1): 'aer', (-1, 1): 'motus', (0, 1): 'vinculum',
                (1, 1): 'perditio', (1, 3): 'praecantatio', (2, -4): 'vacuos',
                (2, -3): 'tempus', (2, -2): 'ordo', (2, -1): 'permutatio',
                (2, 0): 'permutatio', (3, -1): 'perditio', (4, -2): 'permutatio'}
    signatures = set()
    for path in files:
        board = recognize(cv2.imread(str(path)), assets, cache)
        assert board.anchors == expected
        assert len(board.cells) == 55
        signatures.add(board.signature())
    assert len(signatures) == 1


def test_temporal_confirmation_recovers_animated_messis():
    from aspects_overlay.recognition import RecognitionCache, read_board
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_ANIMATION_DIR')
    if not root or not fixtures:
        pytest.skip('Animated Messis regression uses private live captures')
    assets = Assets(Path(root))
    files = sorted(Path(fixtures).glob('frame-*.png'))
    assert len(files) == 30
    cache = RecognitionCache()
    signatures = set()
    for path in files:
        board = recognize(cv2.imread(str(path)), assets, cache, True)
        assert board.anchors[(3, 0)] == 'messis'
        assert len(board.anchors) == 5 and len(board.cells) == 33
        signatures.add(board.signature())
    assert len(signatures) == 1
    # These frames originally failed alone. One request must confirm the same
    # complete board without requiring another button press.
    frames = [cv2.imread(str(path)) for path in files[:2]]
    actual, _ = read_board(iter(frames).__next__, assets, wait=lambda _: None)
    assert actual.signature() == board.signature()


@pytest.mark.parametrize('variable, pattern', [
    ('ASPECTS_PROGRESS_DIR', 'before.png'),
    ('ASPECTS_PROGRESS_DIR', 'mortuus.png'),
    ('ASPECTS_PROGRESS_DIR', 'current-full.png'),
    ('ASPECTS_SPIRITUS_DIR', 'frame-*.png'),
    ('ASPECTS_LINK_GLOW_DIR', 'frame-*.png'),
])
def test_temporal_candidates_do_not_change_known_boards(variable, pattern):
    from aspects_overlay.recognition import RecognitionCache
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv(variable)
    if not root or not fixtures:
        pytest.skip('Board regressions use private live captures')
    assets = Assets(Path(root))
    strict_cache, temporal_cache = RecognitionCache(), RecognitionCache()
    for path in sorted(Path(fixtures).glob(pattern)):
        frame = cv2.imread(str(path))
        strict = recognize(frame, assets, strict_cache)
        temporal = recognize(frame, assets, temporal_cache, True)
        assert temporal.signature() == strict.signature()


@pytest.mark.parametrize('temporal', [False, True])
def test_neighbour_herba_does_not_hide_messis(temporal):
    from aspects_overlay.recognition import RecognitionCache
    root, fixtures = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_OVERLAP_DIR')
    if not root or not fixtures:
        pytest.skip('Overlapping glyph regression uses private live captures')
    assets = Assets(Path(root))
    cache = RecognitionCache()
    files = sorted(Path(fixtures).glob('frame-*.png'))
    assert len(files) == 24
    expected = {(-3, 0): 'gula', (-3, 1): 'fames', (-3, 3): 'herba',
                (-2, -1): 'fames', (0, -3): 'arbor', (0, -2): 'herba',
                (0, 3): 'praecantatio', (1, -2): 'messis',
                (3, -3): 'fabrico', (3, 0): 'instrumentum'}
    signatures = set()
    for path in files:
        board = recognize(cv2.imread(str(path)), assets, cache, temporal)
        assert board.anchors == expected
        assert len(board.cells) == 33
        signatures.add(board.signature())
    assert len(signatures) == 1


def test_visible_neighbour_alone_cannot_create_a_glyph():
    from aspects_overlay.recognition import _uncovered_glyph
    root = os.getenv('ASPECTS_GAME_DIR')
    if not root:
        pytest.skip('Glyph validation uses locally installed game artwork')
    assets = Assets(Path(root))
    icons = {name: cv2.resize(icon, (48, 48)) for name, icon in assets.icons.items()}
    crop = np.full((54, 54, 3), (100, 165, 205), np.uint8)
    # An adjacent green glyph intrudes at the edge; the rest is blank paper.
    crop[:24, :8] = (5, 190, 5)
    excluded = np.zeros((54, 54), np.uint8)
    excluded[:30, :14] = 255
    assert _uncovered_glyph(crop, icons, excluded, 3) is None


def test_terminus_is_loaded_from_avaritia_misc_textures():
    root = os.getenv('ASPECTS_GAME_DIR')
    if not root:
        pytest.skip('Addon regression uses locally installed game artwork')
    assets = Assets(Path(root))
    assert 'terminus' in assets.icons
    assert assets.icons['terminus'].shape == (32, 32, 4)
    assert assets.recipes['terminus'] == ('lucrum', 'alienis')


def test_terminus_anchor_is_not_silently_missing_from_board():
    root, fixture = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_TERMINUS_IMAGE')
    if not root or not fixture:
        pytest.skip('Terminus regression uses a private live capture')
    assets = Assets(Path(root))
    board = recognize(cv2.imread(fixture), assets, temporal=True)
    assert len(board.cells) == 33 and len(board.anchors) == 17
    assert board.anchors[(0, 3)] == 'terminus'
    assert board.anchors[(0, -3)] == 'tempestas'
    assert board.anchors[(3, 0)] == 'volatus'


def test_unsupported_coloured_glyph_is_rejected_even_without_a_hex_border():
    root, fixture = os.getenv('ASPECTS_GAME_DIR'), os.getenv('ASPECTS_TERMINUS_IMAGE')
    if not root or not fixture:
        pytest.skip('Unknown aspect regression uses a private live capture')
    assets = Assets(Path(root))
    assets.icons.pop('terminus')
    with pytest.raises(RecognitionError, match=r'\(0, 3\)'):
        recognize(cv2.imread(fixture), assets, temporal=True)
