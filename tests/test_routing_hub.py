"""Routing regressions: multi-row hub connectivity and degenerate pairs.

These exercise wire-level routing paths that the SVG snapshot fixtures do not
cover (no committed fixture produces a multi-row hub whose trunk sits beside
its rows, nor two coincident ports on one net).
"""

from __future__ import annotations

from fypa.topology.geometry import parse_wire_path
from fypa.topology.routing.context import RoutingContext
from fypa.topology.routing.hub import _route_hub_tap, hub_row_edge_x, route_hub
from fypa.topology.routing.paths import hub_row_stub_columns
from fypa.topology.routing.paths import two_port_path
from fypa.topology.types import TopologyPort


def _port(node_id: str, y: float, wire_x: float) -> TopologyPort:
    """A right-side hub port whose stub column is pinned at ``wire_x``."""
    return TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=0.0,
        y=y,
        node_id=node_id,
        wire_x=wire_x,
    )


def _max_x(path_d: str) -> float:
    return max(x for x, _y in parse_wire_path(path_d))


def _xs_monotonic(path_d: str) -> bool:
    xs = [x for x, _y in parse_wire_path(path_d)]
    non_decr = all(b >= a - 1e-6 for a, b in zip(xs, xs[1:]))
    non_incr = all(b <= a + 1e-6 for a, b in zip(xs, xs[1:]))
    return non_decr or non_incr


def _row_ports_for_wire(
    ports: list[TopologyPort],
    row_wire,
) -> list[TopologyPort]:
    """Ports that define stub columns for a ``hub_row`` wire."""
    ids = {row_wire.src_node, row_wire.dst_node}
    matched = [p for p in ports if p.node_id in ids]
    if len(matched) >= 2:
        return matched
    row_y = parse_wire_path(row_wire.path_d)[0][1]
    return [p for p in ports if abs(p.y - row_y) < 1e-6]


def _row_feed_reaches_trunk(
    wires: list,
    row_wire,
    bus_x: float,
    *,
    row_ports: list[TopologyPort],
) -> bool:
    """Row path or a row-edge hub tap feed reaches the trunk column."""
    if _max_x(row_wire.path_d) >= bus_x - 1e-6:
        return True
    row_y = parse_wire_path(row_wire.path_d)[0][1]
    row_lo, row_hi = hub_row_stub_columns(row_ports)
    edge_x = hub_row_edge_x(row_lo, row_hi, bus_x)
    for wire in wires:
        if wire.routing_kind != "hub_tap" or wire.net != row_wire.net:
            continue
        wpts = parse_wire_path(wire.path_d)
        if len(wpts) < 2:
            continue
        if abs(wpts[0][1] - row_y) > 1e-6:
            continue
        if abs(wpts[0][0] - edge_x) > 1e-6:
            continue
        if _max_x(wire.path_d) >= bus_x - 1e-6:
            return True
    return False


def test_every_hub_row_reaches_trunk_when_bus_sits_beside_rows():
    """Each row bus must reach the trunk column, not just the last one.

    Regression for the edge-tap extending ``row_wires[-1]`` (always the last
    appended row) instead of the row currently being processed, which left
    earlier rows electrically orphaned from the trunk.
    """
    bus_x = 200.0
    ports = [
        _port("A", y=100.0, wire_x=50.0),
        _port("B", y=100.0, wire_x=100.0),
        _port("C", y=200.0, wire_x=60.0),
        _port("D", y=200.0, wire_x=120.0),
    ]
    wires = route_hub("VDD", ports, bus_x, obstacles=[], ctx=RoutingContext())

    row_wires = [w for w in wires if w.routing_kind == "hub_row"]
    assert len(row_wires) == 2, "expected one bus per row"
    for w in row_wires:
        assert _row_feed_reaches_trunk(
            wires,
            w,
            bus_x,
            row_ports=_row_ports_for_wire(ports, w),
        ), (
            f"row bus {w.path_d!r} stops before the trunk at x={bus_x}"
        )


def test_two_port_path_uses_stub_when_port_to_bus_horizontal_blocked():
    """Blocked port→bus horizontals must not cut through foreign symbol bodies."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.routing.obstacles import horizontal_segment_clear
    from fypa.topology.types import TopologyNode

    blocker = TopologyNode(
        node_id="U2",
        label="U2",
        designator="U2",
        role="SINK",
        x=150.0,
        y=80.0,
        width=NODE_W,
        height=80.0,
        config_label="",
        has_error=False,
        bounds=(150.0, 80.0, NODE_W, 80.0),
        ports=[],
    )
    start = TopologyPort(
        terminal="P",
        net="SIG",
        label="SIG",
        side="right",
        x=100.0 + NODE_W,
        y=100.0,
        node_id="U1",
        wire_x=180.0,
    )
    end = TopologyPort(
        terminal="P",
        net="SIG",
        label="SIG",
        side="left",
        x=400.0,
        y=200.0,
        node_id="U3",
        wire_x=380.0,
    )
    bus_x = 220.0
    path = two_port_path(
        start,
        end,
        bus_x=bus_x,
        net="SIG",
        obstacles=[blocker],
        ctx=RoutingContext(),
    )
    pts = parse_wire_path(path)
    assert pts[0] == (start.x, start.y)
    assert not horizontal_segment_clear(
        start.y,
        min(start.x, bus_x),
        max(start.x, bus_x),
        [blocker],
        {start.node_id, end.node_id},
    )
    assert pts[1][0] == 180.0, f"expected stub escape before bus column, got {path!r}"


def test_two_port_path_reserves_actual_vertical_column():
    """Vertical reservations must match the column the path actually uses."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.routing.obstacles import horizontal_segment_clear
    from fypa.topology.types import TopologyNode

    blocker = TopologyNode(
        node_id="U2",
        label="U2",
        designator="U2",
        role="SINK",
        x=150.0,
        y=80.0,
        width=NODE_W,
        height=80.0,
        config_label="",
        has_error=False,
        bounds=(150.0, 80.0, NODE_W, 80.0),
        ports=[],
    )
    start = TopologyPort(
        terminal="P",
        net="SIG",
        label="SIG",
        side="right",
        x=100.0 + NODE_W,
        y=100.0,
        node_id="U1",
        wire_x=180.0,
    )
    end = TopologyPort(
        terminal="P",
        net="SIG",
        label="SIG",
        side="left",
        x=400.0,
        y=200.0,
        node_id="U3",
        wire_x=380.0,
    )
    bus_x = 220.0
    ctx = RoutingContext()
    path = two_port_path(
        start,
        end,
        bus_x=bus_x,
        net="SIG",
        obstacles=[blocker],
        ctx=ctx,
    )
    from fypa.topology.geometry import path_to_segments

    vertical_xs = {
        round(seg.x1, 1)
        for seg in path_to_segments("SIG", parse_wire_path(path))
        if seg.orient == "V"
    }
    reserved_xs = {round(vx, 1) for vx, _lo, _hi, _net in ctx.vertical_bands}
    assert vertical_xs <= reserved_xs, (
        f"path verticals {vertical_xs} not covered by reservations {reserved_xs}: {path!r}"
    )
    assert not horizontal_segment_clear(
        start.y,
        min(start.x, bus_x),
        max(start.x, bus_x),
        [blocker],
        {start.node_id, end.node_id},
    )
    assert 180.0 in vertical_xs or 180.0 in reserved_xs


