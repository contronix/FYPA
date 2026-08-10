"""Column gaps, node placement, and iterative gutter widening."""

from __future__ import annotations

from fypa.topology.constants import (
    BODY_PAD,
    COL_GAP,
    GND_NET,
    HEADER_H,
    MARGIN,
    MIN_PARALLEL_GAP,
    NODE_W,
    PORT_ROW_H,
    PORT_WIRE_STUB,
    WIRE_EPS,
    WIRE_GUTTER_PAD,
)
from fypa.topology.layout.stubs import assign_edge_wire_columns, assign_stacked_stub_lengths
from fypa.topology.layout.vertical_align import (
    assign_vertical_positions,
    composite_node_height,
    node_height,
    port_layout_rows,
    section_y_offsets,
)
from fypa.topology.metadata.specs import spec_port_role
from fypa.topology.metadata.layout_bridge import (
    is_return_port_row,
    jump_row_for_directive,
)
from fypa.topology.metadata_schema import NodeSpec
from fypa.topology.terminal_roles import is_single_net_node
from fypa.topology.placement import (
    BusPlan,
    gutter_bus_span_from_plan,
    gutter_groups,
    group_ports_by_net,
    plan_signal_buses,
)
from fypa.topology.terminal_roles import is_power_input_port
from fypa.topology.types import NodeSection, TopologyNode, TopologyPort


def _col_gap_for_gutter_slots(n_slots: int, *, gnd_reserve: bool = True) -> float:
    if n_slots <= 1:
        return COL_GAP
    gnd_pad = MIN_PARALLEL_GAP if gnd_reserve else 0.0
    needed_usable = (n_slots - 1) * MIN_PARALLEL_GAP + gnd_pad
    return max(
        COL_GAP,
        needed_usable + 2 * PORT_WIRE_STUB + MIN_PARALLEL_GAP + WIRE_GUTTER_PAD,
    )


def _col_gap_for_bus_span(x_min: float, x_max: float) -> float:
    needed = (x_max - x_min) + 2 * PORT_WIRE_STUB + MIN_PARALLEL_GAP + WIRE_GUTTER_PAD
    return max(COL_GAP, needed)


def _gutter_channel_count(nets_in_gutter: set[str], all_ports: list[TopologyPort]) -> int:
    count = 0
    for net in nets_in_gutter:
        group = [p for p in all_ports if p.net == net]
        if len(group) < 2:
            continue
        count += 1
    return max(count, 1)


def _column_x_positions(
    x_offset: float,
    gaps: list[float],
    max_col: int,
) -> list[float]:
    xs = [x_offset]
    for c in range(max_col):
        xs.append(xs[-1] + NODE_W + gaps[c])
    return xs


def _required_gaps(
    all_ports: list[TopologyPort],
    col_x: list[float],
    max_col: int,
    base_gap: float,
    bus_plan: BusPlan,
    *,
    gnd_bus_y: float | None = None,
) -> list[float]:
    """Per-gap widths: each gap only widens for gutters whose span crosses it.

    Bus *span* width (``x_max - x_min``) is applied only when those buses sit in
    a **single** column gap. Multi-column port spans (e.g. SOURCE→far SINK) often
    collect buses in different corridors; treating their global min/max as every
    intervening gap's width previously exploded the canvas (empty-looking gutters).
    """
    del gnd_bus_y
    gaps = [base_gap] * max_col
    spans = gutter_bus_span_from_plan(bus_plan, all_ports)
    planned_xs: list[float] = []
    for xs in bus_plan.gutter_spans.values():
        planned_xs.extend(xs)
    planned_xs.extend(bus_plan.hub_buses.values())
    planned_xs.extend(bus_plan.pair_buses.values())
    planned_xs.extend(bus_plan.stack_buses.values())

    for (x_lo, x_hi), nets_in_gutter in gutter_groups(all_ports).items():
        n_channels = _gutter_channel_count(nets_in_gutter, all_ports)
        overlapped = [
            g
            for g in range(max_col)
            if x_hi > col_x[g] + NODE_W + WIRE_EPS and x_lo < col_x[g + 1] - WIRE_EPS
        ]
        if not overlapped:
            continue

        slot_req = base_gap
        if n_channels > 1:
            slot_req = max(slot_req, _col_gap_for_gutter_slots(n_channels))
        measured = spans.get((x_lo, x_hi))
        if measured is not None and measured[2] > 1:
            slot_req = max(slot_req, _col_gap_for_gutter_slots(measured[2]))

        if len(overlapped) == 1 and measured is not None and measured[2] > 1:
            x_min, x_max, _n_buses = measured
            slot_req = max(slot_req, _col_gap_for_bus_span(x_min, x_max))

        for g in overlapped:
            gaps[g] = max(gaps[g], slot_req)

    # Local parallel buses inside one corridor still need their measured span.
    for g in range(max_col):
        gap_lo = col_x[g] + NODE_W
        gap_hi = col_x[g + 1]
        local = [x for x in planned_xs if gap_lo - WIRE_EPS <= x <= gap_hi + WIRE_EPS]
        if len(local) > 1:
            gaps[g] = max(gaps[g], _col_gap_for_bus_span(min(local), max(local)))
    return gaps


