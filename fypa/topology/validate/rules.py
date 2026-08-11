"""Rule-catalog validation (see ``fypa/topology/RULES.md``)."""

from __future__ import annotations

from collections import defaultdict

from fypa.topology.constants import GND_NET, PORT_R, PORT_WIRE_STUB, WIRE_EPS, WIRE_GUTTER_PAD
from fypa.topology.geometry import (
    SchematicGeometry,
    compute_schematic_geometry,
)
from fypa.topology.issues import make_issue
from fypa.topology.placement import column_gaps_from_nodes
from fypa.topology.terminal_roles import expected_port_side, is_output_port
from fypa.topology.types import TopologyModel, TopologyNode


def check_port_sides(model: TopologyModel) -> list[dict]:
    """Outputs on the right, inputs on the left (RULES ports)."""
    issues: list[dict] = []
    for node in model.nodes:
        if node.role in ("GND",):
            continue
        for port in node.ports:
            role = port.role or node.role
            want = expected_port_side(role, port.terminal)
            if want is None:
                continue
            if port.side != want:
                issues.append(
                    make_issue(
                        "port_on_wrong_side",
                        (
                            f"{node.designator}.{port.terminal} is on the "
                            f"{port.side} face; expected {want} for role {role}"
                        ),
                        node_id=node.node_id,
                        terminal=port.terminal,
                        side=port.side,
                        expected_side=want,
                        role=role,
                    )
                )
    return issues


def check_ports_overlapping(model: TopologyModel) -> list[dict]:
    """Ports on the same face must not share the same Y."""
    issues: list[dict] = []
    min_sep = max(PORT_R * 2.0, 1.0)
    for node in model.nodes:
        by_side: dict[str, list] = defaultdict(list)
        for port in node.ports:
            by_side[port.side].append(port)
        for side, ports in by_side.items():
            ordered = sorted(ports, key=lambda p: (p.y, p.terminal))
            for a, b in zip(ordered, ordered[1:]):
                if abs(a.y - b.y) < min_sep - WIRE_EPS:
                    issues.append(
                        make_issue(
                            "ports_overlapping",
                            (
                                f"{node.designator} ports {a.terminal} and "
                                f"{b.terminal} overlap on the {side} face "
                                f"at y≈{a.y:.1f}"
                            ),
                            node_id=node.node_id,
                            terminal_a=a.terminal,
                            terminal_b=b.terminal,
                            side=side,
                            y=round(a.y, 1),
                        )
                    )
    return issues


def check_right_to_left_wires(model: TopologyModel) -> list[dict]:
    """Power horizontals must not travel right→left except short channel stubs.

    Left-face ports reach their gutter bus with a short outward stub (path
    order port→bus is decreasing x). Those channel entries are allowed up to
    ``PORT_WIRE_STUB + WIRE_GUTTER_PAD``. Longer RTL runs are illegal.
    """
    from fypa.topology.geometry import parse_wire_path

    max_stub = PORT_WIRE_STUB + WIRE_GUTTER_PAD + WIRE_EPS
    issues: list[dict] = []
    for wi, wire in enumerate(model.wires):
        if wire.dashed or not wire.net or wire.net == GND_NET:
            continue
        points = parse_wire_path(wire.path_d)
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            if abs(y1 - y2) >= WIRE_EPS:
                continue
            if x1 <= x2 + WIRE_EPS:
                continue
            if (x1 - x2) <= max_stub:
                continue
            issues.append(
                make_issue(
                    "right_to_left_wire",
                    (
                        f"Wire {wire.net} has a right-to-left horizontal "
                        f"from x={x1:.1f} to x={x2:.1f}"
                    ),
                    net=wire.net,
                    x1=round(x1, 1),
                    x2=round(x2, 1),
                    y=round(y1, 1),
                    wire_index=wi,
                )
            )
    return issues