def test_two_port_same_row_detour_returns_to_destination_port():
    """Regression: when both ports share a row but an obstacle forces a detour,
    the wire must drop back to the port row and end on the destination port
    (previously it terminated on the detour row, leaving the port open)."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.types import TopologyNode

    # Obstacle body straddles the shared row (y=100) between the two stub columns.
    blocker = TopologyNode(
        node_id="OBS",
        label="OBS",
        designator="OBS",
        role="SINK",
        x=200.0,
        y=80.0,
        width=NODE_W,
        height=40.0,
        config_label="",
        has_error=False,
        bounds=(200.0, 80.0, NODE_W, 40.0),
        ports=[],
    )
    start = TopologyPort(
        terminal="P", net="SIG", label="SIG", side="right",
        x=100.0, y=100.0, node_id="U1",
    )
    end = TopologyPort(
        terminal="P", net="SIG", label="SIG", side="left",
        x=400.0, y=100.0, node_id="U3",
    )
    path = two_port_path(
        start, end, bus_x=250.0, net="SIG", obstacles=[blocker], ctx=RoutingContext()
    )
    pts = parse_wire_path(path)
    assert pts[0] == (start.x, start.y)
    # The branch under test only guards the bug if a detour actually happened.
    assert any(abs(y - start.y) > 1.0 for _x, y in pts), (
        f"expected an off-row detour, got {path!r}"
    )
    # The wire must terminate exactly on the destination port.
    assert pts[-1] == (end.x, end.y), f"path does not reach the port: {path!r}"


def test_hub_row_feed_fail_closed_when_all_candidates_blocked():
    """When every corridor is blocked, leave the row unattached (fail-closed)."""
    from fypa.topology.routing.hub import (
        _HubRowPlan,
        _connect_row_to_bus,
        _hub_wires_connect_ports,
        route_hub,
    )

    port = _port("U1", y=100.0, wire_x=100.0)
    plan = _HubRowPlan(
        group=[port],
        y_row=100.0,
        span_lo=100.0,
        span_hi=200.0,
        row_lo=100.0,
        row_hi=200.0,
        detoured=False,
    )
    bus_x = 400.0
    ctx = RoutingContext()
    ctx.reserve_horizontal(100.0, 150.0, 450.0, "OTHER")
    ctx.reserve_vertical(200.0, -1000.0, 1000.0, "OTHER")

    trunk_y, bus_leg = _connect_row_to_bus(plan, bus_x, ctx, "VDD", [])

    assert trunk_y is None
    assert bus_leg is None

    # Full hub with a disconnected feed must emit no wires (not a partial draw).
    other = _port("U2", y=300.0, wire_x=500.0)
    wires = route_hub("VDD", [port, other], bus_x, [], RoutingContext())
    if wires:
        assert _hub_wires_connect_ports([port, other], wires)


def test_hub_eastward_tap_uses_upstream_vertical_before_bus():
    """Downstream singletons branch from an existing tap vertical when possible."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.types import TopologyNode

    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=300.0,
        y=350.0,
        width=NODE_W,
        height=80.0,
        config_label="",
        has_error=False,
        bounds=(300.0, 350.0, NODE_W, 80.0),
        ports=[],
    )
    bus_x = 680.0
    ports = [
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=164.0,
            y=381.0,
            node_id="J3",
            wire_x=184.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=381.0,
            node_id="U3",
            wire_x=244.0,
        ),
        TopologyPort(
            terminal="IN_P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=75.0,
            node_id="U4",
            wire_x=244.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=732.0,
            y=279.0,
            node_id="U1",
            wire_x=712.0,
        ),
    ]

    def _node(node_id: str, y: float) -> TopologyNode:
        return TopologyNode(
            node_id=node_id,
            label=node_id,
            designator=node_id,
            role="SINK",
            x=264.0,
            y=y,
            width=NODE_W,
            height=80.0,
            config_label="",
            has_error=False,
            bounds=(264.0, y, NODE_W, 80.0),
            ports=[],
        )

    wires = route_hub(
        "VDD",
        ports,
        bus_x,
        obstacles=[blocker, _node("U4", 75.0), _node("U1", 279.0)],
        ctx=RoutingContext(),
    )
    u1_tap = next(w for w in wires if w.src_node == "U1")
    u4_tap = next(w for w in wires if w.src_node == "U4")
    feed_x = parse_wire_path(u4_tap.path_d)[-1][0]
    assert parse_wire_path(u1_tap.path_d)[0][0] == feed_x, u1_tap.path_d
    assert feed_x < bus_x - 1e-6
    assert not any(w.routing_kind == "hub" for w in wires)


