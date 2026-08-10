"""Seed topology columns/order/port sides from Altium schematic placement."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from fypa.topology.metadata.specs import spec_has_series_role
from fypa.topology.metadata_schema import NodeSpec, PortDef, TopologyMetadata

# Fraction of directive nodes that must carry sch_x/sch_y before seeding.
SCHEMATIC_SEED_COVERAGE = 0.5


@dataclass(frozen=True)
class SchematicSeed:
    """Discrete layout seed derived from schematic placement."""

    columns: dict[str, int]
    orders: dict[str, int]
    # Optional per-node replacement port_defs (side flips from orientation).
    port_defs: dict[str, list[PortDef]]


def node_has_sch_placement(spec: NodeSpec) -> bool:
    return "sch_x" in spec and "sch_y" in spec


def schematic_coverage(node_specs: list[NodeSpec]) -> float:
    if not node_specs:
        return 0.0
    n = sum(1 for s in node_specs if node_has_sch_placement(s))
    return n / len(node_specs)


def _sheet_key(spec: NodeSpec) -> str:
    """Stable sheet id: prefer full path, fall back to basename."""
    raw = str(spec.get("schdoc") or "").replace("\\", "/").strip().lower()
    return raw or "_"


def _sheet_basename(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def _order_sheets(
    sheet_keys: set[str],
    sheet_placements: list[dict] | None,
) -> list[str]:
    """Left-to-right sheet block order from sheet-symbol X, else by name."""
    xs_by_child: dict[str, list[float]] = defaultdict(list)
    for row in sheet_placements or []:
        fn = str(row.get("filename") or "").replace("\\", "/").strip().lower()
        if not fn:
            continue
        try:
            x = float(row["x"])
        except (KeyError, TypeError, ValueError):
            continue
        xs_by_child[fn].append(x)
        base = _sheet_basename(fn)
        if base != fn:
            xs_by_child[base].append(x)

    def sort_key(name: str) -> tuple:
        xs = xs_by_child.get(name) or xs_by_child.get(_sheet_basename(name))
        if xs:
            return (0, min(xs), name)
        return (1, 0.0, name)

    return sorted(sheet_keys, key=sort_key)


def _cluster_xs_to_local_columns(xs: list[float]) -> dict[float, int]:
    """Map distinct X values to 0-based column indices via gap clustering."""
    uniq = sorted(set(xs))
    if not uniq:
        return {}
    if len(uniq) == 1:
        return {uniq[0]: 0}
    diffs = [uniq[i + 1] - uniq[i] for i in range(len(uniq) - 1)]
    # Split on gaps larger than the typical neighbour spacing. With only two
    # positions the sole gap always opens a new column.
    if len(diffs) == 1:
        thr = diffs[0] * 0.5
    else:
        med = sorted(diffs)[len(diffs) // 2]
        thr = max(med * 1.25, abs(uniq[-1] - uniq[0]) * 0.02, 1.0)
    col = 0
    out = {uniq[0]: 0}
    for i in range(1, len(uniq)):
        if uniq[i] - uniq[i - 1] > thr:
            col += 1
        out[uniq[i]] = col
    return out


def flip_port_defs(port_defs: list[PortDef]) -> list[PortDef]:
    return [
        (pname, "right" if side == "left" else "left" if side == "right" else side, sk)
        for pname, side, sk in port_defs
    ]


def _flip_non_series_port_defs(spec: NodeSpec) -> list[PortDef]:
    """Flip L/R for non-SERIES ports; leave SERIES/RESISTOR faces unchanged."""
    roles = spec.get("port_roles") or {}
    out: list[PortDef] = []
    for pname, side, sk in spec["port_defs"]:
        role = roles.get(pname, spec.get("role", ""))
        if role in ("SERIES", "RESISTOR"):
            out.append((pname, side, sk))
            continue
        new_side = (
            "right" if side == "left" else "left" if side == "right" else side
        )
        out.append((pname, new_side, sk))
    return out


def should_flip_lr(orientation_deg: int, mirrored: bool) -> bool:
    """Whether left/right port faces should swap for this symbol placement."""
    flip = bool(mirrored)
    deg = int(orientation_deg) % 360
    if deg in (90, 180, 270):
        flip = not flip
    return flip


def schematic_seed_placement(
    node_specs: list[NodeSpec],
    *,
    metadata: TopologyMetadata | None = None,
    graph_columns: dict[str, int] | None = None,
    min_coverage: float = SCHEMATIC_SEED_COVERAGE,
) -> SchematicSeed | None:
    """Build a column/order seed from ``sch_x``/``sch_y`` when coverage is enough.

    Returns ``None`` when too few nodes carry schematic coordinates (caller
    keeps graph ``assign_columns``).
    """
    if schematic_coverage(node_specs) < min_coverage:
        return None

    sheet_placements = None
    if metadata is not None:
        sheet_placements = list(metadata.get("sch_sheet_placements") or [])

    by_sheet: dict[str, list[NodeSpec]] = defaultdict(list)
    for spec in node_specs:
        if node_has_sch_placement(spec):
            by_sheet[_sheet_key(spec)].append(spec)

    sheet_order = _order_sheets(set(by_sheet.keys()), sheet_placements)

    columns: dict[str, int] = {}
    orders: dict[str, int] = {}
    port_defs: dict[str, list[PortDef]] = {}
    col_offset = 0

    for sheet in sheet_order:
        group = by_sheet[sheet]
        xs = [float(s["sch_x"]) for s in group]
        local_map = _cluster_xs_to_local_columns(xs)
        # Per local column: order by descending sch_y (Altium Y grows upward).
        members: dict[int, list[NodeSpec]] = defaultdict(list)
        for spec in group:
            local = local_map[float(spec["sch_x"])]
            members[local].append(spec)
        n_local = max(members.keys(), default=-1) + 1
        for local in range(n_local):
            ranked = sorted(
                members.get(local, []),
                key=lambda s: (-float(s["sch_y"]), s["node_id"]),
            )
            global_col = col_offset + local
            for order, spec in enumerate(ranked):
                nid = spec["node_id"]
                columns[nid] = global_col
                orders[nid] = order
                orient = int(spec.get("sch_orientation_deg") or 0)
                mirrored = bool(spec.get("sch_mirrored") or False)
                if not should_flip_lr(orient, mirrored):
                    continue
                # Pure SERIES/RESISTOR: faces come from column re-orient later.
                if (
                    spec_has_series_role(spec)
                    and not spec.get("sections")
                ):
                    continue
                if spec.get("sections") or spec_has_series_role(spec):
                    port_defs[nid] = _flip_non_series_port_defs(spec)
                else:
                    port_defs[nid] = flip_port_defs(list(spec["port_defs"]))
        col_offset += max(n_local, 1)

    # Nodes without schematic XY: hang off the right using graph columns.
    missing = [s for s in node_specs if s["node_id"] not in columns]
    if missing and graph_columns:
        gcols = sorted({graph_columns.get(s["node_id"], 0) for s in missing})
        remap = {gc: col_offset + i for i, gc in enumerate(gcols)}
        by_gcol: dict[int, list[NodeSpec]] = defaultdict(list)
        for spec in missing:
            by_gcol[graph_columns.get(spec["node_id"], 0)].append(spec)
        for gc in gcols:
            ranked = sorted(by_gcol[gc], key=lambda s: s["node_id"])
            for order, spec in enumerate(ranked):
                columns[spec["node_id"]] = remap[gc]
                orders[spec["node_id"]] = order
    elif missing:
        for order, spec in enumerate(sorted(missing, key=lambda s: s["node_id"])):
            columns[spec["node_id"]] = col_offset
            orders[spec["node_id"]] = order

    return SchematicSeed(columns=columns, orders=orders, port_defs=port_defs)


def y_assign_from_orders(
    node_specs: list[NodeSpec],
    columns: dict[str, int],
    orders: dict[str, int],
    heights: dict[str, float],
    *,
    margin: float,
    row_gap: float,
) -> dict[str, float]:
    """Stack nodes top-to-bottom within each column by ``orders``."""
    by_col: dict[int, list[str]] = defaultdict(list)
    for spec in node_specs:
        nid = spec["node_id"]
        by_col[columns.get(nid, 0)].append(nid)
    y_assign: dict[str, float] = {}
    for _col, nids in by_col.items():
        ranked = sorted(nids, key=lambda n: (orders.get(n, 0), n))
        y = float(margin)
        for nid in ranked:
            y_assign[nid] = y
            y += heights.get(nid, 60.0) + row_gap
    return y_assign