def check_driver_left_of_load(model: TopologyModel) -> list[dict]:
    """Every power output port must sit strictly left of every input on that net.

    Same-node P/N shorts (0Ω bridge on one net) are skipped — both faces of one
    symbol are not a driver→load column violation.
    """
    outputs: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    inputs: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for node in model.nodes:
        if node.role == "GND":
            continue
        for port in node.ports:
            if not port.net or port.net in (GND_NET, "?"):
                continue
            role = port.role or node.role
            if is_output_port(role, port.terminal, port.side):
                outputs[port.net].append((node.node_id, port.terminal, port.x))
            elif expected_port_side(role, port.terminal) == "left" and role != "SOURCE":
                if role == "SINK" and port.terminal.startswith("N"):
                    continue
                if role == "REGULATOR" and port.terminal.startswith("IN_N"):
                    continue
                if role in ("RESISTOR", "SERIES") and port.terminal.startswith("N"):
                    continue
                if role == "SOURCE":
                    continue
                inputs[port.net].append((node.node_id, port.terminal, port.x))

    issues: list[dict] = []
    for net, outs in outputs.items():
        ins = inputs.get(net) or []
        if not outs or not ins:
            continue
        for out_nid, _oterm, out_x in outs:
            for in_nid, _iterm, in_x in ins:
                if out_nid == in_nid:
                    continue
                if out_x >= in_x - WIRE_EPS:
                    issues.append(
                        make_issue(
                            "driver_not_left_of_load",
                            (
                                f"Net {net}: driver {out_nid} x={out_x:.1f} is not "
                                f"strictly left of load {in_nid} x={in_x:.1f}"
                            ),
                            net=net,
                            driver_x=round(out_x, 1),
                            load_x=round(in_x, 1),
                            driver_node=out_nid,
                            load_node=in_nid,
                        )
                    )
    return issues


def check_wire_outside_channel(
    model: TopologyModel,
    geo: SchematicGeometry | None = None,
) -> list[dict]:
    """Vertical power buses must sit in column gutters, not inside symbol columns.

    Horizontal-through-body is covered by ``segment_through_foreign_node``;
    this check focuses on the V-in-gutter rule from RULES.md.
    """
    if geo is None:
        geo = compute_schematic_geometry(
            model.wires,
            gnd_symbol_x=model.gnd_symbol_x,
            gnd_bus_y=model.gnd_bus_y,
        )
    nodes = [n for n in model.nodes if n.role != "GND"]
    gap_intervals = column_gaps_from_nodes(nodes)

    def _in_column_gap(x: float) -> bool:
        for lo, hi in gap_intervals:
            if lo - WIRE_EPS <= x <= hi + WIRE_EPS:
                return True
        return False

    def _inside_node_x(node: TopologyNode, x: float) -> bool:
        return node.x + WIRE_EPS < x < node.x + node.width - WIRE_EPS

    issues: list[dict] = []
    if not gap_intervals:
        return issues
    for seg in geo.verticals:
        if seg.net == GND_NET:
            continue
        x = seg.x1
        if _in_column_gap(x):
            continue
        if any(_inside_node_x(n, x) for n in nodes):
            issues.append(
                make_issue(
                    "wire_outside_channel",
                    (
                        f"Vertical {seg.net} at x={x:.1f} is not in a column "
                        f"gutter (runs in a symbol column)"
                    ),
                    net=seg.net,
                    x=round(x, 1),
                    orient="V",
                )
            )
    return issues


def check_source_sink_columns(model: TopologyModel) -> list[dict]:
    """SOURCE in leftmost occupied column band; pure SINK in rightmost."""
    directive = [n for n in model.nodes if n.role != "GND"]
    if not directive:
        return []
    # Column bands by rounded node.x
    by_x: dict[float, list[TopologyNode]] = defaultdict(list)
    for n in directive:
        by_x[round(n.x, 1)].append(n)
    xs = sorted(by_x.keys())
    leftmost, rightmost = xs[0], xs[-1]
    issues: list[dict] = []
    sources = [n for n in directive if n.role == "SOURCE"]
    for s in sources:
        if round(s.x, 1) > leftmost + WIRE_EPS:
            # Allow SOURCE not alone on left only if some SOURCE is on leftmost
            if not any(round(n.x, 1) <= leftmost + WIRE_EPS for n in sources):
                issues.append(
                    make_issue(
                        "source_not_leftmost",
                        f"SOURCE {s.designator} is not in the leftmost column",
                        node_id=s.node_id,
                    )
                )
    sinks = [n for n in directive if n.role == "SINK"]
    # Pure sink: only SINK role (composites may be mid)
    for s in sinks:
        if round(s.x, 1) < rightmost - WIRE_EPS:
            # Only flag when *no* sink sits on the rightmost column
            if not any(round(n.x, 1) >= rightmost - WIRE_EPS for n in sinks):
                issues.append(
                    make_issue(
                        "sink_not_rightmost",
                        f"SINK {s.designator} is not in the rightmost column",
                        node_id=s.node_id,
                    )
                )
    return issues
