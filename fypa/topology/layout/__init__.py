"""Node column layout and port placement for the topology schematic."""

from __future__ import annotations

from copy import deepcopy

from fypa.topology.constants import GND_BUS_BELOW, MARGIN, ROW_GAP
from fypa.topology.layout.columns import place_nodes, refine_place_nodes_for_gnd
from fypa.topology.layout.stubs import assign_edge_wire_columns, assign_stacked_stub_lengths
from fypa.topology.layout.vertical_align import _spec_layout_height
from fypa.topology.layout_result import LayoutResult
from fypa.topology.metadata.layout_bridge import (
    orient_ports_toward_peers,
    orient_series_ports_for_columns,
    parse_topology_directives,
    specs_by_column,
)
from fypa.topology.metadata.schematic_seed import (
    flip_port_defs,
    schematic_seed_placement,
    should_flip_lr,
    y_assign_from_orders,
)
from fypa.topology.metadata.specs import spec_has_series_role
from fypa.topology.metadata_schema import TopologyMetadata
from fypa.topology.placement import BusPlan

__all__ = [
    "assign_edge_wire_columns",
    "assign_stacked_stub_lengths",
    "build_node_layout",
    "place_nodes",
    "refine_place_nodes_for_gnd",
]


def build_node_layout(
    metadata: TopologyMetadata | None,
    *,
    use_schematic_layout: bool = False,
) -> LayoutResult:
    """Parse metadata and place nodes; returns layout state for wire routing.

    Graph column packing and peer-facing ports are the primary layout path.
    When *use_schematic_layout* is true and coverage allows, SchDoc coordinates
    may refine within-column order (experimental hint only).
    """
    empty = LayoutResult(
        nodes=[],
        ports=[],
        content_right=MARGIN,
        max_col=0,
        needs_gnd=False,
        gnd_bus_y=None,
        directive_nodes=[],
        node_specs=[],
        net_to_rail={},
        driven_nets=set(),
        bus_plan=BusPlan(),
    )
    if metadata is None:
        return empty

    parsed = parse_topology_directives(metadata)
    node_specs = parsed.node_specs
    columns = parsed.columns
    y_override: dict[str, float] | None = None
    seed = None

    if use_schematic_layout:
        seed = schematic_seed_placement(
            node_specs,
            metadata=metadata,
            graph_columns=parsed.columns,
        )
        if seed is not None:
            columns = seed.columns
            node_specs = deepcopy(node_specs)
            if seed.port_defs:
                for spec in node_specs:
                    nid = spec["node_id"]
                    if nid in seed.port_defs:
                        spec["port_defs"] = seed.port_defs[nid]
                        for sec in spec.get("sections") or []:
                            if sec.get("role") in ("SERIES", "RESISTOR"):
                                continue
                            sec["port_defs"] = flip_port_defs(
                                list(sec.get("port_defs") or []),
                            )
            # Re-orient for the seeded column map; peer faces win over SchDoc
            # mirror flips so flow direction stays consistent.
            orient_series_ports_for_columns(
                node_specs, columns, parsed.net_to_rail,
            )
            for spec in node_specs:
                if not spec_has_series_role(spec):
                    continue
                if "sch_x" not in spec:
                    continue
                if not should_flip_lr(
                    int(spec.get("sch_orientation_deg") or 0),
                    bool(spec.get("sch_mirrored") or False),
                ):
                    continue
                spec["port_defs"] = flip_port_defs(list(spec["port_defs"]))
                for sec in spec.get("sections") or []:
                    if sec.get("role") in ("SERIES", "RESISTOR"):
                        sec["port_defs"] = flip_port_defs(
                            list(sec.get("port_defs") or []),
                        )
            orient_ports_toward_peers(node_specs, columns, parsed.net_to_rail)
            heights = {s["node_id"]: _spec_layout_height(s) for s in node_specs}
            y_override = y_assign_from_orders(
                node_specs,
                columns,
                seed.orders,
                heights,
                margin=MARGIN,
                row_gap=ROW_GAP,
            )

    by_col, max_col = specs_by_column(node_specs, columns)
    # Keep within-column list order consistent with schematic orders when seeded.
    if seed is not None:
        for col, specs in list(by_col.items()):
            by_col[col] = sorted(
                specs,
                key=lambda s: (seed.orders.get(s["node_id"], 0), s["node_id"]),
            )

    nodes, all_ports, content_right, bus_plan, gaps = place_nodes(
        node_specs,
        by_col=by_col,
        max_col=max_col,
        y_assign=y_override,
    )

    directive_nodes = [n for n in nodes if n.role != "GND"]
    directive_bottom = max((n.y + n.height for n in directive_nodes), default=MARGIN)
    gnd_bus_y = directive_bottom + GND_BUS_BELOW if parsed.needs_gnd else None

    if parsed.needs_gnd and gnd_bus_y is not None:
        nodes, all_ports, content_right, bus_plan, gaps = refine_place_nodes_for_gnd(
            node_specs,
            by_col=by_col,
            max_col=max_col,
            gaps=gaps,
            gnd_bus_y=gnd_bus_y,
            y_assign=y_override,
        )
        directive_nodes = [n for n in nodes if n.role != "GND"]
        directive_bottom = max((n.y + n.height for n in directive_nodes), default=MARGIN)
        gnd_bus_y = directive_bottom + GND_BUS_BELOW

    return LayoutResult(
        nodes=nodes,
        ports=all_ports,
        content_right=content_right,
        max_col=max_col,
        needs_gnd=parsed.needs_gnd,
        gnd_bus_y=gnd_bus_y,
        directive_nodes=directive_nodes,
        node_specs=node_specs,
        net_to_rail=parsed.net_to_rail,
        driven_nets=parsed.driven_nets,
        bus_plan=bus_plan,
    )
