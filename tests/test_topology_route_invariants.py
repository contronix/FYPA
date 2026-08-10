"""Routing invariants: outward first leg, no through-own-body shortcuts."""

from __future__ import annotations

from fypa.topology.constants import NODE_W, PORT_WIRE_STUB
from fypa.topology.geometry import parse_wire_path, path_to_segments
from fypa.topology.routing.context import RoutingContext
from fypa.topology.routing.paths import (
    path_start_to_bus_x,
    two_port_path,
)
from fypa.topology.types import TopologyNode, TopologyPort
from fypa.topology.validate.segments import check_wires_through_foreign_nodes
from fypa.topology.types import TopologyModel, TopologyWire


def _port(
    *,
    node_id: str,
    side: str,
    x: float,
    y: float,
    net: str = "VDD",
    terminal: str = "P",
) -> TopologyPort:
    return TopologyPort(
        terminal=terminal,
        net=net,
        label=net,
        side=side,
        x=x,
        y=y,
        node_id=node_id,
    )


def _node(node_id: str, x: float, y: float = 0.0) -> TopologyNode:
    return TopologyNode(
        node_id=node_id,
        label=node_id,
        designator=node_id,
        role="SOURCE",
        x=x,
        y=y,
        width=NODE_W,
        height=40.0,
        config_label="",
        has_error=False,
        bounds=(x, y, NODE_W, 40.0),
    )


def test_path_start_to_bus_goes_outward_stub_first():
    """Left port must leave leftward even when the bus sits to the right."""
    port = _port(node_id="A", side="left", x=100.0, y=50.0)
    path, end_x, _ = path_start_to_bus_x(port, bus_x=200.0)
    pts = parse_wire_path(path)
    assert pts[0] == (100.0, 50.0)
    # First horizontal stop is the outward stub, not the bus.
    assert pts[1][0] == 100.0 - PORT_WIRE_STUB
    assert pts[1][0] < port.x
    assert end_x == 200.0
    assert pts[-1][0] == 200.0


def test_two_port_path_left_port_first_leg_is_leftward():
    left = _port(node_id="A", side="left", x=100.0, y=40.0)
    right = _port(node_id="B", side="left", x=300.0, y=80.0)
    node_a = _node("A", x=100.0, y=20.0)
    node_b = _node("B", x=300.0, y=60.0)
    # Bus to the right of A — previously drew through A's body.
    path = two_port_path(
        left,
        right,
        bus_x=250.0,
        net="VDD",
        obstacles=[node_a, node_b],
        ctx=RoutingContext(),
    )
    pts = parse_wire_path(path)
    assert pts[1][0] < left.x


def test_check_flags_segment_through_own_node():
    node = _node("A", x=100.0, y=0.0)
    # Chord from left face through the body to the right side.
    wire = TopologyWire(
        net="VDD",
        path_d="M 100.0,20.0 H 250.0",
        src_node="A",
        dst_node="B",
    )
    model = TopologyModel(nodes=[node], wires=[wire])
    issues = check_wires_through_foreign_nodes(model)
    assert any(i["code"] == "segment_through_own_node" for i in issues)
