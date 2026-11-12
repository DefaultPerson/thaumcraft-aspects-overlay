from types import MethodType, SimpleNamespace
import threading

import pytest

from aspects_overlay import app
from aspects_overlay.recognition import Board


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class Worker:
    def __init__(self, *args):
        self.running = False
        self.cancel = threading.Event()
        for name in ['completed', 'already_completed', 'failed', 'finished', 'progress']:
            setattr(self, name, Signal())

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running


@pytest.fixture
def overlay(monkeypatch):
    monkeypatch.setattr(app, 'Worker', Worker)
    ui = SimpleNamespace(
        target=None, assets=None, budget=8, inventory=None, output_dir=None,
        inventory_known=False, worker=None, apply_worker=None, pending_solve=False,
        pending_apply=False, auto_apply=False, board=None, result=None,
        logical_size=(1920, 1200), update=lambda: None, escape_states=[], applied=[],
        pointer=SimpleNamespace(interactive=False, transition=None),
    )
    ui.keys = SimpleNamespace(escape=ui.escape_states.append)
    for name in ['start_auto', 'start_solve', 'resume_solve', 'resume_auto', 'solved',
                 'research_done', 'failed', 'stop_apply', 'applying', 'recognition_progress']:
        setattr(ui, name, MethodType(getattr(app.Overlay, name), ui))
    def apply():
        # Matches the real entry point's guard against a running solver.
        assert not ui.worker.isRunning()
        ui.applied.append(ui.result)
    ui.apply_or_stop = apply
    return ui


def finish(ui):
    board = Board({(0, 0)}, {}, (0, 0), 3, (0, 0, 444, 444))
    result = SimpleNamespace(placements={(0, 0): 'aer'}, status='optimal', elapsed_s=.1)
    ui.worker.completed.emit((board, result, None))
    assert not ui.applied  # completed precedes QThread.finished
    ui.worker.running = False
    ui.worker.finished.emit()
    return result


def test_one_click_recalculates_and_applies_after_worker_exits(overlay):
    overlay.board = overlay.result = object()  # An old scheme must not be reused.
    overlay.start_auto()
    assert overlay.board is None and overlay.result is None
    result = finish(overlay)
    overlay.resume_auto()  # A second notification must not duplicate placement.
    assert overlay.applied == [result]


def test_manual_calculation_does_not_place_aspects(overlay):
    overlay.start_solve()
    finish(overlay)
    assert not overlay.applied


def test_stop_during_calculation_cancels_automatic_continuation(overlay):
    overlay.start_auto()
    overlay.stop_apply()
    assert overlay.worker.cancel.is_set()
    finish(overlay)
    assert not overlay.applied
    assert overlay.escape_states[-1] is False


@pytest.mark.parametrize('outcome', ['failed', 'already_completed'])
def test_failed_or_completed_note_cannot_leave_auto_placement_armed(overlay, outcome):
    overlay.start_auto()
    getattr(overlay.worker, outcome).emit(*(['bad capture'] if outcome == 'failed' else []))
    overlay.worker.running = False
    overlay.worker.finished.emit()
    assert not overlay.auto_apply and not overlay.applied
    assert overlay.escape_states[-1] is False
    overlay.start_solve()
    finish(overlay)
    assert not overlay.applied


def test_panel_focus_handoff_preserves_one_click_request(overlay):
    callbacks = []
    overlay.pointer.interactive = True
    overlay.pointer.return_to_game = lambda callback, point: callbacks.append(callback)
    overlay.start_auto()
    assert overlay.pending_solve and overlay.worker is None
    overlay.pointer.interactive = False
    callbacks.pop()()
    result = finish(overlay)
    assert overlay.applied == [result]


def test_cancelled_worker_does_not_capture_or_solve(monkeypatch, tmp_path):
    monkeypatch.setattr(app, 'capture', lambda _: pytest.fail('Captured after cancellation'))
    worker = app.Worker(None, None, 8, None, tmp_path, False)
    failures = []
    worker.failed.connect(failures.append)
    worker.cancel.set()
    worker.run()
    assert failures == ['Остановлено пользователем.']
    assert not (tmp_path / 'last-solution.json').exists()


def test_one_bad_watch_frame_does_not_discard_manual_scheme(overlay, monkeypatch):
    overlay.board = Board({(0, 0)}, {}, (0, 0), 3, (0, 0, 444, 444))
    overlay.note_mismatches = 0
    monkeypatch.setattr(app, 'capture', lambda _: None)
    def obscured(_):
        raise app.RecognitionError('tooltip')
    monkeypatch.setattr(app, 'find_parchment', obscured)
    app.Overlay.check_note(overlay)
    assert overlay.board is not None
    app.Overlay.check_note(overlay)
    assert overlay.board is None
