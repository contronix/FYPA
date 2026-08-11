"""Unit tests for RULES.md validate codes (illegal models must fail)."""

from __future__ import annotations

from fypa.topology.constants import NODE_W
from fypa.topology.types import TopologyModel, TopologyNode, TopologyPort, TopologyWire
from fypa.topology.validate import (
    check_driver_left_of_load,
    check_port_sides,
    check_ports_overlapping,
    check_right_to_left_wires,
    check_wire_outside_channel,
    validate_topology,
)


def _node(
    node_id: str,
    *,
    role: str,
    x: float,
    y: float = 40.0,
    ports: list[TopologyPort] | None = None,
) -> TopologyNode:
    return TopologyNode(
        node_id=node_id,
        label=node_id,
        designator=node_id,
        role=role,
        x=x,
        y=y,
        width=NODE_W,
        height=40.0,
        config_label="",
        has_error=False,
        bounds=(x, y, x + NODE_W, y + 40.0),
        ports=ports or [],
    )


def test_port_on_wrong_side_output_on_left():
    port = TopologyPort(
        terminal="OUT_P",
        net="VOUT",
        label="VOUT",
        side="left",
        x=10.0,
        y=50.0,
        node_id="U1",
        role="REGULATOR",
    )
    model = TopologyModel(nodes=[_node("U1", role="REGULATOR", x=36.0, ports=[port])])
    issues = check_port_sides(model)
    assert any(i["code"] == "port_on_wrong_side" for i in issues)


def test_ports_overlapping_same_face():
    ports = [
        TopologyPort("P", "VIN", "VIN", "left", 36.0, 50.0, "J1", role="SINK"),
        TopologyPort("N", "GND", "GND", "left", 36.0, 50.0, "J1", role="SINK"),
    ]
    model = TopologyModel(nodes=[_node("J1", role="SINK", x=36.0, ports=ports)])
    issues = check_ports_overlapping(model)
    assert any(i["code"] == "ports_overlapping" for i in issues)


def test_right_to_left_wire_flagged():
    wire = TopologyWire(
        net="VIN",
        path_d="M 200.0,50.0 H 100.0",
        src_node="J1",
        src_terminal="P",
        dst_node="U1",
        dst_terminal="P",
    )
    model = TopologyModel(wires=[wire])
    issues = check_right_to_left_wires(model)
    assert any(i["code"] == "right_to_left_wire" for i in issues)


def test_short_channel_stub_rtl_allowed():
    """Left-face stub toward gutter bus is a short RTL path step — allowed."""
    port = TopologyPort(
        terminal="P",
        net="VIN",
        label="VIN",
        side="left",
        x=100.0,
        y=50.0,
        node_id="U1",
        role="SINK",
        stub_length=20.0,
    )
    node = _node("U1", role="SINK", x=100.0, ports=[port])
    # port at 100, stub tip at 80 (left face)
    wire = TopologyWire(
        net="VIN",
        path_d="M 100.0,50.0 H 80.0",
        src_node="U1",
        src_terminal="P",
        dst_node="",
        dst_terminal="",
    )
    assert check_right_to_left_wires(TopologyModel(nodes=[node], wires=[wire])) == []


def test_short_non_stub_rtl_flagged():
    """A short reverse run that is not a left-face stub is still illegal."""
    wire = TopologyWire(
        net="VIN",
        path_d="M 150.0,50.0 H 130.0",
        src_node="J1",
        src_terminal="P",
        dst_node="U1",
        dst_terminal="P",
    )
    issues = check_right_to_left_wires(TopologyModel(wires=[wire]))
    assert any(i["code"] == "right_to_left_wire" for i in issues)


def test_left_to_right_wire_ok():
    wire = TopologyWire(
        net="VIN",
        path_d="M 100.0,50.0 H 200.0",
        src_node="J1",
        src_terminal="P",
        dst_node="U1",
        dst_terminal="P",
    )
    assert check_right_to_left_wires(TopologyModel(wires=[wire])) == []


def test_driver_not_left_of_load():
    src = TopologyPort("P", "VIN", "VIN", "right", 300.0, 50.0, "J1", role="SOURCE")
    snk = TopologyPort("P", "VIN", "VIN", "left", 100.0, 50.0, "U1", role="SINK")
    model = TopologyModel(
        nodes=[
            _node("J1", role="SOURCE", x=200.0, ports=[src]),
            _node("U1", role="SINK", x=36.0, ports=[snk]),
        ]
    )
    issues = check_driver_left_of_load(model)
    assert any(i["code"] == "driver_not_left_of_load" for i in issues)


