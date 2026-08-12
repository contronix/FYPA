"""Rule-catalog validation (see ``fypa/topology/RULES.md``)."""

from __future__ import annotations

from collections import defaultdict

from fypa.topology.constants import (
    GND_NET,
    MAX_DETOUR_RATIO,
    MAX_EXTRA_BENDS,
    NODE_W,
    PORT_R,
    PORT_WIRE_STUB,
    WIRE_EPS,
    WIRE_GUTTER_PAD,
)
from fypa.topology.geometry import (
    SchematicGeometry,
    compute_schematic_geometry,
    parse_wire_path,
)
from fypa.topology.issues import make_issue
from fypa.topology.placement import column_gaps_from_nodes, row_gaps_from_nodes
from fypa.topology.routing.cost import manhattan, path_length
from fypa.topology.terminal_roles import expected_port_side, is_output_port
from fypa.topology.types import TopologyModel, TopologyNode


def _expected_side_for_port(
    model: TopologyModel,
    role: str,
    terminal: str,
    net: str,
) -> str | None:
    """Canonical face, with loop-return override (RULES.md §19)."""
    # Rule 19 only rewrites SERIES/RESISTOR peer faces on return nets.
    if (
        net
        and net in model.loop_return_nets
        and role in ("SERIES", "RESISTOR")
    ):
        if terminal.startswith("P"):
            return "right"
        if terminal.startswith("N"):
            return "left"
    return expected_port_side(role, terminal)


