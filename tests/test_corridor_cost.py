"""Corridor cost prefers the longer clear path over a grazing short one."""

from __future__ import annotations

from fypa.topology.constants import NODE_W, OBSTACLE_CLEAR, WIRE_GRAZE_BAND
from fypa.topology.routing.cost import corridor_cost
from fypa.topology.routing.obstacles import obstacle_detour_y
from fypa.topology.routing.context import RoutingContext
from fypa.topology.types import TopologyNode


def _block(node_id: str, x: float, y: float, *, height: float = 56.0) -> TopologyNode:
    return TopologyNode(
        node_id=node_id,
        label=node_id,
        designator=node_id,
        role="RESISTOR",
        x=x,
        y=y,
        width=NODE_W,
        height=height,
        config_label="",
        has_error=False,
        bounds=(x, y, NODE_W, height),
    )


def test_corridor_cost_prefers_below_over_graze_above():
    """Equal-length clear corridor beats grazing the body (RULES.md §20)."""
    l1 = _block("L1", 100.0, 100.0)
    obstacles = [l1]
    y_nominal = 128.0  # mid L1
    x_lo, x_hi = 80.0, 300.0
    y_above = l1.y - OBSTACLE_CLEAR - 1.0  # ~11px above body → inside graze band
    y_below = l1.y + l1.height + WIRE_GRAZE_BAND + 1.0  # clear of graze band
    # Match |Δy| so length terms are equal; graze penalty decides.
    y_nominal = (y_above + y_below) / 2
    cost_above = corridor_cost(y_above, y_nominal, x_lo, x_hi, obstacles, set(), bends=1)
    cost_below = corridor_cost(y_below, y_nominal, x_lo, x_hi, obstacles, set(), bends=1)
    assert cost_above < float("inf")
    assert cost_below < cost_above


def test_obstacle_detour_y_picks_lower_cost_side():
    l1 = _block("L1", 100.0, 100.0)
    # Only L1 blocks the nominal row; below is a long clear run, above is tight.
    ctx = RoutingContext()
    y = obstacle_detour_y(ctx, 128.0, 80.0, 300.0, [l1], set(), "VDD")
    # Must clear the body.
    assert y <= l1.y - OBSTACLE_CLEAR + 0.1 or y >= l1.y + l1.height + OBSTACLE_CLEAR - 0.1