def _place_columns(
    node_specs: list[NodeSpec],
    by_col: dict[int, list[NodeSpec]],
    max_col: int,
    col_x: list[float],
    *,
    columns: dict[str, int] | None = None,
    y_assign: dict[str, float] | None = None,
) -> tuple[list[TopologyNode], list[TopologyPort]]:
    if columns is None:
        columns = {}
        for c, col_specs in by_col.items():
            for s in col_specs:
                columns[s["node_id"]] = c
    if y_assign is None:
        y_assign = assign_vertical_positions(node_specs, columns, max_col)

    nodes: list[TopologyNode] = []
    all_ports: list[TopologyPort] = []
    for c in range(max_col + 1):
        for s in by_col.get(c, []):
            sections = s.get("sections")
            if sections:
                nh = composite_node_height(sections)
            else:
                visible_ports = s["port_defs"]
                n_layout_rows, _ = port_layout_rows(visible_ports)
                nh = node_height(n_layout_rows)
            nx = col_x[c]
            ny = y_assign[s["node_id"]]
            role = s["role"]
            node = TopologyNode(
                node_id=s["node_id"],
                label=s["label"],
                designator=s["designator"],
                role=role,
                x=nx,
                y=ny,
                width=NODE_W,
                height=nh,
                config_label="",
                has_error=s["has_error"],
                tooltip=s.get("tooltip", ""),
                single_net=is_single_net_node(role, s["port_defs"]) if not sections else False,
                bounds=(nx, ny, NODE_W, nh),
                jump_row=jump_row_for_directive(s["directive"]),
            )
            resolved_ports = s.get("resolved_ports") or {}
            if sections:
                for sec, sec_y, sec_h in section_y_offsets(sections):
                    node.sections.append(
                        NodeSection(role=sec["role"], y=sec_y, height=sec_h),
                    )
                    n_rows, return_row_map = port_layout_rows(sec["port_defs"])
                    sorted_ports = sorted(sec["port_defs"], key=lambda t: t[2])
                    for pname, side, sort_key in sorted_ports:
                        resolved = resolved_ports.get(pname)
                        if resolved is None:
                            continue
                        row_i = (
                            sort_key
                            if not is_return_port_row(sort_key)
                            else return_row_map[sort_key]
                        )
                        py = (
                            ny + sec_y + HEADER_H + BODY_PAD
                            + row_i * PORT_ROW_H + PORT_ROW_H / 2
                        )
                        px = nx if side == "left" else nx + NODE_W
                        port_role = spec_port_role(s, pname)
                        port = TopologyPort(
                            terminal=pname,
                            net=resolved.wnet,
                            label=resolved.plabel,
                            side=side,
                            x=px,
                            y=py,
                            node_id=s["node_id"],
                            role=port_role,
                            is_power_input=is_power_input_port(port_role, pname),
                            tooltip=resolved.tooltip,
                        )
                        node.ports.append(port)
                        all_ports.append(port)
            else:
                visible_ports = s["port_defs"]
                n_layout_rows, return_row_map = port_layout_rows(visible_ports)
                sorted_ports = sorted(visible_ports, key=lambda t: t[2])
                for pname, side, sort_key in sorted_ports:
                    resolved = resolved_ports.get(pname)
                    if resolved is None:
                        continue
                    row_i = sort_key if not is_return_port_row(sort_key) else return_row_map[sort_key]
                    py = ny + HEADER_H + BODY_PAD + row_i * PORT_ROW_H + PORT_ROW_H / 2
                    px = nx if side == "left" else nx + NODE_W
                    port = TopologyPort(
                        terminal=pname,
                        net=resolved.wnet,
                        label=resolved.plabel,
                        side=side,
                        x=px,
                        y=py,
                        node_id=s["node_id"],
                        role=role,
                        is_power_input=is_power_input_port(role, pname),
                        tooltip=resolved.tooltip,
                    )
                    node.ports.append(port)
                    all_ports.append(port)
            assign_stacked_stub_lengths(node.ports)
            nodes.append(node)
    for node in nodes:
        assign_edge_wire_columns(node.ports, node.role, all_ports)
    return nodes, all_ports


def _columns_from_by_col(by_col: dict[int, list[NodeSpec]]) -> dict[str, int]:
    return {s["node_id"]: c for c, col_specs in by_col.items() for s in col_specs}