def test_hub_row_bus_feed_detour_avoids_row_member_bodies():
    """Detoured row feeds must clear every symbol body, including row members."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.geometry import horizontal_crosses_node
    from fypa.topology.types import TopologyNode

    regulator = TopologyNode(
        node_id="U4",
        label="U4",
        designator="U4",
        role="SINK",
        x=264.0,
        y=222.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(264.0, 222.0, NODE_W, 74.0),
        ports=[],
    )
    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=300.0,
        y=250.0,
        width=NODE_W,
        height=40.0,
        config_label="",
        has_error=False,
        bounds=(300.0, 250.0, NODE_W, 40.0),
        ports=[],
    )
    bus_x = 456.0
    ports = [
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=164.0,
            y=261.0,
            node_id="J3",
            wire_x=184.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=261.0,
            node_id="U4",
            wire_x=244.0,
        ),
    ]
    wires = route_hub(
        "VDD",
        ports,
        bus_x,
        obstacles=[regulator, blocker],
        ctx=RoutingContext(),
    )
    for wire in wires:
        if wire.routing_kind != "hub_tap":
            continue
        from fypa.topology.geometry import path_to_segments

        for seg in path_to_segments("VDD", parse_wire_path(wire.path_d)):
            if seg.orient != "H":
                continue
            y = seg.y1
            x_lo, x_hi = min(seg.x1, seg.x2), max(seg.x1, seg.x2)
            assert not horizontal_crosses_node(regulator, y, x_lo, x_hi), wire.path_d


def test_hub_row_feed_detours_when_row_horizontal_blocked():
    """Row-to-trunk feed uses obstacle detour Y when the row span is blocked."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.types import TopologyNode

    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=300.0,
        y=350.0,
        width=NODE_W,
        height=80.0,
        config_label="",
        has_error=False,
        bounds=(300.0, 350.0, NODE_W, 80.0),
        ports=[],
    )
    bus_x = 680.0
    ports = [
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=164.0,
            y=381.0,
            node_id="J3",
            wire_x=184.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=381.0,
            node_id="U3",
            wire_x=244.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=732.0,
            y=279.0,
            node_id="U1",
            wire_x=712.0,
        ),
    ]
    wires = route_hub(
        "VDD",
        ports,
        bus_x,
        obstacles=[blocker],
        ctx=RoutingContext(),
    )
    row_wire = next(w for w in wires if w.routing_kind == "hub_row")
    assert _row_feed_reaches_trunk(
        wires,
        row_wire,
        bus_x,
        row_ports=[p for p in ports if p.node_id in {row_wire.src_node, row_wire.dst_node}],
    ), [w.path_d for w in wires]


def test_hub_row_stub_columns_ignores_symbol_edge_extension():
    """Stub span follows port stubs, not the optional right-symbol ``H`` in the path."""
    from fypa.topology.routing.paths import hub_row_path, hub_row_stub_columns

    group = [
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=164.0,
            y=261.0,
            node_id="J3",
            wire_x=184.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=261.0,
            node_id="U4",
            wire_x=244.0,
        ),
    ]
    row_lo, row_hi = hub_row_stub_columns(group)
    assert (row_lo, row_hi) == (184.0, 244.0)
    path_d = hub_row_path(group, 261.0)
    assert path_d == "M 164.0,261.0 H 184.0 H 244.0 H 264.0"