def check_port_sides(model: TopologyModel) -> list[dict]:
    """Outputs on the right, inputs on the left (RULES ports)."""
    issues: list[dict] = []
    for node in model.nodes:
        if node.role in ("GND",):
            continue
        for port in node.ports:
            role = port.role or node.role
            want = _expected_side_for_port(model, role, port.terminal, port.net)
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
    """Power horizontals must not travel right→left except left-face channel stubs.

    A short RTL step is allowed only when it starts at a left-face port and ends
    at that port's stub tip (channel entry). Other short reverse runs are illegal.
    Loop-return nets and GND are exempt (RULES.md §5 / §19).
    """
    from fypa.topology.placement import port_stub_x

    max_stub = PORT_WIRE_STUB + WIRE_GUTTER_PAD + WIRE_EPS
    left_ports: list[tuple[float, float, float]] = []
    for node in model.nodes:
        for port in node.ports:
            if port.side != "left" or not port.net or port.net == GND_NET:
                continue
            left_ports.append((port.x, port.y, port_stub_x(port)))

    def _is_left_channel_stub(x1: float, y1: float, x2: float) -> bool:
        for px, py, stub_x in left_ports:
            if abs(y1 - py) > WIRE_EPS:
                continue
            span = x1 - x2
            if span <= WIRE_EPS or span > max_stub + WIRE_EPS:
                continue
            # Port → stub tip
            if abs(x1 - px) <= WIRE_EPS and abs(x2 - stub_x) <= WIRE_EPS:
                return True
            # Stub tip → nearby gutter bus (still within the stub channel band)
            if abs(x1 - stub_x) <= WIRE_EPS and x2 >= min(px, stub_x) - max_stub - WIRE_EPS:
                return True
        return False

    issues: list[dict] = []
    for wi, wire in enumerate(model.wires):
        if wire.dashed or not wire.net or wire.net == GND_NET:
            continue
        if wire.net in model.loop_return_nets:
            continue
        points = parse_wire_path(wire.path_d)
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            if abs(y1 - y2) >= WIRE_EPS:
                continue
            if x1 <= x2 + WIRE_EPS:
                continue
            if _is_left_channel_stub(x1, y1, x2):
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
    symbol are not a driver→load column violation. Loop-return nets are exempt.
    """
    outputs: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    inputs: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for node in model.nodes:
        if node.role == "GND":
            continue
        for port in node.ports:
            if not port.net or port.net in (GND_NET, "?"):
                continue
            if port.net in model.loop_return_nets:
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
    """V buses in column gutters; H runs in row gutters (RULES.md §8 / §9)."""
    if geo is None:
        geo = compute_schematic_geometry(
            model.wires,
            gnd_symbol_x=model.gnd_symbol_x,
            gnd_bus_y=model.gnd_bus_y,
        )
    nodes = [n for n in model.nodes if n.role != "GND"]
    gap_intervals = column_gaps_from_nodes(nodes)
    row_intervals = row_gaps_from_nodes(nodes)

    def _in_column_gap(x: float) -> bool:
        for lo, hi in gap_intervals:
            if lo - WIRE_EPS <= x <= hi + WIRE_EPS:
                return True
        return False

    def _in_row_gap(y: float) -> bool:
        for lo, hi in row_intervals:
            if lo - WIRE_EPS <= y <= hi + WIRE_EPS:
                return True
        return False

    def _inside_node_x(node: TopologyNode, x: float) -> bool:
        return node.x + WIRE_EPS < x < node.x + node.width - WIRE_EPS

    def _inside_node_y(node: TopologyNode, y: float) -> bool:
        return node.y + WIRE_EPS < y < node.y + node.height - WIRE_EPS

    issues: list[dict] = []
    if gap_intervals:
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

    if row_intervals:
        for seg in geo.horizontals:
            if seg.net == GND_NET:
                continue
            y = seg.y1
            # Short port-row / stub horizontals are not row-gutter runs.
            x_lo, x_hi = min(seg.x1, seg.x2), max(seg.x1, seg.x2)
            if abs(x_hi - x_lo) <= PORT_WIRE_STUB + WIRE_GUTTER_PAD + WIRE_EPS:
                continue
            if any(
                abs(y - p.y) <= WIRE_EPS
                for n in nodes
                for p in n.ports
                if p.net == seg.net
            ):
                continue
            if _in_row_gap(y):
                continue
            # Only flag when the run actually crosses a symbol body in X and Y.
            if any(
                _inside_node_y(n, y)
                and n.x + WIRE_EPS < x_hi
                and x_lo < n.x + n.width - WIRE_EPS
                for n in nodes
            ):
                issues.append(
                    make_issue(
                        "wire_outside_channel",
                        (
                            f"Horizontal {seg.net} at y={y:.1f} is not in a row "
                            f"gutter (runs through a symbol row)"
                        ),
                        net=seg.net,
                        y=round(y, 1),
                        orient="H",
                    )
                )
    return issues


def check_source_sink_columns(model: TopologyModel) -> list[dict]:
    """SOURCE leftmost; rightmost column is SINK-only (pure or multi-role with SINK)."""
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

    def _roles_of(n: TopologyNode) -> set[str]:
        if n.sections:
            return {sec.role for sec in n.sections}
        return {n.role}

    def _has_sink_role(n: TopologyNode) -> bool:
        return "SINK" in _roles_of(n)

    sinks = [n for n in directive if _has_sink_role(n)]
    pure_sinks = [n for n in sinks if _roles_of(n) == {"SINK"}]
    for s in pure_sinks:
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
    for n in by_x[rightmost]:
        if not _has_sink_role(n):
            issues.append(
                make_issue(
                    "non_sink_in_rightmost",
                    f"{n.designator} ({n.role}) must not share the rightmost "
                    "column with SINKs",
                    node_id=n.node_id,
                    role=n.role,
                )
            )
    return issues


def check_loop_return_in_pair_gutter(model: TopologyModel) -> list[dict]:
    """Loop-return verticals must sit in the shared gutter of the loop pair."""
    if not model.loop_return_nets or not model.loop_parent:
        return []
    nodes_by_id = {n.node_id: n for n in model.nodes}
    issues: list[dict] = []
    geo = compute_schematic_geometry(
        model.wires,
        gnd_symbol_x=model.gnd_symbol_x,
        gnd_bus_y=model.gnd_bus_y,
    )
    for child_id, parent_id in model.loop_parent.items():
        child = nodes_by_id.get(child_id)
        parent = nodes_by_id.get(parent_id)
        if child is None or parent is None:
            continue
        gutter_lo = min(parent.x + NODE_W, child.x)
        gutter_hi = max(parent.x + NODE_W, child.x)
        if gutter_hi - gutter_lo < WIRE_EPS:
            continue
        for seg in geo.verticals:
            if seg.net not in model.loop_return_nets:
                continue
            # Only constrain return nets that touch this pair.
            pair_ports = [
                p
                for n in (parent, child)
                for p in n.ports
                if p.net == seg.net
            ]
            if len(pair_ports) < 2:
                continue
            x = seg.x1
            if gutter_lo - WIRE_EPS <= x <= gutter_hi + WIRE_EPS:
                continue
            issues.append(
                make_issue(
                    "loop_return_outside_pair_gutter",
                    (
                        f"Loop return {seg.net} vertical at x={x:.1f} is outside "
                        f"the pair gutter [{gutter_lo:.1f}, {gutter_hi:.1f}]"
                    ),
                    net=seg.net,
                    x=round(x, 1),
                    gutter_lo=round(gutter_lo, 1),
                    gutter_hi=round(gutter_hi, 1),
                    parent=parent_id,
                    child=child_id,
                )
            )
    return issues


def check_wire_detour_excessive(model: TopologyModel) -> list[dict]:
    """Flag wires whose drawn length exceeds Manhattan(ends) × MAX_DETOUR_RATIO."""
    issues: list[dict] = []
    for wi, wire in enumerate(model.wires):
        if wire.dashed or not wire.net or wire.net == GND_NET:
            continue
        points = parse_wire_path(wire.path_d)
        if len(points) < 2:
            continue
        drawn = path_length(points)
        base = manhattan(points[0], points[-1])
        if base < WIRE_EPS:
            # Closed / zero-span path: any real drawn length is an excessive detour.
            if drawn <= WIRE_EPS:
                continue
            issues.append(
                make_issue(
                    "wire_detour_excessive",
                    (
                        f"Wire {wire.net} has drawn length {drawn:.1f} with "
                        f"coincident endpoints (Manhattan 0)"
                    ),
                    net=wire.net,
                    length=round(drawn, 1),
                    manhattan=0.0,
                    ratio=None,
                    wire_index=wi,
                )
            )
            continue
        if drawn <= base * MAX_DETOUR_RATIO + WIRE_EPS:
            continue
        issues.append(
            make_issue(
                "wire_detour_excessive",
                (
                    f"Wire {wire.net} length {drawn:.1f} exceeds "
                    f"{MAX_DETOUR_RATIO:.1f}× Manhattan {base:.1f}"
                ),
                net=wire.net,
                length=round(drawn, 1),
                manhattan=round(base, 1),
                ratio=round(drawn / base, 2),
                wire_index=wi,
            )
        )
    return issues


def _polyline_bend_count(points: list[tuple[float, float]]) -> int:
    """Count axis changes / reversals along an orthogonal polyline."""
    bends = 0
    i = 1
    while i < len(points) - 1:
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dx1, dy1 = x1 - x0, y1 - y0
        dx2, dy2 = x2 - x1, y2 - y1
        if abs(dx1) + abs(dy1) < WIRE_EPS:
            i += 1
            continue
        if abs(dx2) + abs(dy2) < WIRE_EPS:
            i += 1
            continue
        o1 = "H" if abs(dy1) < WIRE_EPS else "V"
        o2 = "H" if abs(dy2) < WIRE_EPS else "V"
        if o1 != o2 or dx1 * dx2 + dy1 * dy2 < 0:
            bends += 1
        i += 1
    return bends


def check_wire_bends_excessive(model: TopologyModel) -> list[dict]:
    """Flag wires with more bends than Manhattan-min + MAX_EXTRA_BENDS."""
    issues: list[dict] = []
    for wi, wire in enumerate(model.wires):
        if wire.dashed or not wire.net or wire.net == GND_NET:
            continue
        points = parse_wire_path(wire.path_d)
        if len(points) < 2:
            continue
        bends = _polyline_bend_count(points)
        x0, y0 = points[0]
        x1, y1 = points[-1]
        manhattan_min = 0 if abs(x0 - x1) < WIRE_EPS or abs(y0 - y1) < WIRE_EPS else 1
        limit = manhattan_min + MAX_EXTRA_BENDS
        if bends <= limit:
            continue
        issues.append(
            make_issue(
                "wire_bends_excessive",
                (
                    f"Wire {wire.net} has {bends} bends "
                    f"(limit {limit} = Manhattan-min {manhattan_min} "
                    f"+ MAX_EXTRA_BENDS {MAX_EXTRA_BENDS})"
                ),
                net=wire.net,
                bends=bends,
                limit=limit,
                manhattan_min=manhattan_min,
                wire_index=wi,
            )
        )
    return issues


def check_hub_net_unrouted(model: TopologyModel) -> list[dict]:
    """Nets with 2+ ports and no connecting wires (fail-closed route drop)."""
    by_net: dict[str, list] = defaultdict(list)
    for node in model.nodes:
        for port in node.ports:
            if port.net and port.net != GND_NET:
                by_net[port.net].append(port)
    wired = {w.net for w in model.wires if w.net and w.path_d}
    issues: list[dict] = []
    for net, ports in by_net.items():
        if len(ports) < 2:
            continue
        if net in wired:
            continue
        kind = "Hub" if len(ports) >= 3 else "Pair"
        issues.append(
            make_issue(
                "hub_net_unrouted",
                (
                    f"{kind} net {net!r} has {len(ports)} ports but no wires "
                    f"(fail-closed: no legal connected channel geometry)"
                ),
                net=net,
                port_count=len(ports),
            )
        )
    return issues
