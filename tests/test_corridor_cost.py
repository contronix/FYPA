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
    """When both sides clear, pick the cheaper corridor (not nearest Δy alone)."""
    l1 = _block("L1", 100.0, 100.0, height=80.0)
    ctx = RoutingContext()
    y_nominal = 140.0  # inside body
    y = obstacle_detour_y(ctx, y_nominal, 80.0, 300.0, [l1], set(), "VDD")
    y_above = l1.y - OBSTACLE_CLEAR
    y_below = l1.y + l1.height + OBSTACLE_CLEAR
    cost_up = corridor_cost(y_above, y_nominal, 80.0, 300.0, [l1], set(), bends=1)
    cost_down = corridor_cost(y_below, y_nominal, 80.0, 300.0, [l1], set(), bends=1)
    expected = y_below if cost_down <= cost_up else y_above
    assert abs(y - expected) <= 1.0, (y, expected, cost_up, cost_down)


def test_hub_fail_closed_rolls_back_context_bands():
    """Discarded hub geometry must not leave phantom reservations for later nets."""
    from fypa.topology.routing.hub import route_hub
    from fypa.topology.types import TopologyPort

    def _p(nid: str, y: float, x: float) -> TopologyPort:
        return TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=x,
            y=y,
            node_id=nid,
            wire_x=x + 20.0,
        )

    ctx = RoutingContext()
    ctx.reserve_horizontal(100.0, 150.0, 450.0, "OTHER")
    ctx.reserve_vertical(200.0, -1000.0, 1000.0, "OTHER")
    wires = route_hub(
        "VDD",
        [_p("U1", 100.0, 0.0), _p("U2", 300.0, 0.0)],
        400.0,
        [],
        ctx,
    )
    vdd_h = [b for b in ctx.horizontal_bands if b[3] == "VDD"]
    vdd_v = [b for b in ctx.vertical_bands if b[3] == "VDD"]
    if not wires:
        assert not vdd_h and not vdd_v
    assert any(b[3] == "OTHER" for b in ctx.horizontal_bands)