def test_hub_row_feed_detour_reserves_vertical_column():
    """Detoured row-to-trunk feeds must reserve the vertical column they use."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.geometry import path_to_segments
    from fypa.topology.types import TopologyNode

    regulator = TopologyNode(
        node_id="U4",
        label="U4",
        designator="U4",
        role="SINK",
        x=264.0,
        y=222.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(264.0, 222.0, NODE_W, 74.0),
        ports=[],
    )
    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=300.0,
        y=250.0,
        width=NODE_W,
        height=40.0,
        config_label="",
        has_error=False,
        bounds=(300.0, 250.0, NODE_W, 40.0),
        ports=[],
    )
    bus_x = 456.0
    ports = [
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=164.0,
            y=261.0,
            node_id="J3",
            wire_x=184.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=261.0,
            node_id="U4",
            wire_x=244.0,
        ),
    ]
    ctx = RoutingContext()
    wires = route_hub(
        "VDD",
        ports,
        bus_x,
        obstacles=[regulator, blocker],
        ctx=ctx,
    )
    feed = next(
        w
        for w in wires
        if w.routing_kind == "hub_tap" and " V " in w.path_d and _max_x(w.path_d) >= bus_x - 1e-6
    )
    vertical_xs = {
        round(seg.x1, 1)
        for seg in path_to_segments("VDD", parse_wire_path(feed.path_d))
        if seg.orient == "V"
    }
    reserved_xs = {round(vx, 1) for vx, _lo, _hi, _net in ctx.vertical_bands}
    assert vertical_xs <= reserved_xs, (
        f"feed verticals {vertical_xs} not covered by reservations {reserved_xs}: {feed.path_d!r}"
    )


def test_hub_east_taps_establish_band_for_west_feed():
    """Sink-side H at one Y is reused by a west feed instead of a twin corridor."""
    from fypa.topology.geometry import parse_wire_path
    from fypa.topology.routing.hub import route_hub
    from fypa.topology.types import TopologyNode, TopologyPort

    bus_x = 500.0
    driver = TopologyPort(
        terminal="OUT_P",
        net="VDD",
        label="VDD",
        side="right",
        x=100.0,
        y=180.0,
        node_id="U1",
        wire_x=120.0,
    )
    sink_a = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=700.0,
        y=100.0,
        node_id="J1",
        wire_x=680.0,
    )
    sink_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=700.0,
        y=279.0,
        node_id="J2",
        wire_x=680.0,
    )
    ctx = RoutingContext()
    # Block the driver row so the west feed must detour near sink_b's y.
    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="RESISTOR",
        x=200.0,
        y=160.0,
        width=200.0,
        height=40.0,
        config_label="",
        has_error=False,
        bounds=(200.0, 160.0, 200.0, 40.0),
    )
    wires = route_hub("VDD", [driver, sink_a, sink_b], bus_x, [blocker], ctx)
    assert wires
    long_h_ys = []
    for w in wires:
        pts = parse_wire_path(w.path_d)
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if abs(y0 - y1) < 0.5 and abs(x1 - x0) > 200:
                long_h_ys.append(round(y0, 1))
    # Near-parallel long runs of the same net must not both appear.
    for i, ya in enumerate(long_h_ys):
        for yb in long_h_ys[i + 1 :]:
            assert abs(ya - yb) < 0.5 or abs(ya - yb) >= 15.5, (long_h_ys, wires)


def test_connect_row_to_bus_ignores_row_drop_vertical():
    """A singleton drop onto the row must not skip the row→bus feed.

    Regression: upstream tap reserved a same-net vertical inside the row span,
    ``_row_meets_net_vertical`` treated that as trunk attachment, and VDD_48V
    left the bus column disconnected (hub_net_unrouted).
    """
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus

    port_a = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=164.0,
        y=177.0,
        node_id="J3",
        wire_x=184.0,
    )
    port_b = TopologyPort(
        terminal="IN_P",
        net="VDD",
        label="VDD",
        side="left",
        x=264.0,
        y=177.0,
        node_id="U4",
        wire_x=244.0,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=177.0,
        span_lo=164.0,
        span_hi=264.0,
        row_lo=184.0,
        row_hi=244.0,
        detoured=False,
    )
    ctx = RoutingContext()
    # Upstream singleton already dropped onto the row at the east stub column.
    ctx.reserve_vertical(244.0, 75.0, 177.0, "VDD")
    # Planned hub bus vertical (collision reservation) must not skip the feed.
    ctx.reserve_vertical(748.0, 75.0, 585.0, "VDD")
    trunk_y, feed = _connect_row_to_bus(plan, 748.0, ctx, "VDD", [])
    assert trunk_y == 177.0
    assert feed is not None
    assert "H 748.0" in feed


def test_connect_row_to_bus_retries_alternate_detour_y(monkeypatch):
    """When the first detour Y fails, try the next candidate."""
    from fypa.topology.constants import NODE_W, WIRE_EPS
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus
    from fypa.topology.routing.obstacles import horizontal_segment_clear
    from fypa.topology.types import TopologyNode

    port_a = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=164.0,
        y=261.0,
        node_id="J3",
        wire_x=184.0,
    )
    port_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=264.0,
        y=261.0,
        node_id="U4",
        wire_x=244.0,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=261.0,
        span_lo=164.0,
        span_hi=264.0,
        row_lo=184.0,
        row_hi=244.0,
        detoured=False,
    )
    regulator = TopologyNode(
        node_id="U4",
        label="U4",
        designator="U4",
        role="SINK",
        x=264.0,
        y=222.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(264.0, 222.0, NODE_W, 74.0),
        ports=[],
    )
    ctx = RoutingContext()
    blocked_ys = {212.0}

    def selective_clear(y, x_lo, x_hi, obstacles, skip):
        if abs(y - 261.0) < WIRE_EPS:
            return False
        if any(abs(y - blocked) < WIRE_EPS for blocked in blocked_ys):
            return False
        return horizontal_segment_clear(y, x_lo, x_hi, obstacles, skip)

    monkeypatch.setattr(
        "fypa.topology.routing.hub.horizontal_segment_clear",
        selective_clear,
    )
    monkeypatch.setattr(
        "fypa.topology.routing.hub.obstacle_detour_y_candidates",
        lambda *_a, **_k: [261.0, 212.0, 180.0],
    )

    trunk_y, path_d = _connect_row_to_bus(plan, 456.0, ctx, "VDD", [regulator])
    assert path_d is not None
    assert trunk_y == 180.0
    assert " V 180.0 " in f" {path_d} "


def test_hub_tap_detour_skips_foreign_gnd_stub_column(monkeypatch):
    """Y-detour must not drop a vertical on a foreign GND trunk at the stub x.

    Left-port stubs often coincide with the column GND trunk; a detour vertical
    on that column yields duplicate_vertical_x (project_b_hub_vdd regression).
    """
    from fypa.topology.constants import GND_NET, PORT_WIRE_STUB
    from fypa.topology.placement import port_stub_x
    from fypa.topology.routing.context import RoutingContext
    from fypa.topology.routing.paths import hub_tap_path
    from fypa.topology.geometry import parse_wire_path

    port = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=504.0,
        y=177.0,
        node_id="U2",
    )
    stub = port_stub_x(port)
    assert abs(stub - (504.0 - PORT_WIRE_STUB)) < 0.1
    bus_x = 708.0
    ctx = RoutingContext()
    # GND trunk shares the stub column and overlaps the detour Y span.
    ctx.reserve_vertical(stub, 195.0, 408.0, GND_NET)

    monkeypatch.setattr(
        "fypa.topology.routing.paths.obstacle_detour_y",
        lambda *_a, **_k: 222.0,
    )
    path, _ = hub_tap_path(port, bus_x=bus_x, obstacles=[], ctx=ctx, net="VDD")
    assert path, "expected escape detour, not fail-closed empty path"
    # No vertical segment may sit on the GND trunk column.
    pts = parse_wire_path(path)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if abs(x0 - x1) < 0.5 and abs(y0 - y1) > 0.5:
            assert abs(x0 - stub) > 0.5, path
    assert f"H {bus_x:.1f}" in path or path.rstrip().endswith(f"{bus_x:.1f}")


def test_two_port_path_dest_avoids_gnd_stub_vertical():
    """Dest approach must not drop a vertical on a foreign GND trunk at the stub.

    Two-port gutter routes previously did ``H stub V port_y`` after a Y-detour,
    which overlays the column GND trunk and puts the bend against the symbol.
    """
    from fypa.topology.constants import GND_NET, PORT_WIRE_STUB
    from fypa.topology.placement import port_stub_x
    from fypa.topology.routing.context import RoutingContext
    from fypa.topology.geometry import parse_wire_path, path_to_segments

    start = TopologyPort(
        terminal="N",
        net="VDD",
        label="VDD",
        side="right",
        x=100.0,
        y=50.0,
        node_id="L1",
    )
    end = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=400.0,
        y=300.0,
        node_id="U3",
    )
    stub = port_stub_x(end)
    assert abs(stub - (400.0 - PORT_WIRE_STUB)) < 0.1
    bus_x = 160.0
    ctx = RoutingContext()
    ctx.reserve_vertical(stub, 40.0, 500.0, GND_NET)

    path = two_port_path(
        start, end, bus_x=bus_x, net="VDD", obstacles=[], ctx=ctx
    )
    assert path, path
    for seg in path_to_segments("VDD", parse_wire_path(path)):
        if seg.orient == "V":
            assert abs(seg.x1 - stub) > 0.5, path


def test_two_port_path_dest_fail_closed_when_no_clear_drop(monkeypatch):
    """When bus and outward escapes are blocked, emit no stub-on-GND vertical."""
    from fypa.topology.constants import GND_NET, MIN_PARALLEL_GAP, PORT_WIRE_STUB
    from fypa.topology.placement import port_stub_x
    from fypa.topology.routing.context import RoutingContext

    start = TopologyPort(
        terminal="N",
        net="VDD",
        label="VDD",
        side="right",
        x=100.0,
        y=50.0,
        node_id="L1",
    )
    end = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=400.0,
        y=300.0,
        node_id="U3",
    )
    stub = port_stub_x(end)
    assert abs(stub - (400.0 - PORT_WIRE_STUB)) < 0.1
    bus_x = 160.0
    ctx = RoutingContext()
    # Occupy stub and every outward escape column across the dest Y span.
    for step in range(0, 10):
        ctx.reserve_vertical(
            stub - MIN_PARALLEL_GAP * step, 40.0, 500.0, GND_NET
        )
    # Also block the bus column so the preferred feed drop cannot land there.
    ctx.reserve_vertical(bus_x, 40.0, 500.0, GND_NET)
    bands_before = len(ctx.vertical_bands)

    def _detour(_ctx, y_nominal, *_a, **_k):
        # Force a dest-row detour so entry must pick a drop column.
        if abs(y_nominal - end.y) < 1.0:
            return 250.0
        return y_nominal

    monkeypatch.setattr(
        "fypa.topology.routing.paths.obstacle_detour_y",
        _detour,
    )

    path = two_port_path(
        start, end, bus_x=bus_x, net="VDD", obstacles=[], ctx=ctx
    )
    assert path == "", path
    assert len(ctx.vertical_bands) == bands_before, (
        "fail-closed dest must roll back phantom bus reservations"
    )


def test_pair_routing_skips_empty_fail_closed_paths(monkeypatch):
    """Gutter pair emission must not keep ghost wires when routing fail-closes."""
    from fypa.topology.routing.pair import signal_wires_from_pairs

    a = TopologyPort(
        terminal="N",
        net="VDD",
        label="VDD",
        side="right",
        x=100.0,
        y=50.0,
        node_id="L1",
    )
    b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=400.0,
        y=300.0,
        node_id="U3",
    )
    ctx = RoutingContext()
    ctx.reserve_vertical(160.0, 50.0, 300.0, "VDD")
    monkeypatch.setattr(
        "fypa.topology.routing.pair.two_port_wire_path",
        lambda *_a, **_k: "",
    )
    monkeypatch.setattr(
        "fypa.topology.routing.pair._bus_x_for_pair",
        lambda *_a, **_k: 160.0,
    )
    wires = signal_wires_from_pairs([(a, b)], obstacles=[], ctx=ctx)
    assert wires == []
    assert not any(
        abs(vx - 160.0) < 0.5 and vnet == "VDD" for vx, _lo, _hi, vnet in ctx.vertical_bands
    )


def test_hub_tap_from_bus_detour_skips_gnd_stub_column(monkeypatch):
    """Y-detour from the bus must not place a vertical on a GND stub column."""
    from fypa.topology.constants import GND_NET, PORT_WIRE_STUB
    from fypa.topology.placement import port_stub_x
    from fypa.topology.routing.context import RoutingContext
    from fypa.topology.routing.paths import hub_tap_path_from_bus
    from fypa.topology.geometry import parse_wire_path
    from fypa.topology.types import TopologyNode

    port = TopologyPort(
        terminal="IN_P",
        net="VDD",
        label="VDD",
        side="left",
        x=400.0,
        y=200.0,
        node_id="U2",
    )
    stub = port_stub_x(port)
    assert abs(stub - (400.0 - PORT_WIRE_STUB)) < 0.1
    bus_x = 200.0
    # Block only the mid-gutter on the port row so a clear outward escape
    # remains between the obstacle and the stub (stub itself is GND).
    blocker = TopologyNode(
        node_id="U_block",
        label="U_block",
        designator="U_block",
        role="SINK",
        x=220.0,
        y=190.0,
        width=80.0,
        height=40.0,
        config_label="",
        has_error=False,
        bounds=(220.0, 190.0, 80.0, 40.0),
        ports=[],
    )
    ctx = RoutingContext()
    ctx.reserve_vertical(stub, 50.0, 400.0, GND_NET)

    monkeypatch.setattr(
        "fypa.topology.routing.paths.obstacle_detour_y",
        lambda *_a, **_k: 120.0,
    )
    path, _ = hub_tap_path_from_bus(
        bus_x, port, obstacles=[blocker], ctx=ctx, net="VDD"
    )
    assert path, path
    pts = parse_wire_path(path)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if abs(x0 - x1) < 0.5 and abs(y0 - y1) > 0.5:
            assert abs(x0 - stub) > 0.5, path
    assert abs(pts[0][0] - bus_x) < 0.5 and abs(pts[0][1] - 120.0) < 0.5, path


def test_hub_tap_from_bus_prefers_feed_column_drop():
    """From-bus attach uses the trunk at port y when that corridor is clear."""
    from fypa.topology.constants import GND_NET, PORT_WIRE_STUB
    from fypa.topology.placement import port_stub_x
    from fypa.topology.routing.context import RoutingContext
    from fypa.topology.routing.paths import hub_tap_path_from_bus
    from fypa.topology.geometry import parse_wire_path

    port = TopologyPort(
        terminal="IN_P",
        net="VDD",
        label="VDD",
        side="left",
        x=200.0,
        y=100.0,
        node_id="U2",
    )
    stub = port_stub_x(port)
    assert abs(stub - (200.0 - PORT_WIRE_STUB)) < 0.1
    bus_x = 120.0
    ctx = RoutingContext()
    ctx.reserve_vertical(stub, 50.0, 400.0, GND_NET)

    path, _ = hub_tap_path_from_bus(bus_x, port, obstacles=[], ctx=ctx, net="VDD")
    assert path, path
    pts = parse_wire_path(path)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if abs(x0 - x1) < 0.5 and abs(y0 - y1) > 0.5:
            assert abs(x0 - stub) > 0.5, path
    assert abs(pts[0][0] - bus_x) < 0.5 and abs(pts[0][1] - 100.0) < 0.5, path


def test_connect_row_to_bus_skips_foreign_vertical_column():
    """Detour vertical must not share a column with a foreign reserved vertical."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus
    from fypa.topology.types import TopologyNode

    port_a = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=164.0,
        y=261.0,
        node_id="J3",
        wire_x=184.0,
    )
    port_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=264.0,
        y=261.0,
        node_id="U4",
        wire_x=244.0,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=261.0,
        span_lo=164.0,
        span_hi=264.0,
        row_lo=184.0,
        row_hi=244.0,
        detoured=False,
    )
    regulator = TopologyNode(
        node_id="U4",
        label="U4",
        designator="U4",
        role="SINK",
        x=264.0,
        y=222.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(264.0, 222.0, NODE_W, 74.0),
        ports=[],
    )
    ctx = RoutingContext()
    ctx.reserve_vertical(244.0, 200.0, 280.0, "SIG")
    trunk_y, path_d = _connect_row_to_bus(plan, 456.0, ctx, "VDD", [regulator])
    assert path_d is not None
    assert trunk_y is not None
    assert abs(trunk_y - 212.0) > 1e-6, path_d


