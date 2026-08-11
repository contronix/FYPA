"""Obstacle detours and segment clearance checks."""

from __future__ import annotations

from fypa.topology.constants import MIN_PARALLEL_GAP, OBSTACLE_CLEAR, WIRE_EPS
from fypa.topology.geometry import (
    horizontal_crosses_node,
    vertical_crosses_node,
)
from fypa.topology.routing.context import RoutingContext
from fypa.topology.types import TopologyNode


def detour_y_for_horizontal(
    y_nominal: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
) -> float | None:
    blocked_bottom = y_nominal
    any_block = False
    for node in obstacles:
        if node.node_id in skip:
            continue
        if horizontal_crosses_node(node, y_nominal, x_lo, x_hi):
            any_block = True
            blocked_bottom = max(blocked_bottom, node.y + node.height)
    if not any_block:
        return None
    return blocked_bottom + OBSTACLE_CLEAR


def detour_y_for_horizontal_upward(
    y_nominal: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
) -> float | None:
    """Return a Y above obstacles when a horizontal run at ``y_nominal`` is blocked."""
    blocked_top = y_nominal
    any_block = False
    for node in obstacles:
        if node.node_id in skip:
            continue
        if horizontal_crosses_node(node, y_nominal, x_lo, x_hi):
            any_block = True
            blocked_top = min(blocked_top, node.y)
    if not any_block:
        return None
    return blocked_top - OBSTACLE_CLEAR


def _obstacle_detour_y_direction(
    ctx: RoutingContext,
    y_nominal: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip_node_ids: set[str],
    net: str | None,
    *,
    downward: bool,
) -> float:
    lo, hi = min(x_lo, x_hi), max(x_lo, x_hi)
    detour_fn = detour_y_for_horizontal if downward else detour_y_for_horizontal_upward
    y = y_nominal
    detour = detour_fn(y_nominal, lo, hi, obstacles, skip_node_ids)
    if detour is not None:
        y = detour

    # Clearing an obstacle body can push the row back onto a foreign reserved
    # band, and clearing a band can push it back onto an obstacle. Alternate the
    # two until neither moves the row. Both nudges are monotonic in the detour
    # direction, so this converges within a bounded number of passes.
    for _ in range(len(ctx.horizontal_bands) + 2):
        y_start = y
        for _ in range(len(ctx.horizontal_bands) + 1):
            blocked = False
            for by, blo, bhi, bnet in ctx.horizontal_bands:
                if net is not None and bnet == net:
                    continue
                if hi <= blo + WIRE_EPS or lo >= bhi - WIRE_EPS:
                    continue
                if abs(by - y) < MIN_PARALLEL_GAP - WIRE_EPS:
                    y = by + MIN_PARALLEL_GAP if downward else by - MIN_PARALLEL_GAP
                    blocked = True
                    break
            if not blocked:
                break
        detour2 = detour_fn(y, lo, hi, obstacles, skip_node_ids)
        if detour2 is not None:
            if downward and detour2 > y + WIRE_EPS:
                y = detour2
            elif not downward and detour2 < y - WIRE_EPS:
                y = detour2
        if abs(y - y_start) < WIRE_EPS:
            break
    return y


def obstacle_detour_y(
    ctx: RoutingContext,
    y_nominal: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip_node_ids: set[str],
    net: str | None = None,
) -> float:
    """Return a Y that clears obstacles and reserved horizontal bands.

    Chooses the lower-cost of upward vs downward when both clear (RULES.md §20).
    """
    from fypa.topology.routing.cost import corridor_cost

    lo, hi = min(x_lo, x_hi), max(x_lo, x_hi)
    y_down = _obstacle_detour_y_direction(
        ctx,
        y_nominal,
        lo,
        hi,
        obstacles,
        skip_node_ids,
        net,
        downward=True,
    )
    y_up = _obstacle_detour_y_direction(
        ctx,
        y_nominal,
        lo,
        hi,
        obstacles,
        skip_node_ids,
        net,
        downward=False,
    )
    if abs(y_down - y_nominal) < WIRE_EPS:
        return y_down
    if abs(y_up - y_nominal) < WIRE_EPS:
        return y_up
    cost_down = corridor_cost(
        y_down, y_nominal, lo, hi, obstacles, skip_node_ids, bends=1
    )
    cost_up = corridor_cost(
        y_up, y_nominal, lo, hi, obstacles, skip_node_ids, bends=1
    )
    if cost_up < cost_down - WIRE_EPS:
        return y_up
    if cost_down < cost_up - WIRE_EPS:
        return y_down
    # Tie: prefer the smaller absolute detour.
    if abs(y_up - y_nominal) < abs(y_down - y_nominal) - WIRE_EPS:
        return y_up
    return y_down