def place_nodes(
    node_specs: list[NodeSpec],
    by_col: dict[int, list[NodeSpec]],
    max_col: int,
    *,
    x_offset: float = MARGIN,
    col_gap: float = COL_GAP,
    gnd_bus_y: float | None = None,
    y_assign: dict[str, float] | None = None,
) -> tuple[list[TopologyNode], list[TopologyPort], float, BusPlan, list[float]]:
    gaps = [col_gap] * max_col
    nodes: list[TopologyNode] = []
    all_ports: list[TopologyPort] = []
    slot_plan = BusPlan()
    columns = _columns_from_by_col(by_col)

    for _ in range(max_col + 2):
        col_x = _column_x_positions(x_offset, gaps, max_col)
        nodes, all_ports = _place_columns(
            node_specs,
            by_col,
            max_col,
            col_x,
            columns=columns,
            y_assign=y_assign,
        )
        new_gaps = _required_gaps(
            all_ports,
            col_x,
            max_col,
            base_gap=col_gap,
            bus_plan=slot_plan,
            gnd_bus_y=gnd_bus_y,
        )
        if all(new_gaps[g] <= gaps[g] + WIRE_EPS for g in range(max_col)):
            break
        gaps = [max(gaps[g], new_gaps[g]) for g in range(max_col)]

    col_x = _column_x_positions(x_offset, gaps, max_col)
    by_net = group_ports_by_net(all_ports)
    gnd_ports = by_net.get(GND_NET, [])
    bus_plan = plan_signal_buses(
        by_net,
        gnd_ports=gnd_ports if gnd_bus_y is not None else None,
        gnd_bus_y=gnd_bus_y,
        obstacles=nodes,
    )
    refined_gaps = _required_gaps(
        all_ports,
        col_x,
        max_col,
        base_gap=col_gap,
        bus_plan=bus_plan,
        gnd_bus_y=gnd_bus_y,
    )
    if any(refined_gaps[g] > gaps[g] + WIRE_EPS for g in range(max_col)):
        gaps = [max(gaps[g], refined_gaps[g]) for g in range(max_col)]
        col_x = _column_x_positions(x_offset, gaps, max_col)
        nodes, all_ports = _place_columns(
            node_specs,
            by_col,
            max_col,
            col_x,
            columns=columns,
            y_assign=y_assign,
        )
        by_net = group_ports_by_net(all_ports)
        gnd_ports = by_net.get(GND_NET, [])
        bus_plan = plan_signal_buses(
            by_net,
            gnd_ports=gnd_ports if gnd_bus_y is not None else None,
            gnd_bus_y=gnd_bus_y,
            obstacles=nodes,
        )

    content_right = max((n.x + n.width for n in nodes), default=x_offset)
    return nodes, all_ports, content_right, bus_plan, gaps


def refine_place_nodes_for_gnd(
    node_specs: list[NodeSpec],
    by_col: dict[int, list[NodeSpec]],
    max_col: int,
    gaps: list[float],
    *,
    gnd_bus_y: float,
    x_offset: float = MARGIN,
    col_gap: float = COL_GAP,
    y_assign: dict[str, float] | None = None,
) -> tuple[list[TopologyNode], list[TopologyPort], float, BusPlan, list[float]]:
    """Re-plan signal buses with GND trunks; re-place only if gaps must widen."""
    columns = _columns_from_by_col(by_col)
    col_x = _column_x_positions(x_offset, gaps, max_col)
    nodes, all_ports = _place_columns(
        node_specs,
        by_col,
        max_col,
        col_x,
        columns=columns,
        y_assign=y_assign,
    )
    by_net = group_ports_by_net(all_ports)
    gnd_ports = by_net.get(GND_NET, [])
    bus_plan = plan_signal_buses(
        by_net,
        gnd_ports=gnd_ports,
        gnd_bus_y=gnd_bus_y,
        obstacles=nodes,
    )
    refined_gaps = _required_gaps(
        all_ports,
        col_x,
        max_col,
        base_gap=col_gap,
        bus_plan=bus_plan,
        gnd_bus_y=gnd_bus_y,
    )
    if any(refined_gaps[g] > gaps[g] + WIRE_EPS for g in range(max_col)):
        gaps = [max(gaps[g], refined_gaps[g]) for g in range(max_col)]
        col_x = _column_x_positions(x_offset, gaps, max_col)
        nodes, all_ports = _place_columns(
            node_specs,
            by_col,
            max_col,
            col_x,
            columns=columns,
            y_assign=y_assign,
        )
        by_net = group_ports_by_net(all_ports)
        gnd_ports = by_net.get(GND_NET, [])
        bus_plan = plan_signal_buses(
            by_net,
            gnd_ports=gnd_ports,
            gnd_bus_y=gnd_bus_y,
            obstacles=nodes,
        )
    content_right = max((n.x + n.width for n in nodes), default=x_offset)
    return nodes, all_ports, content_right, bus_plan, gaps
