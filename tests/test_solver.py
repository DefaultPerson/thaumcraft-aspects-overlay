"""Small board examples with independently known minimum solutions."""

import pytest

from aspects_overlay.solver import solve, validate_solution


RECIPES = {"a": (), "b": ("a",), "c": ("b",), "d": ()}


def checked(cells, anchors, recipes=RECIPES, inventory=None):
    answer = solve(cells, anchors, recipes, inventory)
    assert answer.status == "optimal", answer.message
    assert validate_solution(cells, anchors, recipes, answer.placements, inventory)[0]
    assert answer.elapsed_s >= 0
    assert set(answer.placements).isdisjoint(anchors)
    return answer.placements


def test_direct_components_work_in_both_directions():
    cells = {(0, 0), (1, 0)}
    assert checked(cells, {(0, 0): "a", (1, 0): "b"}) == {}
    assert checked(cells, {(0, 0): "b", (1, 0): "a"}) == {}


@pytest.mark.parametrize("right", ["a", "c", "d"])
def test_identical_transitive_and_unrelated_aspects_do_not_conduct(right):
    answer = solve({(0, 0), (1, 0)}, {(0, 0): "a", (1, 0): right}, RECIPES)
    assert answer.status == "infeasible"
    assert not validate_solution(
        {(0, 0), (1, 0)}, {(0, 0): "a", (1, 0): right}, RECIPES, {},
    )[0]


def test_incompatible_neighbors_are_allowed_when_another_path_connects_them():
    # The two a anchors touch, but both can connect through the third hex b.
    cells = {(0, 0), (1, 0), (0, 1)}
    anchors = {(0, 0): "a", (1, 0): "a"}
    assert checked(cells, anchors) == {(0, 1): "b"}


def test_hole_forces_a_detour():
    cells = {(0, 0), (0, 1), (1, 1), (2, 0)}
    anchors = {(0, 0): "a", (2, 0): "b"}
    # (1, 0) is a hole, so two added cells are necessary and sufficient.
    assert checked(cells, anchors) == {(0, 1): "b", (1, 1): "a"}
    assert not validate_solution(cells, anchors, RECIPES, {(1, 0): "b"})[0]


def test_three_terminals_share_one_bridge():
    anchors = {(1, 0): "a", (0, -1): "a", (-1, 1): "a"}
    cells = set(anchors) | {(0, 0)}
    assert checked(cells, anchors, inventory={"b": 1}) == {(0, 0): "b"}


def test_disconnected_board_is_proven_infeasible():
    answer = solve({(0, 0), (2, 0)}, {(0, 0): "a", (2, 0): "b"}, RECIPES)
    assert answer.status == "infeasible"
    assert answer.placements == {}


def test_inventory_counts_repeated_insertions_and_omitted_stock_is_zero():
    cells = {(q, 0) for q in range(5)}
    anchors = {(0, 0): "a", (4, 0): "a"}
    stock = {"a": 1, "b": 2}
    assert checked(cells, anchors, inventory=stock) == {
        (1, 0): "b", (2, 0): "a", (3, 0): "b",
    }
    for insufficient in ({"a": 1, "b": 1}, {"b": 2}, {}):
        assert solve(cells, anchors, RECIPES, insufficient).status == "infeasible"
    assert not validate_solution(cells, anchors, RECIPES, {
        (1, 0): "b", (2, 0): "a", (3, 0): "b",
    }, {"a": 1, "b": 1})[0]


def test_anchors_are_free_and_one_anchor_needs_no_insertions():
    assert checked({(0, 0)}, {(0, 0): "a"}, inventory={}) == {}
    assert checked({(0, 0), (1, 0)}, {(0, 0): "a", (1, 0): "b"}, inventory={}) == {}


def test_zero_budget_does_not_claim_infeasibility():
    cells = {(0, 0), (1, 0), (2, 0)}
    answer = solve(cells, {(0, 0): "a", (2, 0): "a"}, RECIPES, timeout_s=0)
    assert answer.status == "unknown"
    assert answer.placements == {}


def test_stable_input_order_produces_the_same_solution():
    cells = {(q, r) for q in range(3) for r in range(2)}
    anchors = {(0, 0): "a", (2, 1): "a"}
    first = checked(cells, anchors)
    second = checked(set(reversed(sorted(cells))), dict(reversed(list(anchors.items()))),
                     dict(reversed(list(RECIPES.items()))))
    assert first == second


@pytest.mark.parametrize("change", [
    {"anchors": {}},
    {"anchors": {(9, 9): "a"}},
    {"anchors": {(0, 0): "unknown"}},
    {"recipes": {"a": ("unknown",)}},
    {"inventory": {"unknown": 1}},
    {"inventory": {"a": -1}},
    {"inventory": {"a": 1.5}},
    {"inventory": {"a": True}},
    {"cells": {(0.5, 0)}},
    {"timeout_s": -1},
    {"timeout_s": float("nan")},
    {"timeout_s": float("inf")},
])
def test_invalid_input_is_reported(change):
    arguments = {"cells": {(0, 0)}, "anchors": {(0, 0): "a"}, "recipes": RECIPES}
    arguments.update(change)
    answer = solve(**arguments)
    assert answer.status == "invalid"
    assert answer.message


@pytest.mark.parametrize("placements", [
    {(0, 0): "b"},  # Anchors cannot be overwritten, even with their own aspect.
    {(1, 0): "unknown"},
    {(8, 0): "b"},
    {},  # Both anchors still need a bridge.
])
def test_validator_rejects_illegal_or_disconnected_manual_solutions(placements):
    cells = {(0, 0), (1, 0), (2, 0)}
    valid, reason = validate_solution(cells, {(0, 0): "a", (2, 0): "a"}, RECIPES, placements)
    assert not valid
    assert reason