def obstacle_detour_y_candidates(
    ctx: RoutingContext,
    y_nominal: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip_node_ids: set[str],
    net: str | None = None,
) -> list[float]:
    """Distinct Y values to try for a horizontal feed, best-cost first."""
    from fypa.topology.routing.cost import corridor_cost

    lo, hi = min(x_lo, x_hi), max(x_lo, x_hi)
    order: list[float] = []

    def add(y: float) -> None:
        if any(abs(y - existing) < WIRE_EPS for existing in order):
            return
        order.append(y)

    add(y_nominal)
    add(obstacle_detour_y(ctx, y_nominal, lo, hi, obstacles, skip_node_ids, net))
    add(
        _obstacle_detour_y_direction(
            ctx,
            y_nominal,
            lo,
            hi,
            obstacles,
            skip_node_ids,
            net,
            downward=True,
        )
    )
    add(
        _obstacle_detour_y_direction(
            ctx,
            y_nominal,
            lo,
            hi,
            obstacles,
            skip_node_ids,
            net,
            downward=False,
        )
    )
    order.sort(
        key=lambda y: (
            corridor_cost(
                y,
                y_nominal,
                lo,
                hi,
                obstacles,
                skip_node_ids,
                bends=0 if abs(y - y_nominal) < WIRE_EPS else 1,
            ),
            abs(y - y_nominal),
        )
    )
    return order


def horizontal_segment_clear(
    y: float,
    x_lo: float,
    x_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
) -> bool:
    for node in obstacles:
        if node.node_id in skip:
            continue
        if horizontal_crosses_node(node, y, x_lo, x_hi):
            return False
    return True


def trunk_vertical_clear(
    x: float,
    y_lo: float,
    y_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
) -> bool:
    lo, hi = min(y_lo, y_hi), max(y_lo, y_hi)
    for node in obstacles:
        if node.node_id in skip:
            continue
        if vertical_crosses_node(node, x, lo, hi):
            return False
    return True


def shift_x_clear_of_vertical_obstacles(
    x: float,
    y_lo: float,
    y_hi: float,
    obstacles: list[TopologyNode],
    skip: set[str],
    outward: float,
) -> float:
    """Shift ``x`` outward so a vertical run over ``[y_lo, y_hi]`` clears foreign nodes.

    Moving past one body can bring the column onto another further out, so
    re-scan until nothing crosses. Each shift is monotonic in ``outward``, so
    this converges within one pass per obstacle.
    """
    lo, hi = min(y_lo, y_hi), max(y_lo, y_hi)
    relevant = [n for n in obstacles if n.node_id not in skip]
    for _ in range(len(relevant) + 1):
        moved = False
        for node in relevant:
            if not vertical_crosses_node(node, x, lo, hi):
                continue
            nx, _ny, nw, _nh = node.bounds
            new_x = nx + nw + OBSTACLE_CLEAR if outward > 0 else nx - OBSTACLE_CLEAR
            if (outward > 0 and new_x > x + WIRE_EPS) or (
                outward < 0 and new_x < x - WIRE_EPS
            ):
                x = new_x
                moved = True
        if not moved:
            break
    return x


def foreign_vertical_covers_y(
    ctx: RoutingContext,
    x: float,
    y: float,
    net: str,
) -> bool:
    for vx, vy_lo, vy_hi, vnet in ctx.vertical_bands:
        if vnet == net or abs(vx - x) >= WIRE_EPS:
            continue
        if vy_lo - WIRE_EPS <= y <= vy_hi + WIRE_EPS:
            return True
    return False
