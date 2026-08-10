"""Build a complete topology model from simulation metadata."""

from __future__ import annotations

from fypa.topology.constants import (
    CANVAS_HEIGHT_PAD_GND,
    CANVAS_HEIGHT_PAD_NO_GND,
    GND_SYMBOL_BELOW,
    LEGEND_BELOW_BUS,
    MARGIN,
)
from fypa.topology.geometry import (
    compute_schematic_geometry,
    parse_wire_path,
    points_to_path_d,
)
from fypa.topology.labels import finalize_wire_labels
from fypa.topology.layout import build_node_layout
from fypa.topology.metadata.feeds import external_feed_wires
from fypa.topology.metadata_schema import TopologyMetadata
from fypa.topology.routing import build_wires
from fypa.topology.types import TopologyModel, TopologyNode, TopologyWire


def _shift_topology_geometry(
    nodes: list[TopologyNode],
    wires: list[TopologyWire],
    *,
    dx: float,
    dy: float,
    gnd_bus_y: float | None,
    gnd_symbol_x: float | None,
) -> tuple[float | None, float | None]:
    """Translate nodes, ports, wires, and GND anchors by ``(dx, dy)``."""
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return gnd_bus_y, gnd_symbol_x
    for node in nodes:
        node.x += dx
        node.y += dy
        node.bounds = (node.x, node.y, node.width, node.height)
        for port in node.ports:
            port.x += dx
            port.y += dy
            if port.wire_x is not None:
                port.wire_x += dx
    for wire in wires:
        pts = [(x + dx, y + dy) for x, y in parse_wire_path(wire.path_d)]
        wire.path_d = points_to_path_d(pts)
        if wire.bus_x is not None:
            wire.bus_x += dx
        if wire.label_x or wire.label_y:
            wire.label_x += dx
            wire.label_y += dy
        if wire.label_has_leader:
            wire.label_leader_x += dx
            wire.label_leader_y += dy
    if gnd_bus_y is not None:
        gnd_bus_y += dy
    if gnd_symbol_x is not None:
        gnd_symbol_x += dx
    return gnd_bus_y, gnd_symbol_x


def _fit_canvas_to_content(
    nodes: list[TopologyNode],
    wires: list[TopologyWire],
    *,
    gnd_bus_y: float | None,
    gnd_symbol_x: float | None,
    needs_gnd: bool,
) -> tuple[float, float, float | None, float | None]:
    """Shift geometry into the margin box and return ``(width, height, gnd_y, gnd_x)``.

    Detours may route above y=0 or left of the margin; the SVG viewBox is
    ``0 0 width height``, so content must be translated in rather than clipped.
    """
    xs: list[float] = []
    ys: list[float] = []
    for node in nodes:
        xs.extend((node.x, node.x + node.width))
        ys.extend((node.y, node.y + node.height))
        for port in node.ports:
            xs.append(port.x)
            ys.append(port.y)
    for wire in wires:
        for x, y in parse_wire_path(wire.path_d):
            xs.append(x)
            ys.append(y)
        if wire.label_x or wire.label_y:
            xs.append(wire.label_x)
            ys.append(wire.label_y)
    if gnd_symbol_x is not None:
        xs.append(gnd_symbol_x)
    if gnd_bus_y is not None:
        ys.append(gnd_bus_y)
        ys.append(gnd_bus_y + GND_SYMBOL_BELOW)

    if not xs or not ys:
        return 400.0, 200.0, gnd_bus_y, gnd_symbol_x

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    dx = MARGIN - min_x if min_x < MARGIN else 0.0
    dy = MARGIN - min_y if min_y < MARGIN else 0.0
    gnd_bus_y, gnd_symbol_x = _shift_topology_geometry(
        nodes,
        wires,
        dx=dx,
        dy=dy,
        gnd_bus_y=gnd_bus_y,
        gnd_symbol_x=gnd_symbol_x,
    )
    max_x += dx
    max_y += dy
    width = max_x + MARGIN
    if needs_gnd and gnd_bus_y is not None:
        height = max(
            max_y + MARGIN,
            gnd_bus_y + GND_SYMBOL_BELOW + LEGEND_BELOW_BUS + CANVAS_HEIGHT_PAD_GND,
        )
    else:
        height = max_y + MARGIN + CANVAS_HEIGHT_PAD_NO_GND
    return width, height, gnd_bus_y, gnd_symbol_x


def build_topology_model(
    metadata: TopologyMetadata | None,
    *,
    use_schematic_layout: bool = False,
) -> TopologyModel:
    """Build a Flow diagram layout model for the PDN simulation schematic.

    Graph layout is primary. *use_schematic_layout* optionally seeds
    within-column order from Altium SchDoc placement when coverage allows.
    """
    if metadata is None:
        return TopologyModel()

    layout = build_node_layout(
        metadata, use_schematic_layout=use_schematic_layout,
    )

    wires, gnd_symbol_x = build_wires(
        layout.ports,
        gnd_bus_y=layout.gnd_bus_y,
        obstacles=layout.directive_nodes,
        bus_plan=layout.bus_plan,
    )

    wires.extend(
        external_feed_wires(layout.ports, layout.driven_nets, layout.net_to_rail),
    )
    geo = compute_schematic_geometry(
        wires,
        gnd_symbol_x=gnd_symbol_x,
        gnd_bus_y=layout.gnd_bus_y,
    )
    finalize_wire_labels(wires, nodes=layout.directive_nodes, geo=geo)

    width, height, gnd_bus_y, gnd_symbol_x = _fit_canvas_to_content(
        layout.nodes,
        wires,
        gnd_bus_y=layout.gnd_bus_y,
        gnd_symbol_x=gnd_symbol_x,
        needs_gnd=layout.needs_gnd,
    )

    return TopologyModel(
        nodes=layout.nodes,
        wires=wires,
        width=width,
        height=height,
        gnd_bus_y=gnd_bus_y,
        gnd_symbol_x=gnd_symbol_x,
    )
