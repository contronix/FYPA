"""Corridor cost for shortest-legal routing (RULES.md rules 20–22)."""

from __future__ import annotations

import math

from fypa.topology.constants import (
    CORRIDOR_BEND_PENALTY,
    CORRIDOR_CROSSING_PENALTY,
    CORRIDOR_GRAZE_PENALTY,
    CORRIDOR_PARALLEL_SAME_NET_PENALTY,
    CORRIDOR_REUSE_BAND_BONUS,
    MIN_PARALLEL_GAP,
    OBSTACLE_CLEAR,
    WIRE_EPS,
    WIRE_GRAZE_BAND,
)
from fypa.topology.geometry import horizontal_crosses_node
from fypa.topology.types import TopologyNode

# Avoid circular import at module load; RoutingContext only for typing/runtime.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fypa.topology.routing.context import RoutingContext


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


def _foreign_crossing_count(
    y: float,
    x_lo: float,
    x_hi: float,
    ctx: RoutingContext,
    net: str,
) -> int:
    """Count foreign reserved verticals that a horizontal at ``y`` would cross."""
    lo, hi = min(x_lo, x_hi), max(x_lo, x_hi)
    n = 0
    for vx, vy_lo, vy_hi, vnet in ctx.vertical_bands:
        if vnet == net:
            continue
        if vx <= lo + WIRE_EPS or vx >= hi - WIRE_EPS:
            continue
        if vy_lo - WIRE_EPS <= y <= vy_hi + WIRE_EPS:
            n += 1
    return n


def _same_net_h_band_adjustment(
    y: float,
    x_lo: float,
    x_hi: float,
    ctx: RoutingContext,
    net: str,
) -> float:
    """Bonus for reusing a same-net H band; penalty for a near-parallel twin."""
    lo, hi = min(x_lo, x_hi), max(x_lo, x_hi)
    adjust = 0.0
    for by, blo, bhi, bnet in ctx.horizontal_bands:
        if bnet != net:
            continue
        # Span overlap required for either bonus or parallel penalty.
        if hi <= blo + WIRE_EPS or lo >= bhi - WIRE_EPS:
            continue
        gap = abs(by - y)
        if gap <= WIRE_EPS:
            adjust -= CORRIDOR_REUSE_BAND_BONUS
        elif gap < MIN_PARALLEL_GAP - WIRE_EPS:
            adjust += CORRIDOR_PARALLEL_SAME_NET_PENALTY
    return adjust


def corridor_cost(
    y: float,
    y_nominal: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
    *,
    bends: int = 0,
    ctx: RoutingContext | None = None,
    net: str | None = None,
) -> float:
    """Cost of a horizontal corridor at ``y`` between ``x_lo`` and ``x_hi``.

    Through-body → ``inf``. Otherwise length + bend penalty + graze soft-cost
    when closer than ``WIRE_GRAZE_BAND`` to a body, plus optional crossing /
    same-net parallel soft terms when ``ctx`` and ``net`` are set.
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
    if ctx is not None and net is not None:
        cost += (
            _foreign_crossing_count(y, lo, hi, ctx, net) * CORRIDOR_CROSSING_PENALTY
        )
        cost += _same_net_h_band_adjustment(y, lo, hi, ctx, net)
    return cost


def path_length(points: list[tuple[float, float]]) -> float:
    """Polyline length of consecutive points."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        total += abs(x2 - x1) + abs(y2 - y1)
    return total


def manhattan(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