def test_connect_row_to_bus_records_trunk_y_when_row_already_meets_bus():
    """When a same-net row H already reaches the trunk, record trunk Y without feed."""
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus

    port_a = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=164.0,
        y=261.0,
        node_id="J3",
        wire_x=184.0,
    )
    port_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=264.0,
        y=261.0,
        node_id="U4",
        wire_x=244.0,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=261.0,
        span_lo=164.0,
        span_hi=264.0,
        row_lo=184.0,
        row_hi=244.0,
        detoured=False,
    )
    ctx = RoutingContext()
    bus_x = 456.0
    ctx.reserve_horizontal(plan.y_row, plan.span_lo, bus_x, "VDD")
    trunk_y, feed = _connect_row_to_bus(plan, bus_x, ctx, "VDD", [])
    assert trunk_y == plan.y_row
    assert feed is None


def test_connect_row_to_bus_escapes_gnd_pinch_outward():
    """When GND pins the row edge and a body fills toward-bus, drop away first.

    Geometry mirrors a gutter hub whose east-side stub sits within
    ``MIN_PARALLEL_GAP`` of a GND trunk while the SERIES body occupies every
    toward-bus column; a foreign same-row H also blocks a flat feed. The feed
    must step outward (away from the bus) then detour onto the trunk.
    """
    from fypa.topology.constants import NODE_W
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus
    from fypa.topology.types import TopologyNode

    edge_x = 552.0
    bus_x = 1024.0
    y_row = 585.0
    port_a = TopologyPort(
        terminal="OUT_P",
        net="VDD",
        label="VDD",
        side="right",
        x=392.0,
        y=y_row,
        node_id="U1",
        wire_x=412.0,
    )
    port_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=572.0,
        y=y_row,
        node_id="L1",
        wire_x=edge_x,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=y_row,
        span_lo=392.0,
        span_hi=572.0,
        row_lo=412.0,
        row_hi=edge_x,
        detoured=False,
    )
    series = TopologyNode(
        node_id="L1",
        label="L1",
        designator="L1",
        role="SERIES",
        x=572.0,
        y=546.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(572.0, 546.0, NODE_W, 74.0),
        ports=[],
    )
    ctx = RoutingContext()
    # GND trunk in the stub gutter — blocks vertical at edge_x (gap < MIN_PARALLEL_GAP).
    ctx.reserve_vertical(560.0, 400.0, 900.0, "GND")
    # Foreign same-row H across the toward-bus corridor (blocks flat feed).
    ctx.reserve_horizontal(y_row, 700.0, 1168.0, "VDD_MCU")
    trunk_y, path_d = _connect_row_to_bus(plan, bus_x, ctx, "VDD", [series])
    assert path_d is not None, "expected outward-escape feed"
    assert trunk_y is not None
    assert abs(trunk_y - y_row) > 1e-6, path_d
    assert "H 536.0" in path_d, path_d
    assert f"H {bus_x:.1f}" in path_d
    from fypa.topology.types import TopologyModel, TopologyWire
    from fypa.topology.validate.rules import check_right_to_left_wires

    u1 = TopologyNode(
        node_id="U1",
        label="U1",
        designator="U1",
        role="REGULATOR",
        x=392.0,
        y=546.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(392.0, 546.0, NODE_W, 74.0),
        ports=[port_a],
    )
    l1 = TopologyNode(
        node_id="L1",
        label="L1",
        designator="L1",
        role="SERIES",
        x=572.0,
        y=546.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(572.0, 546.0, NODE_W, 74.0),
        ports=[port_b],
    )
    model = TopologyModel(
        nodes=[u1, l1],
        wires=[TopologyWire(net="VDD", path_d=path_d)],
    )
    assert check_right_to_left_wires(model) == []


