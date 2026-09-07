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
    assert any(i["code"] == "non_sink_in_rightmost" for i in issues)


def test_non_sink_in_rightmost_flags_series_peer():
    from fypa.topology.validate import check_source_sink_columns

    snk = TopologyPort("P", "VIN", "VIN", "left", 300.0, 50.0, "U2", role="SINK")
    series = TopologyPort("P", "OUT", "OUT", "left", 300.0, 150.0, "J1", role="RESISTOR")
    model = TopologyModel(
        nodes=[
            _node("U2", role="SINK", x=200.0, ports=[snk]),
            _node("J1", role="RESISTOR", x=200.0, y=140.0, ports=[series]),
        ]
    )
    issues = check_source_sink_columns(model)
    assert any(
        i["code"] == "non_sink_in_rightmost" and i.get("node_id") == "J1"
        for i in issues
    )


def test_loop_pair_return_faces_and_gutter():
    """Loop return ports face the peer; returns stay in the pair gutter."""
    from fypa.topology import build_topology_model, validate_topology
    from fypa.topology.validate import check_loop_return_in_pair_gutter
    from tests.topology_fixtures import load_topology_fixture

    model = build_topology_model(load_topology_fixture("project_a_stepper_loop_rails"))
    assert model.loop_parent.get("J7") == "U1"
    assert model.loop_return_nets >= frozenset({"AY", "BY"})
    u1 = next(n for n in model.nodes if n.designator == "U1")
    j7 = next(n for n in model.nodes if n.designator == "J7")
    u1_ports = {p.terminal: p for p in u1.ports}
    j7_ports = {p.terminal: p for p in j7.ports}
    assert u1_ports["P2"].side == "right"
    assert j7_ports["N1"].side == "left"
    assert check_loop_return_in_pair_gutter(model) == []
    # No RTL / driver≺load / open stubs on the return nets.
    bad = {
        i["code"]
        for i in validate_topology(model)
        if i.get("net") in model.loop_return_nets
    }
    assert "right_to_left_wire" not in bad
    assert "driver_not_left_of_load" not in bad
    assert "open_signal_stub" not in bad


def test_wire_detour_excessive():
    from fypa.topology.validate import check_wire_detour_excessive

    # Ends 100 apart; path wanders to length 500 (> 3×).
    wire = TopologyWire(
        net="VIN",
        path_d="M 0.0,0.0 H 100.0 V 200.0 H 0.0 V 0.0 H 100.0",
    )
    issues = check_wire_detour_excessive(TopologyModel(wires=[wire]))
    assert any(i["code"] == "wire_detour_excessive" for i in issues)


def test_wire_bends_excessive():
    from fypa.topology.validate import check_wire_bends_excessive

    # Manhattan-min 1; 5 bends > 1+2.
    wire = TopologyWire(
        net="VIN",
        path_d="M 0.0,0.0 H 20.0 V 20.0 H 40.0 V 40.0 H 100.0",
    )
    issues = check_wire_bends_excessive(TopologyModel(wires=[wire]))
    assert any(i["code"] == "wire_bends_excessive" for i in issues)


def test_redundant_parallel_run_same_net_horizontals():
    from fypa.topology.geometry import compute_schematic_geometry
    from fypa.topology.validate import check_redundant_parallel_runs

    wires = [
        TopologyWire(net="VIN", path_d="M 0.0,100.0 H 200.0"),
        TopologyWire(net="VIN", path_d="M 50.0,108.0 H 250.0"),
    ]
    geo = compute_schematic_geometry(wires)
    issues = check_redundant_parallel_runs(geo.segments)
    assert any(i["code"] == "redundant_parallel_run" for i in issues)


def test_redundant_parallel_run_allows_collinear():
    from fypa.topology.geometry import compute_schematic_geometry
    from fypa.topology.validate import check_redundant_parallel_runs

    wires = [
        TopologyWire(net="VIN", path_d="M 0.0,100.0 H 100.0"),
        TopologyWire(net="VIN", path_d="M 100.0,100.0 H 200.0"),
    ]
    geo = compute_schematic_geometry(wires)
    issues = check_redundant_parallel_runs(geo.segments)
    assert not any(i["code"] == "redundant_parallel_run" for i in issues)


def test_wire_detour_excessive_closed_path():
    from fypa.topology.validate import check_wire_detour_excessive

    wire = TopologyWire(
        net="VIN",
        path_d="M 0.0,0.0 H 100.0 V 100.0 H 0.0 V 0.0",
    )
    issues = check_wire_detour_excessive(TopologyModel(wires=[wire]))
    assert any(i["code"] == "wire_detour_excessive" for i in issues)


def test_hub_net_unrouted():
    from fypa.topology.validate import check_hub_net_unrouted

    ports = [
        TopologyPort("P", "VIN", "VIN", "right", 100.0, 50.0, "J1", role="SOURCE"),
        TopologyPort("P", "VIN", "VIN", "left", 300.0, 50.0, "U1", role="SINK"),
        TopologyPort("P", "VIN", "VIN", "left", 300.0, 150.0, "U2", role="SINK"),
    ]
    model = TopologyModel(
        nodes=[
            _node("J1", role="SOURCE", x=36.0, ports=[ports[0]]),
            _node("U1", role="SINK", x=200.0, ports=[ports[1]]),
            _node("U2", role="SINK", x=200.0, y=140.0, ports=[ports[2]]),
        ]
    )
    issues = check_hub_net_unrouted(model)
    assert any(i["code"] == "hub_net_unrouted" for i in issues)


def test_pair_net_unrouted():
    from fypa.topology.validate import check_hub_net_unrouted

    ports = [
        TopologyPort("P", "VOUT", "VOUT", "right", 100.0, 50.0, "U1", role="REGULATOR"),
        TopologyPort("P", "VOUT", "VOUT", "left", 300.0, 50.0, "J1", role="SINK"),
    ]
    model = TopologyModel(
        nodes=[
            _node("U1", role="REGULATOR", x=36.0, ports=[ports[0]]),
            _node("J1", role="SINK", x=200.0, ports=[ports[1]]),
        ]
    )
    issues = check_hub_net_unrouted(model)
    assert any(i["code"] == "hub_net_unrouted" and i["port_count"] == 2 for i in issues)


def test_loop_return_rtl_exempt():
    wire = TopologyWire(net="RETA", path_d="M 200.0,50.0 H 100.0")
    model = TopologyModel(wires=[wire], loop_return_nets=frozenset({"RETA"}))
    assert check_right_to_left_wires(model) == []


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


def test_source_return_on_left_is_wrong_side():
    from fypa.topology.validate import check_port_sides

    port = TopologyPort(
        terminal="N",
        net="GND",
        label="GND",
        side="left",
        x=10.0,
        y=70.0,
        node_id="J1",
        role="SOURCE",
    )
    model = TopologyModel(nodes=[_node("J1", role="SOURCE", x=36.0, ports=[port])])
    assert any(i["code"] == "port_on_wrong_side" for i in check_port_sides(model))

