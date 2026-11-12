from itertools import combinations
import time

from aspects_overlay.solver import NEIGHBORS, _spatial_lower_bound


def brute_force_cost(cells, terminals):
    free = sorted(cells - terminals)
    for count in range(len(free) + 1):
        for subset in combinations(free, count):
            used = terminals | set(subset)
            reached = {min(terminals)}
            pending = list(reached)
            while pending:
                q, r = pending.pop()
                for dq, dr in NEIGHBORS:
                    cell = q+dq, r+dr
                    if cell in used and cell not in reached:
                        reached.add(cell)
                        pending.append(cell)
            if terminals <= reached:
                return count


def test_spatial_bound_matches_exhaustive_small_boards():
    cells = {(q, r) for q in range(3) for r in range(2)}
    for count in (2, 3, 4):
        for subset in combinations(sorted(cells), count):
            terminals = set(subset)
            bound = _spatial_lower_bound(cells, dict.fromkeys(terminals, "a"), time.monotonic()+10)
            assert bound == brute_force_cost(cells, terminals)


def test_actual_note_spatial_minimum_is_nine():
    cells = {(q, r) for q in range(-3, 4) for r in range(-3, 4)
             if max(abs(q), abs(r), abs(q+r)) <= 3}
    cells -= {(0, -3), (0, -1), (0, 2), (-1, 3)}
    anchors = {(-2, -1): "aer", (3, -3): "alienis", (-3, 3): "iter", (2, 1): "perditio"}
    assert _spatial_lower_bound(cells, anchors, time.monotonic()+10) == 9