def test_connect_row_to_bus_fail_closed_when_rtl_escape_blocked():
    """When the only RTL-legal away column is blocked, fail-closed — no deep RTL."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus
    from fypa.topology.types import TopologyNode

    edge_x = 552.0
    bus_x = 1024.0
    y_row = 585.0
    port_a = TopologyPort(
        terminal="OUT_P",
        net="VDD",
        label="VDD",
        side="right",
        x=392.0,
        y=y_row,
        node_id="U1",
        wire_x=412.0,
    )
    port_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=572.0,
        y=y_row,
        node_id="L1",
        wire_x=edge_x,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=y_row,
        span_lo=392.0,
        span_hi=572.0,
        row_lo=412.0,
        row_hi=edge_x,
        detoured=False,
    )
    series = TopologyNode(
        node_id="L1",
        label="L1",
        designator="L1",
        role="SERIES",
        x=572.0,
        y=546.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(572.0, 546.0, NODE_W, 74.0),
        ports=[],
    )
    ctx = RoutingContext()
    ctx.reserve_vertical(560.0, 400.0, 900.0, "GND")
    ctx.reserve_horizontal(y_row, 700.0, 1168.0, "VDD_MCU")
    ctx.reserve_vertical(536.0, 400.0, 900.0, "SIG")
    trunk_y, path_d = _connect_row_to_bus(plan, bus_x, ctx, "VDD", [series])
    assert trunk_y is None
    assert path_d is None


def test_connect_row_to_bus_rejects_rtl_detoured_bus_leg():
    """Detoured row→bus feeds must not run the final H right-to-left."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus
    from fypa.topology.types import TopologyNode

    edge_x = 552.0
    bus_x = 360.0
    y_row = 585.0
    port_a = TopologyPort(
        terminal="OUT_P",
        net="VDD",
        label="VDD",
        side="right",
        x=392.0,
        y=y_row,
        node_id="U1",
        wire_x=412.0,
    )
    port_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=572.0,
        y=y_row,
        node_id="L1",
        wire_x=edge_x,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=y_row,
        span_lo=392.0,
        span_hi=572.0,
        row_lo=412.0,
        row_hi=edge_x,
        detoured=False,
    )
    series = TopologyNode(
        node_id="L1",
        label="L1",
        designator="L1",
        role="SERIES",
        x=572.0,
        y=546.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(572.0, 546.0, NODE_W, 74.0),
        ports=[],
    )
    ctx = RoutingContext()
    trunk_y, path_d = _connect_row_to_bus(plan, bus_x, ctx, "VDD", [series])
    assert trunk_y is None
    assert path_d is None


