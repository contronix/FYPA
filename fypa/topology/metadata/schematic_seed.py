"""Seed topology columns/order/port sides from Altium schematic placement.

Schematic coordinates are a **soft hint**: they refine within-column order and
optional left/right ranking inside a sheet, but they do not allocate an exclusive
column block per subsheet. Graph column *ranks* supply left→right order, then
ranks are compressed into at most ``SCHEMATIC_MAX_COLUMNS`` so sparse chains do
not dominate the diagram width. SOURCE/SINK stay on the outer columns.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from fypa.topology.metadata.specs import spec_has_series_role
from fypa.topology.metadata_schema import NodeSpec, PortDef, TopologyMetadata

# Fraction of directive nodes that must carry sch_x/sch_y before seeding.
SCHEMATIC_SEED_COVERAGE = 0.5
# Soft cap on column count in schematic mode. Graph assign_columns can open a
# long sparse chain (one SERIES per column); schematic packing collapses that
# into a readable shared width while keeping left→right flow order.
SCHEMATIC_MAX_COLUMNS = 4


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
    """Left-to-right sheet preference from sheet-symbol X, else by name.

    Used only as a within-column tie-break (not as a column-block offset).
    """
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


def _compact_columns(columns: dict[str, int]) -> dict[str, int]:
    """Remap used column indices to a dense 0..n-1 range."""
    used = sorted(set(columns.values()))
    if not used:
        return columns
    remap = {old: i for i, old in enumerate(used)}
    return {nid: remap[c] for nid, c in columns.items()}


def _compress_column_ranks(
    columns: dict[str, int],
    max_cols: int,
) -> dict[str, int]:
    """Bucket ordered column ranks into at most *max_cols* shared columns.

    Preserves left-to-right order of the backbone ranks while eliminating the
    sparse one-node-per-column chains that make large boards unreadably wide.
    """
    if max_cols < 1:
        return _compact_columns(columns)
    used = sorted(set(columns.values()))
    if len(used) <= max_cols:
        return _compact_columns(columns)
    n = len(used)
    remap = {
        old: int(round(i / (n - 1) * (max_cols - 1)))
        for i, old in enumerate(used)
    }
    return {nid: remap[c] for nid, c in columns.items()}


def _apply_source_sink_columns(
    node_specs: list[NodeSpec],
    columns: dict[str, int],
) -> dict[str, int]:
    """Prefer SOURCE in column 0 and SINK in the rightmost occupied column."""
    out = dict(columns)
    for spec in node_specs:
        if spec["role"] == "SOURCE":
            out[spec["node_id"]] = 0

    non_sink_cols = [
        out[s["node_id"]]
        for s in node_specs
        if s["role"] != "SINK" and s["node_id"] in out
    ]
    has_source = any(s["role"] == "SOURCE" for s in node_specs)
    has_sink = any(s["role"] == "SINK" for s in node_specs)
    others_max = max(non_sink_cols, default=0)
    if has_source and has_sink:
        sink_col = max(others_max, 1)
    else:
        sink_col = others_max
    for spec in node_specs:
        if spec["role"] == "SINK":
            out[spec["node_id"]] = sink_col
    return _compact_columns(out)


def _shared_width(
    max_local: int,
    node_specs: list[NodeSpec],
) -> int:
    """Column count for the shared sheet-packing space (no-graph path)."""
    w = max(max_local, 1)
    roles = {s["role"] for s in node_specs}
    if "SOURCE" in roles and "SINK" in roles:
        w = max(w, 2)
    return min(w, SCHEMATIC_MAX_COLUMNS)


def _map_local_to_shared(local: int, n_local: int, width: int) -> int:
    """Map a sheet-local column into the shared 0..width-1 range."""
    if width <= 1 or n_local <= 1:
        return 0
    return int(round(local / (n_local - 1) * (width - 1)))


def schematic_seed_placement(
    node_specs: list[NodeSpec],
    *,
    metadata: TopologyMetadata | None = None,
    graph_columns: dict[str, int] | None = None,
    min_coverage: float = SCHEMATIC_SEED_COVERAGE,
) -> SchematicSeed | None:
    """Build a column/order seed from ``sch_x``/``sch_y`` when coverage is enough.

    Schematic placement is a soft hint:

    * Subsheets **share** one column space (no exclusive block per sheet).
    * When ``graph_columns`` is provided it supplies left→right *order*; ranks
      are then compressed into at most :data:`SCHEMATIC_MAX_COLUMNS` so sparse
      one-node graph columns do not dominate the width.
    * Without graph columns, sheet-local X ranks pack into a shared width.
    * SOURCE nodes prefer column 0; SINK nodes prefer the last column.
    * Within a column, order follows schematic Y (and sheet-symbol X as tie-break).

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
    sheet_rank = {name: i for i, name in enumerate(sheet_order)}

    # Per-node sheet-local column and sheet size (for soft X → shared map).
    local_col: dict[str, int] = {}
    local_count: dict[str, int] = {}
    max_local = 1
    for sheet, group in by_sheet.items():
        xs = [float(s["sch_x"]) for s in group]
        x_map = _cluster_xs_to_local_columns(xs)
        n_local = max(x_map.values(), default=-1) + 1
        max_local = max(max_local, n_local)
        for spec in group:
            nid = spec["node_id"]
            local_col[nid] = x_map[float(spec["sch_x"])]
            local_count[nid] = n_local

    width = _shared_width(max_local, node_specs)

    # Backbone: graph column *ranks* when present (order only — width is capped
    # below). Without graph columns, pack every sheet into one shared range.
    columns: dict[str, int] = {}
    if graph_columns:
        for spec in node_specs:
            columns[spec["node_id"]] = int(graph_columns.get(spec["node_id"], 0))
    else:
        for spec in node_specs:
            nid = spec["node_id"]
            if nid not in local_col:
                continue
            n_local = local_count[nid]
            if n_local < 2:
                # Single cluster — park mid-band; SOURCE/SINK overrides apply later.
                columns[nid] = width // 2
            else:
                columns[nid] = _map_local_to_shared(local_col[nid], n_local, width)

    # Nodes without schematic XY: keep graph column or hang at the right edge.
    missing = [s for s in node_specs if s["node_id"] not in columns]
    if missing and graph_columns:
        for spec in missing:
            columns[spec["node_id"]] = int(graph_columns.get(spec["node_id"], 0))
    elif missing:
        right = max(columns.values(), default=0)
        for spec in missing:
            columns[spec["node_id"]] = right

    columns = _compress_column_ranks(columns, SCHEMATIC_MAX_COLUMNS)
    columns = _apply_source_sink_columns(node_specs, columns)

    # Within-column order: schematic Y (Altium Y up), then sheet rank, then id.
    orders: dict[str, int] = {}
    by_col: dict[int, list[NodeSpec]] = defaultdict(list)
    for spec in node_specs:
        by_col[columns[spec["node_id"]]].append(spec)
    for _col, members in by_col.items():
        ranked = sorted(
            members,
            key=lambda s: (
                -float(s["sch_y"]) if node_has_sch_placement(s) else 0.0,
                sheet_rank.get(_sheet_key(s), 10**9),
                s["node_id"],
            ),
        )
        for order, spec in enumerate(ranked):
            orders[spec["node_id"]] = order

    port_defs: dict[str, list[PortDef]] = {}
    for spec in node_specs:
        if not node_has_sch_placement(spec):
            continue
        nid = spec["node_id"]
        orient = int(spec.get("sch_orientation_deg") or 0)
        mirrored = bool(spec.get("sch_mirrored") or False)
        if not should_flip_lr(orient, mirrored):
            continue
        # Pure SERIES/RESISTOR: faces come from column re-orient later.
        if spec_has_series_role(spec) and not spec.get("sections"):
            continue
        if spec.get("sections") or spec_has_series_role(spec):
            port_defs[nid] = _flip_non_series_port_defs(spec)
        else:
            port_defs[nid] = flip_port_defs(list(spec["port_defs"]))

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
