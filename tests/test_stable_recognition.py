from dataclasses import replace
import threading

import pytest

from aspects_overlay.recognition import Board, BoardChanged, RecognitionError, read_board


def board():
    return Board({(0, 0), (1, 0), (2, 0)}, {(0, 0): 'aer', (2, 0): 'terra'},
                 (100, 100), 3, (0, 0, 444, 444))


class Frames:
    def __init__(self, values):
        self.values = values
        self.calls = 0
        self.now = 0

    def capture(self):
        value = self.values[min(self.calls, len(self.values)-1)]
        self.calls += 1
        return value

    def recognize(self, frame, *_):
        if isinstance(frame, Exception):
            raise frame
        return frame

    def wait(self, seconds):
        self.now += seconds

    def read(self, **kwargs):
        return read_board(self.capture, None, recognizer=self.recognize,
                          wait=self.wait, clock=lambda: self.now, timeout=.6, **kwargs)


def test_initial_read_retries_noise_and_confirms_whole_board():
    b = board()
    frames = Frames([RecognitionError('sparkle'), b, b])
    assert frames.read()[0] is b
    assert frames.calls == 3


def test_known_confident_board_does_not_add_a_delay():
    b = board()
    frames = Frames([b])
    assert frames.read(expected=b)[0] is b
    assert frames.calls == 1 and frames.now == 0


def test_tentative_glyph_needs_consecutive_confirmation_even_on_known_board():
    b = board()
    weak = replace(b, tentative={(0, 0)})
    frames = Frames([weak, RecognitionError('covered'), weak, weak])
    assert frames.read(expected=b)[0] is weak
    assert frames.calls == 4


def test_temporarily_missing_anchor_does_not_abort_known_board():
    b = board()
    missing = replace(b, anchors={(0, 0): 'aer'})
    frames = Frames([missing, missing, b])
    assert frames.read(expected=b)[0] is b
    assert frames.calls == 3


def test_changed_note_never_satisfies_previous_plan():
    b = board()
    changed = replace(b, anchors={(0, 0): 'ordo', (2, 0): 'terra'})
    frames = Frames([changed])
    with pytest.raises(BoardChanged):
        frames.read(expected=b)
    assert frames.now == pytest.approx(.6)


def test_initial_read_does_not_merge_different_notes():
    a = board()
    b = replace(a, anchors={(0, 0): 'ordo', (2, 0): 'terra'})
    frames = Frames([a, b]*10)
    with pytest.raises(RecognitionError):
        frames.read()


def test_drop_waits_for_acknowledgement_without_another_mouse_action():
    before = board()
    after = replace(before, anchors={**before.anchors, (1, 0): 'victus'})
    frames = Frames([before, before, before, after])
    assert frames.read(expected=after, previous=before)[0] is after
    assert frames.calls == 4


def test_permanent_obstruction_has_bounded_retries():
    frames = Frames([RecognitionError('tooltip')])
    with pytest.raises(RecognitionError, match='tooltip'):
        frames.read()
    assert frames.now == pytest.approx(.6)
    assert frames.calls <= 7


def test_cancellation_during_retry_stops_before_next_capture():
    frames = Frames([RecognitionError('animation')])
    cancel = threading.Event()
    def check():
        if cancel.is_set():
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        frames.read(check=check, on_retry=lambda _: cancel.set())
    assert frames.calls == 1