def test_connect_row_to_bus_no_westward_escape_from_right_face_edge():
    """Westward away columns apply only at a left-face stub on the feed edge."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.routing.hub import _HubRowPlan, _connect_row_to_bus
    from fypa.topology.types import TopologyNode

    edge_x = 412.0
    bus_x = 1024.0
    y_row = 585.0
    port_a = TopologyPort(
        terminal="OUT_P",
        net="VDD",
        label="VDD",
        side="right",
        x=392.0,
        y=y_row,
        node_id="U1",
        wire_x=edge_x,
    )
    port_b = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="left",
        x=572.0,
        y=y_row,
        node_id="L1",
        wire_x=552.0,
    )
    plan = _HubRowPlan(
        group=[port_a, port_b],
        y_row=y_row,
        span_lo=392.0,
        span_hi=572.0,
        row_lo=edge_x,
        row_hi=552.0,
        detoured=False,
    )
    driver = TopologyNode(
        node_id="U1",
        label="U1",
        designator="U1",
        role="REGULATOR",
        x=392.0,
        y=546.0,
        width=NODE_W,
        height=74.0,
        config_label="",
        has_error=False,
        bounds=(392.0, 546.0, NODE_W, 74.0),
        ports=[],
    )
    ctx = RoutingContext()
    ctx.reserve_vertical(424.0, 400.0, 900.0, "GND")
    ctx.reserve_horizontal(y_row, 700.0, 1168.0, "VDD_MCU")
    trunk_y, path_d = _connect_row_to_bus(plan, bus_x, ctx, "VDD", [driver])
    assert path_d is None or "H 396.0" not in path_d


def test_detoured_hub_row_emits_row_bus_and_vertical_drops():
    """Detoured row plans still emit ``hub_row`` plus drops from port Y onto the bus."""
    from fypa.topology.types import TopologyNode

    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=250.0,
        y=248.0,
        width=20.0,
        height=30.0,
        config_label="",
        has_error=False,
        bounds=(250.0, 248.0, 20.0, 30.0),
        ports=[],
    )
    ports = [
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=164.0,
            y=261.0,
            node_id="J3",
            wire_x=184.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=261.0,
            node_id="U4",
            wire_x=244.0,
        ),
    ]
    wires = route_hub(
        "VDD",
        ports,
        456.0,
        obstacles=[blocker],
        ctx=RoutingContext(),
    )
    row_wire = next(w for w in wires if w.routing_kind == "hub_row")
    row_y = parse_wire_path(row_wire.path_d)[0][1]
    assert abs(row_y - 261.0) > 1e-6, row_wire.path_d
    drops = [
        w
        for w in wires
        if w.routing_kind == "hub_tap"
        and " V " in w.path_d
        and abs(parse_wire_path(w.path_d)[-1][1] - row_y) < 1e-6
    ]
    assert len(drops) == 2, [w.path_d for w in wires]


def test_detoured_row_drop_skips_foreign_vertical_column():
    """Detoured port drops must not use a stub column blocked by foreign verticals."""
    from fypa.topology.types import TopologyNode

    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=250.0,
        y=248.0,
        width=20.0,
        height=30.0,
        config_label="",
        has_error=False,
        bounds=(250.0, 248.0, 20.0, 30.0),
        ports=[],
    )
    ports = [
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="right",
            x=164.0,
            y=261.0,
            node_id="J3",
            wire_x=184.0,
        ),
        TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side="left",
            x=264.0,
            y=261.0,
            node_id="U4",
            wire_x=244.0,
        ),
    ]
    ctx = RoutingContext()
    ctx.reserve_vertical(244.0, 200.0, 280.0, "SIG")
    wires = route_hub(
        "VDD",
        ports,
        456.0,
        obstacles=[blocker],
        ctx=ctx,
    )
    u4_tap = next(w for w in wires if w.src_node == "U4")
    row_y = parse_wire_path(next(w for w in wires if w.routing_kind == "hub_row").path_d)[0][1]
    assert abs(row_y - 261.0) > 1e-6
    assert " H 228.0 " in f" {u4_tap.path_d} ", u4_tap.path_d
    assert u4_tap.path_d.endswith(f"V {row_y:.1f}")


def test_detoured_row_connector_merges_at_port_column():
    """Detoured rows expose member columns so connector sub-symbols can drop vertically."""
    from fypa.topology.constants import NODE_W
    from fypa.topology.types import TopologyNode

    def _connector_node(
        designator: str,
        node_id: str,
        x: float,
        y: float,
        wire_x: float,
    ) -> TopologyNode:
        return TopologyNode(
            node_id=node_id,
            label=designator,
            designator=designator,
            role="CONNECTOR",
            x=x,
            y=y,
            width=NODE_W,
            height=40.0,
            config_label="",
            has_error=False,
            bounds=(x, y, NODE_W, 40.0),
            ports=[
                TopologyPort(
                    terminal="P",
                    net="VDD",
                    label="VDD",
                    side="right",
                    x=x,
                    y=y + 20.0,
                    node_id=node_id,
                    wire_x=wire_x,
                ),
            ],
        )

    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=250.0,
        y=248.0,
        width=20.0,
        height=30.0,
        config_label="",
        has_error=False,
        bounds=(250.0, 248.0, 20.0, 30.0),
        ports=[],
    )
    j21 = _connector_node("J2.1", "J2.1", 164.0, 241.0, 184.0)
    j22 = _connector_node("J2.2", "J2.2", 264.0, 241.0, 244.0)
    j23 = _connector_node("J2.3", "J2.3", 164.0, 80.0, 184.0)
    ports = [j21.ports[0], j22.ports[0], j23.ports[0]]
    wires = route_hub(
        "VDD",
        ports,
        456.0,
        obstacles=[j21, j22, j23, blocker],
        ctx=RoutingContext(),
    )
    row_wire = next(w for w in wires if w.routing_kind == "hub_row")
    row_y = parse_wire_path(row_wire.path_d)[0][1]
    assert abs(row_y - 261.0) > 1e-6
    merge_tap = next(w for w in wires if w.src_node == "J2.3")
    assert merge_tap.path_d == f"M 164.0,100.0 V {row_y:.1f}"


def test_route_hub_tap_scans_later_row_spans_at_same_y():
    """Do not stop row-span search when the first same-Y span misses the stub column."""
    port = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=320.0,
        y=200.0,
        node_id="U5",
        wire_x=320.0,
    )
    row_spans = [
        (200.0, 140.0, 160.0, {140.0, 160.0}),
        (200.0, 300.0, 340.0, {300.0, 320.0, 340.0}),
    ]
    path_d, tap_y = _route_hub_tap(
        port,
        456.0,
        [],
        RoutingContext(),
        "VDD",
        row_spans,
        {},
    )
    assert path_d == ""
    assert tap_y == 200.0


def test_route_hub_tap_same_y_off_row_stub_needs_tap():
    """Same-Y ports off the stub span still need a tap even when inside the body span."""
    port = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=220.0,
        y=200.0,
        node_id="U5",
        wire_x=200.0,
    )
    row_spans = [
        (200.0, 130.0, 160.0, {140.0, 160.0}),
    ]
    path_d, tap_y = _route_hub_tap(
        port,
        456.0,
        [],
        RoutingContext(),
        "VDD",
        row_spans,
        {},
    )
    assert path_d != ""
    assert tap_y == 200.0
    assert path_d.startswith("M 220.0,200.0")


def test_route_hub_tap_same_y_stub_overlap_without_membership_needs_tap():
    """Same-Y singletons must not skip taps when only the stub column overlaps a row."""
    port = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=220.0,
        y=200.0,
        node_id="U5",
        wire_x=150.0,
    )
    row_spans = [
        (200.0, 130.0, 140.0, {120.0, 140.0}),
        (200.0, 150.0, 160.0, {164.0, 264.0}),
    ]
    path_d, tap_y = _route_hub_tap(
        port,
        456.0,
        [],
        RoutingContext(),
        "VDD",
        row_spans,
        {},
    )
    assert path_d != ""
    assert tap_y == 200.0


def test_route_hub_tap_tries_later_row_when_first_span_fails():
    """Keep scanning row spans when the first matching stub cannot drop onto its row."""
    from fypa.topology.types import TopologyNode

    blocker = TopologyNode(
        node_id="BLK",
        label="BLK",
        designator="BLK",
        role="SINK",
        x=100.0,
        y=215.0,
        width=300.0,
        height=20.0,
        config_label="",
        has_error=False,
        bounds=(100.0, 215.0, 300.0, 20.0),
        ports=[],
    )
    port = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=140.0,
        y=220.0,
        node_id="U1",
        wire_x=150.0,
    )
    ctx = RoutingContext()
    ctx.reserve_vertical(150.0, 200.0, 219.0, "SIG")
    row_spans = [
        (200.0, 130.0, 160.0, set()),
        (240.0, 130.0, 170.0, set()),
    ]
    path_d, tap_y = _route_hub_tap(
        port,
        456.0,
        [blocker],
        ctx,
        "VDD",
        row_spans,
        {},
    )
    assert tap_y == 240.0
    assert path_d.endswith("V 240.0")


def test_hub_tap_vertical_merge_at_port_reserves_column():
    from fypa.topology.routing.paths import hub_tap_vertical_to_row

    port = TopologyPort(
        terminal="P",
        net="VDD",
        label="VDD",
        side="right",
        x=164.0,
        y=100.0,
        node_id="J2.3",
        wire_x=184.0,
    )
    ctx = RoutingContext()
    hub_tap_vertical_to_row(
        port,
        261.0,
        merge_at_port=True,
        ctx=ctx,
        net="VDD",
    )
    assert ctx.vertical_bands == [(164.0, 100.0, 261.0, "VDD")]


def test_obstacle_detour_y_candidates_respects_wire_eps(monkeypatch):
    from fypa.topology.constants import WIRE_EPS
    from fypa.topology.routing import obstacles as obstacles_mod
    from fypa.topology.routing.obstacles import obstacle_detour_y_candidates

    monkeypatch.setattr(obstacles_mod, "obstacle_detour_y", lambda *_a, **_k: 262.5)
    monkeypatch.setattr(
        obstacles_mod,
        "_obstacle_detour_y_direction",
        lambda *_a, downward, **_k: 258.0 if downward else 261.8,
    )
    order = obstacle_detour_y_candidates(
        RoutingContext(),
        260.0,
        184.0,
        456.0,
        [],
        set(),
        "VDD",
    )
    # Cost-sorted; membership and WIRE_EPS uniqueness matter, not insert order.
    assert set(order) == {260.0, 262.5, 258.0, 261.8}
    assert all(
        sum(1 for y in order if abs(y - candidate) < WIRE_EPS) == 1 for candidate in order
    )


def test_coincident_ports_do_not_produce_a_double_back_wire():
    """Two ports at the same point route as a single stub, not a stub-back-stub.

    Regression for ``two_port_path`` emitting ``H 120 H 100 H 120 H 100`` for
    coincident endpoints — a self-overlapping wire that ``simplify_wire_path``
    cannot collapse.
    """
    p = TopologyPort(
        terminal="P", net="N", label="N", side="right",
        x=100.0, y=100.0, node_id="A", wire_x=120.0,
    )
    q = TopologyPort(
        terminal="N", net="N", label="N", side="right",
        x=100.0, y=100.0, node_id="B", wire_x=120.0,
    )
    path = two_port_path(p, q, bus_x=120.0, net="N")
    assert _xs_monotonic(path), f"coincident-port wire doubles back: {path!r}"
