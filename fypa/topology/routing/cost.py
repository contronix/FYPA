"""Corridor cost for shortest-legal routing (RULES.md rule 20)."""

from __future__ import annotations

import math

from fypa.topology.constants import (
    CORRIDOR_BEND_PENALTY,
    CORRIDOR_GRAZE_PENALTY,
    OBSTACLE_CLEAR,
    WIRE_EPS,
    WIRE_GRAZE_BAND,
)
from fypa.topology.geometry import horizontal_crosses_node
from fypa.topology.types import TopologyNode


def _min_body_clearance(
    y: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
) -> float:
    """Minimum vertical distance from ``y`` to any obstacle body under the span."""
    lo, hi = min(x_lo, x_hi), max(x_lo, x_hi)
    best = float("inf")
    for node in obstacles:
        if node.node_id in skip:
            continue
        # Span must overlap the node in X (with a small pad).
        if hi < node.x - WIRE_EPS or lo > node.x + node.width + WIRE_EPS:
            continue
        top, bottom = node.y, node.y + node.height
        if top - WIRE_EPS <= y <= bottom + WIRE_EPS:
            return 0.0
        best = min(best, abs(y - top), abs(y - bottom))
    return best


def corridor_cost(
    y: float,
    y_nominal: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
    *,
    bends: int = 0,
) -> float:
    """Cost of a horizontal corridor at ``y`` between ``x_lo`` and ``x_hi``.

    Through-body → ``inf``. Otherwise length + bend penalty + graze soft-cost
    when closer than ``WIRE_GRAZE_BAND`` to a body.
    """
    lo, hi = min(x_lo, x_hi), max(x_lo, x_hi)
    for node in obstacles:
        if node.node_id in skip:
            continue
        if horizontal_crosses_node(node, y, lo, hi):
            return math.inf

    length = (hi - lo) + abs(y - y_nominal)
    cost = length + bends * CORRIDOR_BEND_PENALTY
    clear = _min_body_clearance(y, lo, hi, obstacles, skip)
    if clear < WIRE_GRAZE_BAND - WIRE_EPS:
        # Prefer corridors that keep a full graze band; still legal above
        # OBSTACLE_CLEAR (hard clearance is enforced by horizontal_crosses_node
        # plus detour helpers).
        if clear < OBSTACLE_CLEAR - WIRE_EPS:
            return math.inf
        cost += CORRIDOR_GRAZE_PENALTY * (WIRE_GRAZE_BAND - clear)
    return cost


def path_length(points: list[tuple[float, float]]) -> float:
    """Polyline length of consecutive points."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        total += abs(x2 - x1) + abs(y2 - y1)
    return total


def manhattan(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
