"""Minimum-placement Thaumcraft 4 solver on an axial hex grid.

Only direct component/compound links conduct a connection. Empty cells and
incompatible touching aspects may coexist; the anchors need a compatible path.
The inventory, when supplied, is exhaustive: omitted aspects have zero stock.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import heapq
import math
import time
from typing import Literal


Coord = tuple[int, int]
Status = Literal["optimal", "feasible", "unknown", "infeasible", "invalid"]
NEIGHBORS: tuple[Coord, ...] = (
    (1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1),
)


@dataclass
class SolveResult:
    placements: dict[Coord, str] = field(default_factory=dict)
    status: Status = "unknown"
    elapsed_s: float = 0.0
    message: str = ""


def _neighbors(cell: Coord):
    q, r = cell
    for dq, dr in NEIGHBORS:
        yield q + dq, r + dr


def _coord(value: object) -> bool:
    return (isinstance(value, tuple) and len(value) == 2
            and all(type(part) is int for part in value))


def _input_error(cells, anchors, recipes, inventory) -> str:
    if not isinstance(cells, (set, frozenset)) or not all(map(_coord, cells)):
        return "Cells must be a set of integer axial coordinate pairs."
    if not isinstance(recipes, dict) or not recipes:
        return "Recipes must define all known aspects, including primal aspects."
    for aspect, components in recipes.items():
        if not isinstance(aspect, str) or not aspect:
            return "Aspect names must be nonempty strings."
        if not isinstance(components, (tuple, list)):
            return f"Components for {aspect!r} must be a sequence."
        if any(not isinstance(item, str) or item not in recipes for item in components):
            return f"Recipe for {aspect!r} references an unknown aspect."
    if not isinstance(anchors, dict) or not anchors:
        return "At least one fixed anchor is required."
    for cell, aspect in anchors.items():
        if not _coord(cell) or cell not in cells:
            return "Every anchor must occupy a usable cell."
        if not isinstance(aspect, str) or aspect not in recipes:
            return "An anchor references an unknown aspect."
    if inventory is not None:
        if not isinstance(inventory, dict):
            return "Inventory must map known aspects to nonnegative integer counts."
        for aspect, count in inventory.items():
            if aspect not in recipes:
                return "Inventory references an unknown aspect."
            if type(count) is not int or count < 0:
                return "Inventory counts must be nonnegative integers."
    return ""


def _compatible(left: str, right: str, recipes) -> bool:
    return left in recipes[right] or right in recipes[left]


def _connected(occupied: dict[Coord, str], root: Coord, recipes) -> set[Coord]:
    seen = {root}
    pending = deque([root])
    while pending:
        cell = pending.popleft()
        for neighbor in _neighbors(cell):
            if (neighbor not in seen and neighbor in occupied
                    and _compatible(occupied[cell], occupied[neighbor], recipes)):
                seen.add(neighbor)
                pending.append(neighbor)
    return seen


def _initial_solution(cells, anchors, recipes, inventory, deadline):
    """Try a deterministic shortest-path construction; never infer impossibility."""
    compatible = {aspect: sorted(other for other in recipes
                                if _compatible(aspect, other, recipes))
                  for aspect in sorted(recipes)}
    root = min(anchors)
    placements = {}
    while time.monotonic() < deadline:
        fixed = {**anchors, **placements}
        reached = _connected(fixed, root, recipes)
        if anchors.keys() <= reached:
            return placements
        stock_used = Counter(placements.values())
        distances = {(cell, fixed[cell]): 0 for cell in sorted(reached)}
        previous = {}
        queue = [(0, cell, fixed[cell]) for cell in sorted(reached)]
        heapq.heapify(queue)
        target = None
        while queue and time.monotonic() < deadline:
            cost, cell, aspect = heapq.heappop(queue)
            state = cell, aspect
            if distances[state] != cost:
                continue
            if cell in anchors and cell not in reached:
                target = state
                break
            for neighbor in _neighbors(cell):
                if neighbor not in cells:
                    continue
                choices = [fixed[neighbor]] if neighbor in fixed else compatible[aspect]
                for other in choices:
                    if other not in compatible[aspect]:
                        continue
                    if (neighbor not in fixed and inventory is not None
                            and stock_used[other] >= inventory.get(other, 0)):
                        continue
                    next_state = neighbor, other
                    next_cost = cost + (neighbor not in fixed)
                    if next_cost < distances.get(next_state, math.inf):
                        distances[next_state] = next_cost
                        previous[next_state] = state
                        heapq.heappush(queue, (next_cost, neighbor, other))
        if target is None:
            return None
        candidate = dict(placements)
        while target in previous:
            cell, aspect = target
            if cell not in anchors:
                if cell in candidate and candidate[cell] != aspect:
                    return None  # A product-graph walk reused a hex with another label.
                candidate[cell] = aspect
            target = previous[target]
        if inventory is not None and any(
            count > inventory.get(aspect, 0)
            for aspect, count in Counter(candidate.values()).items()
        ):
            return None
        placements = candidate
    return None


def validate_solution(
    cells: set[Coord],
    anchors: dict[Coord, str],
    recipes: dict[str, tuple[str, ...]],
    placements: dict[Coord, str],
    inventory: dict[str, int] | None = None,
) -> tuple[bool, str]:
    """Check placement legality and anchor connectivity without a solver.

    Returns ``(valid, reason)``. This checks feasibility, not minimality. Anchors
    are immutable and free; placements may not overlap them or use holes.
    """
    error = _input_error(cells, anchors, recipes, inventory)
    if error:
        return False, error
    if not isinstance(placements, dict):
        return False, "Placements must map coordinates to aspects."
    for cell, aspect in placements.items():
        if not _coord(cell) or cell not in cells:
            return False, "A placement is outside the usable cells."
        if cell in anchors:
            return False, "A placement overlaps a fixed anchor."
        if not isinstance(aspect, str) or aspect not in recipes:
            return False, "A placement references an unknown aspect."
    if inventory is not None:
        if any(count > inventory.get(aspect, 0)
               for aspect, count in Counter(placements.values()).items()):
            return False, "Placements exceed the available inventory."
    occupied = {**anchors, **placements}
    reachable = _connected(occupied, min(anchors), recipes)
    if not anchors.keys() <= reachable:
        return False, "The anchors are not connected by direct component links."
    return True, "All anchors are connected and placements are legal."


def _spatial_lower_bound(cells, anchors, deadline):
    """Exact node-weighted Steiner cost without aspect constraints.

    Every valid labeled solution is a spatial connection, so this relaxation
    provides a lower bound. Subset dynamic programming joins terminal groups
    at each cell, then Dijkstra propagates each joined tree through the grid.
    """
    terminals = sorted(anchors)
    if len(terminals) > 7:
        return 0  # Bound work on large notes, without inventing a proof.
    weights = {cell: int(cell not in anchors) for cell in cells}
    dp = {}
    infinity = len(cells) + 1
    for mask in range(1, 1 << len(terminals)):
        if time.monotonic() >= deadline:
            return 0
        distance = {cell: infinity for cell in cells}
        if mask.bit_count() == 1:
            distance[terminals[mask.bit_length() - 1]] = 0
        subset = (mask - 1) & mask
        while subset:
            other = mask ^ subset
            if other and subset < other:
                for cell in cells:
                    distance[cell] = min(distance[cell],
                                         dp[subset][cell] + dp[other][cell] - weights[cell])
            subset = (subset - 1) & mask
        queue = [(cost, cell) for cell, cost in distance.items()]
        heapq.heapify(queue)
        while queue:
            cost, cell = heapq.heappop(queue)
            if cost != distance[cell]:
                continue
            for neighbor in _neighbors(cell):
                if neighbor in cells and cost + weights[neighbor] < distance[neighbor]:
                    distance[neighbor] = cost + weights[neighbor]
                    heapq.heappush(queue, (distance[neighbor], neighbor))
        dp[mask] = distance
    return min(dp[(1 << len(terminals)) - 1].values())


def solve(
    cells: set[Coord],
    anchors: dict[Coord, str],
    recipes: dict[str, tuple[str, ...]],
    inventory: dict[str, int] | None = None,
    timeout_s: float = 8.0,
) -> SolveResult:
    """Minimize inserted cells, using a single deterministic CP-SAT worker.

    ``optimal`` includes a proof of minimality; ``feasible`` contains a valid
    solution without that proof; ``unknown`` has no solution and is never an
    infeasibility claim. Ordering and seed are fixed; a wall-clock cutoff can
    still affect which incumbent has been found. The time budget includes model
    construction. No game state is read or changed.
    """
    started = time.monotonic()

    def result(status: Status, message: str, placements=None):
        return SolveResult(placements or {}, status, time.monotonic() - started, message)

    error = _input_error(cells, anchors, recipes, inventory)
    if error:
        return result("invalid", error)
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s) or timeout_s < 0):
        return result("invalid", "The timeout must be a finite nonnegative number.")
    root = min(anchors)
    if anchors.keys() <= _connected(anchors, root, recipes):
        return result("optimal", "Anchors already connected; no insertions are needed.")

    # Geometric reachability is an inexpensive proof even without OR-Tools.
    component = {root}
    pending = deque([root])
    while pending:
        for neighbor in _neighbors(pending.popleft()):
            if neighbor in cells and neighbor not in component:
                component.add(neighbor)
                pending.append(neighbor)
    if not anchors.keys() <= component:
        return result("infeasible", "Usable cells cannot connect all anchors.")
    if component <= anchors.keys() or (inventory is not None and not any(inventory.values())):
        return result("infeasible", "Disconnected anchors require insertions, but none are available.")
    if timeout_s == 0:
        return result("unknown", "Time budget expired before a solution was found.")
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return result("unknown", "OR-Tools is required to search for a solution.")

    ordered_cells = sorted(component)
    aspects = sorted(recipes)
    ids = {aspect: index + 1 for index, aspect in enumerate(aspects)}
    allowed = [aspect for aspect in aspects
               if inventory is None or inventory.get(aspect, 0) > 0]
    initial = _initial_solution(
        component, anchors, recipes, inventory,
        min(started + timeout_s, time.monotonic() + min(0.2, timeout_s / 10)),
    )
    if initial is not None and not validate_solution(cells, anchors, recipes, initial, inventory)[0]:
        initial = None
    lower_bound = _spatial_lower_bound(component, anchors, started + timeout_s)
    if initial is not None and len(initial) == lower_bound:
        return result("optimal", f"Minimum proven by spatial lower bound: {lower_bound} inserted cells.", initial)

    def unproven(message):
        if initial is not None:
            return result("feasible", "Valid connection found; minimality was not proven within the time budget.", initial)
        return result("unknown", message)

    pairs = sorted({pair for aspect, components in recipes.items()
                    for part in components
                    for pair in ((ids[aspect], ids[part]), (ids[part], ids[aspect]))})
    if not pairs:
        return result("infeasible", "No aspects have a direct component connection.")

    model = cp_model.CpModel()
    labels = {}
    occupied = {}
    free_cells = [cell for cell in ordered_cells if cell not in anchors]
    domain = cp_model.Domain.from_values([0, *(ids[aspect] for aspect in allowed)])
    for index, cell in enumerate(ordered_cells):
        if cell in anchors:
            labels[cell] = model.new_constant(ids[anchors[cell]])
        else:
            label = model.new_int_var_from_domain(domain, f"aspect_{index}")
            used = model.new_bool_var(f"occupied_{index}")
            model.add(label != 0).only_enforce_if(used)
            model.add(label == 0).only_enforce_if(used.Not())
            labels[cell] = label
            occupied[cell] = used
    if inventory is not None:
        for aspect in allowed:
            stock = inventory[aspect]
            if stock >= len(free_cells):
                continue
            usages = []
            for index, cell in enumerate(free_cells):
                use = model.new_bool_var(f"stock_{ids[aspect]}_{index}")
                model.add(labels[cell] == ids[aspect]).only_enforce_if(use)
                model.add(labels[cell] != ids[aspect]).only_enforce_if(use.Not())
                usages.append(use)
            model.add(sum(usages) <= stock)

    # Send one unit from the first anchor to each other anchor. A signed flow
    # on each physical edge is possible only for a compatible aspect pair.
    demand = len(anchors) - 1
    outgoing = {cell: [] for cell in ordered_cells}
    for cell in ordered_cells:
        for neighbor in _neighbors(cell):
            if neighbor not in component or neighbor <= cell:
                continue
            edge = model.new_bool_var(f"edge_{cell}_{neighbor}")
            model.add_allowed_assignments([labels[cell], labels[neighbor]], pairs).only_enforce_if(edge)
            flow = model.new_int_var(-demand, demand, f"flow_{cell}_{neighbor}")
            model.add(flow <= demand * edge)
            model.add(flow >= -demand * edge)
            outgoing[cell].append(flow)
            outgoing[neighbor].append(-flow)
    for cell in ordered_cells:
        balance = demand if cell == root else -1 if cell in anchors else 0
        model.add(sum(outgoing[cell]) == balance)
    model.add(sum(occupied.values()) >= lower_bound)
    model.minimize(sum(occupied.values()))
    if initial is not None:
        model.add(sum(occupied.values()) <= len(initial))
        for cell in free_cells:
            model.add_hint(labels[cell], ids[initial[cell]] if cell in initial else 0)
            model.add_hint(occupied[cell], int(cell in initial))

    remaining = timeout_s - (time.monotonic() - started)
    if remaining <= 0:
        return unproven("Time budget expired while constructing the model.")
    engine = cp_model.CpSolver()
    engine.parameters.num_search_workers = 1
    engine.parameters.random_seed = 0
    engine.parameters.max_time_in_seconds = remaining
    class BoundReached(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self):
            if round(self.objective_value) == lower_bound:
                self.stop_search()

    status = engine.solve(model, BoundReached())
    if status == cp_model.INFEASIBLE:
        if initial is not None:
            return result("invalid", "The exact model rejected an independently validated connection.")
        return result("infeasible", "No legal placement connects all anchors with this inventory.")
    if status == cp_model.MODEL_INVALID:
        return result("invalid", f"Solver model is invalid: {engine.solution_info()}")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return unproven("Time budget expired without a solution or infeasibility proof.")

    placements = {cell: aspects[engine.value(labels[cell]) - 1]
                  for cell in free_cells if engine.value(labels[cell]) != 0}
    # Remove any irrelevant component from an early incumbent.
    reachable = _connected({**anchors, **placements}, root, recipes)
    placements = {cell: aspect for cell, aspect in placements.items() if cell in reachable}
    valid, reason = validate_solution(cells, anchors, recipes, placements, inventory)
    if not valid:
        return result("invalid", f"Solver result failed independent validation: {reason}")
    if status == cp_model.OPTIMAL or len(placements) == lower_bound:
        return result("optimal", f"Minimum proven: {len(placements)} inserted cells.", placements)
    return result("feasible", "Valid connection found; minimality was not proven within the time budget.", placements)