def test_driver_load_same_series_node_skipped():
    """0Ω bridge with P and N on the same net is not a column violation."""
    ports = [
        TopologyPort("P", "VIN", "VIN", "left", 36.0, 50.0, "R1", role="RESISTOR"),
        TopologyPort("N", "VIN", "VIN", "right", 164.0, 50.0, "R1", role="RESISTOR"),
    ]
    model = TopologyModel(nodes=[_node("R1", role="RESISTOR", x=36.0, ports=ports)])
    assert check_driver_left_of_load(model) == []


def test_wire_outside_channel_vertical_in_body():
    left = _node("J1", role="SOURCE", x=36.0)
    right = _node("U1", role="SINK", x=36.0 + NODE_W + 100.0)
    # Vertical inside U1's body (not in the column gap).
    wire = TopologyWire(
        net="VIN",
        path_d=f"M {right.x + 20.0:.1f},20.0 V 80.0",
        src_node="J1",
        src_terminal="P",
        dst_node="U1",
        dst_terminal="P",
    )
    model = TopologyModel(nodes=[left, right], wires=[wire])
    issues = check_wire_outside_channel(model)
    assert any(i["code"] == "wire_outside_channel" for i in issues)


def test_source_not_leftmost_when_all_sources_offset():
    src = TopologyPort("P", "VIN", "VIN", "right", 300.0, 50.0, "J1", role="SOURCE")
    other = TopologyPort("P", "VIN", "VIN", "left", 50.0, 50.0, "U1", role="SINK")
    model = TopologyModel(
        nodes=[
            _node("U1", role="SINK", x=36.0, ports=[other]),
            _node("J1", role="SOURCE", x=200.0, ports=[src]),
        ]
    )
    from fypa.topology.validate import check_source_sink_columns

    issues = check_source_sink_columns(model)
    assert any(i["code"] == "source_not_leftmost" for i in issues)


def test_sink_not_rightmost_when_all_sinks_offset():
    src = TopologyPort("P", "VIN", "VIN", "right", 50.0, 50.0, "J1", role="SOURCE")
    snk = TopologyPort("P", "VIN", "VIN", "left", 100.0, 50.0, "U1", role="SINK")
    reg = TopologyPort("OUT_P", "VOUT", "VOUT", "right", 300.0, 50.0, "U2", role="REGULATOR")
    model = TopologyModel(
        nodes=[
            _node("J1", role="SOURCE", x=36.0, ports=[src]),
            _node("U1", role="SINK", x=100.0, ports=[snk]),
            _node("U2", role="REGULATOR", x=200.0, ports=[reg]),
        ]
    )
    from fypa.topology.validate import check_source_sink_columns

    issues = check_source_sink_columns(model)
    assert any(i["code"] == "sink_not_rightmost" for i in issues)


def test_loop_series_fixed_faces_fail_closed_not_peer_facing():
    """Loop SERIES keep P/N faces; routing issues surface as validate errors."""
    from fypa.topology import build_topology_model, validate_topology
    from tests.topology_fixtures import load_topology_fixture

    model = build_topology_model(load_topology_fixture("project_a_stepper_loop_rails"))
    for node in model.nodes:
        if node.role not in ("SERIES", "RESISTOR"):
            continue
        for port in node.ports:
            if port.terminal.startswith("P"):
                assert port.side == "left"
            elif port.terminal.startswith("N"):
                assert port.side == "right"
    # Peer-facing was removed; cycle/channel gaps must fail closed, not silently
    # look clean with illegal faces.
    assert validate_topology(model)  # non-empty until channel router covers loops


def test_validate_topology_includes_rule_codes():
    port = TopologyPort(
        terminal="P",
        net="VIN",
        label="VIN",
        side="left",
        x=10.0,
        y=50.0,
        node_id="J1",
        role="SOURCE",
    )
    model = TopologyModel(nodes=[_node("J1", role="SOURCE", x=36.0, ports=[port])])
    codes = {i["code"] for i in validate_topology(model)}
    assert "port_on_wrong_side" in codes
