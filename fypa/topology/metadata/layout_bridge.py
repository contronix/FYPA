"""Facade between topology metadata and node layout."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from fypa.topology.constants import GND_NET, RETURN_PORT_SORT_BASE
from fypa.topology.metadata.nets import (
    canonical_net,
    is_ideal_return,
    net_to_rail_map,
    port_display_net,
    terminal_net,
    wire_net,
)
from fypa.topology.metadata.specs import (
    directives_to_component_specs,
    driven_power_nets,
    jump_row_for_directive,
    natural_sort_key,
    spec_has_role,
    spec_has_series_role,
    spec_port_role,
    spec_series_terms,
)
from fypa.topology.metadata.tooltips import port_tooltip
from fypa.topology.metadata_schema import NodeSpec, TerminalDict, TopologyMetadata
from fypa.topology.terminal_roles import is_output_port
from fypa.topology.util import truncate_label


@dataclass(frozen=True)
class ResolvedPort:
    wnet: str
    plabel: str
    tooltip: str


@dataclass(frozen=True)
class ParsedLayoutInput:
    node_specs: list[NodeSpec]
    net_to_rail: dict[str, str]
    driven_nets: set[str]
    needs_gnd: bool
    columns: dict[str, int]


def _column_flow_net(term: TerminalDict | None) -> str | None:
    """Physical net for column placement (GND collapsed; no rail-group merge).

    Rail merging is for the solver/viewer dropdown — using it here creates
    feedback cycles when SERIES bridges join upstream and downstream nets
    (e.g. VDD_3V3 ↔ VDD_IMU) onto one canonical name.
    """
    if not term or is_ideal_return(term):
        return None
    return wire_net(terminal_net(term))


def _compact_columns(col: dict[str, int]) -> dict[str, int]:
    """Remap sparse column indices to a dense 0..n-1 range (no empty columns)."""
    if not col:
        return col
    order = sorted(set(col.values()))
    remap = {old: new for new, old in enumerate(order)}
    return {nid: remap[c] for nid, c in col.items()}


# Soft safety cap after connectivity packing. Long SERIES chains otherwise open
# one column per hop; ranks stay ordered left→right when compressed.
GRAPH_LAYOUT_MAX_COLUMNS = 8


def compress_column_ranks(
    columns: dict[str, int],
    max_cols: int,
) -> dict[str, int]:
    """Bucket ordered column ranks into at most *max_cols* shared columns."""
    if max_cols < 1 or not columns:
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


def _column_roles(
    node_specs: list[NodeSpec],
    col: dict[str, int],
) -> dict[int, set[str]]:
    by_col: dict[int, set[str]] = defaultdict(set)
    for s in node_specs:
        by_col[col.get(s["node_id"], 0)].add(s["role"])
    return by_col


def _merge_adjacent_passive_columns(
    node_specs: list[NodeSpec],
    col: dict[str, int],
    loop_parent: dict[str, str] | None = None,
) -> dict[str, int]:
    """Merge consecutive columns that contain only SERIES/RESISTOR nodes.

    Hop-depth assignment often isolates each fuse/ferrite in its own column;
    sharing those columns keeps the diagram dense without SchDoc hints.
    Loop parent/child SERIES stay in separate columns.
    """
    out = dict(col)
    passive = {"SERIES", "RESISTOR"}
    loop_ids: set[str] = set()
    if loop_parent:
        loop_ids = set(loop_parent) | set(loop_parent.values())

    def _col_has_loop(c: int) -> bool:
        return any(nid in loop_ids for nid, cc in out.items() if cc == c)

    while True:
        roles = _column_roles(node_specs, out)
        used = sorted(roles)
        merged = False
        for i in range(1, len(used)):
            left, right = used[i - 1], used[i]
            if not roles[left] or not roles[right]:
                continue
            if roles[left] <= passive and roles[right] <= passive:
                if _col_has_loop(left) or _col_has_loop(right):
                    continue
                for nid, c in list(out.items()):
                    if c == right:
                        out[nid] = left
                out = _compact_columns(out)
                merged = True
                break
        if not merged:
            break
    return out


def _pin_source_sink_columns(
    node_specs: list[NodeSpec],
    col: dict[str, int],
    mixed_role_ids: set[str],
) -> dict[str, int]:
    """SOURCE → column 0; pure SINK → rightmost occupied column."""
    out = dict(col)
    for s in node_specs:
        if s["role"] == "SOURCE":
            out[s["node_id"]] = 0
    has_source = any(s["role"] == "SOURCE" for s in node_specs)
    has_sink = any(
        s["role"] == "SINK" and s["node_id"] not in mixed_role_ids
        for s in node_specs
    )
    sink_col = max(out.values(), default=0)
    if has_source and has_sink and sink_col == 0:
        sink_col = 1
    for s in node_specs:
        if s["role"] == "SINK" and s["node_id"] not in mixed_role_ids:
            out[s["node_id"]] = sink_col
    return _compact_columns(out)


def _has_source_rail_p_input(
    spec: NodeSpec,
    source_ids: set[str],
    outputs_by_net: dict[str, list[str]],
) -> bool:
    """True when a series P port is fed directly from a SOURCE output."""
    for pname, term in spec_series_terms(spec):
        if not pname.startswith("P") or not term or is_ideal_return(term):
            continue
        flow_net = _column_flow_net(term)
        if not flow_net:
            continue
        for pid in outputs_by_net.get(flow_net, []):
            if pid in source_ids:
                return True
    return False


def _resolve_mutual_loop_parents(
    loop_parent: dict[str, str],
    node_specs: list[NodeSpec],
    source_ids: set[str],
    outputs_by_net: dict[str, list[str]],
) -> dict[str, str]:
    """Break A↔B mutual loops: keep the source-rail bridge as parent."""
    spec_by_id = {s["node_id"]: s for s in node_specs}
    resolved = dict(loop_parent)
    for child, parent in list(loop_parent.items()):
        if resolved.get(parent) != child:
            continue
        child_spec = spec_by_id[child]
        parent_spec = spec_by_id[parent]
        child_src = _has_source_rail_p_input(child_spec, source_ids, outputs_by_net)
        parent_src = _has_source_rail_p_input(parent_spec, source_ids, outputs_by_net)
        # Pick the loop root (the node that keeps no parent link) symmetrically,
        # so both (child, parent) and (parent, child) iterations agree on it: the
        # source-rail bridge wins, else the lexicographically smaller node id as
        # an arbitrary-but-deterministic tie-break. ``pop`` is idempotent, so the
        # second direction of the mutual pair is a harmless no-op rather than a
        # KeyError on an already-removed key.
        if child_src and not parent_src:
            root = child
        elif parent_src and not child_src:
            root = parent
        else:
            root = min(child, parent)
        resolved.pop(root, None)
    return resolved


def _detect_loop_series_parents(
    node_specs: list[NodeSpec],
    outputs_by_net: dict[str, list[str]],
    inputs_by_net: dict[str, list[str]],
) -> dict[str, str]:
    """Map loop-child SERIES/RESISTOR node_id -> parent node_id.

    A loop child receives on P ports from the parent's N outputs and drives
    back into the parent's P inputs (e.g. J7 relative to U1).
    """
    loop_parent: dict[str, str] = {}
    spec_by_id = {s["node_id"]: s for s in node_specs}
    for s in node_specs:
        if not spec_has_series_role(s):
            continue
        child_id = s["node_id"]
        for parent_id, parent in spec_by_id.items():
            if parent_id == child_id:
                continue
            from_parent = False
            to_parent = False
            for pname, term in spec_series_terms(s):
                if not term or is_ideal_return(term):
                    continue
                flow_net = _column_flow_net(term)
                if not flow_net:
                    continue
                if pname.startswith("P"):
                    if parent_id in outputs_by_net.get(flow_net, []):
                        from_parent = True
                elif pname.startswith("N"):
                    if parent_id in inputs_by_net.get(flow_net, []):
                        to_parent = True
            if from_parent and to_parent:
                loop_parent[child_id] = parent_id
                break
    source_ids = {
        s["node_id"] for s in node_specs
        if spec_has_role(s, ("SOURCE", "REGULATOR"))
    }
    return _resolve_mutual_loop_parents(loop_parent, node_specs, source_ids, outputs_by_net)


def _passive_upstream_cols(
    spec: NodeSpec,
    nid: str,
    outputs_by_net: dict[str, list[str]],
    col: dict[str, int],
    loop_parent: dict[str, str],
) -> list[int]:
    """Column indices of nodes driving this passive's P-side inputs (excl. loop-back)."""
    upstream: list[int] = []
    for pname, term in spec_series_terms(spec):
        if not term or is_ideal_return(term) or not pname.startswith("P"):
            continue
        flow_net = _column_flow_net(term)
        if not flow_net:
            continue
        for pid in outputs_by_net.get(flow_net, []):
            if pid == nid:
                continue
            if loop_parent.get(pid) == nid:
                continue
            upstream.append(col.get(pid, 0))
    return upstream


def _passive_downstream_cols(
    spec: NodeSpec,
    nid: str,
    inputs_by_net: dict[str, list[str]],
    col: dict[str, int],
) -> list[int]:
    """Column indices of nodes fed from this passive's N-side outputs."""
    downstream: list[int] = []
    for pname, term in spec_series_terms(spec):
        if not term or is_ideal_return(term) or not pname.startswith("N"):
            continue
        flow_net = _column_flow_net(term)
        if not flow_net:
            continue
        for pid in inputs_by_net.get(flow_net, []):
            if pid != nid:
                downstream.append(col.get(pid, 0))
    return downstream


def _apply_passive_column_col(
    spec: NodeSpec,
    nid: str,
    col: dict[str, int],
    outputs_by_net: dict[str, list[str]],
    inputs_by_net: dict[str, list[str]],
    loop_parent: dict[str, str],
) -> None:
    """Place an inline passive from upstream/downstream peers (not loop children)."""
    if nid in loop_parent:
        return
    upstream_cols = _passive_upstream_cols(spec, nid, outputs_by_net, col, loop_parent)
    if upstream_cols:
        col[nid] = max(max(upstream_cols) + 1, col.get(nid, 0))
        return
    downstream_cols = _passive_downstream_cols(spec, nid, inputs_by_net, col)
    if downstream_cols:
        col[nid] = max(min(downstream_cols), col.get(nid, 0))


def _ensure_passives_right_of_upstream(
    node_specs: list[NodeSpec],
    col: dict[str, int],
    outputs_by_net: dict[str, list[str]],
    loop_parent: dict[str, str],
) -> None:
    """After rank compression, restore driver→load order for every SERIES.

    Compression may co-locate a SERIES driver with its SERIES load; the load's
    left port then faces away from the driver. Iterate until every passive sits
    strictly right of all P-side upstream drivers (any role).
    """
    changed = True
    guard = 0
    while changed and guard < len(node_specs) * 2 + 5:
        guard += 1
        changed = False
        for s in node_specs:
            if not spec_has_series_role(s):
                continue
            nid = s["node_id"]
            if nid in loop_parent:
                continue
            upstream = _passive_upstream_cols(
                s, nid, outputs_by_net, col, loop_parent,
            )
            if not upstream:
                continue
            need = max(upstream) + 1
            if col.get(nid, 0) < need:
                col[nid] = need
                changed = True


def _dedupe_port_rows_on_same_side(
    port_defs: list[tuple[str, str, int]],
) -> list[tuple[str, str, int]]:
    """Give each port on a symbol edge its own layout row (no overlapping circles).

    Channel ports on a side become contiguous ``0..n-1``. Return/GND ports keep
    keys in the ``RETURN_PORT_SORT_BASE`` band so ``port_layout_rows`` still
    stacks them below the channel block.
    """
    by_side: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for item in port_defs:
        by_side[item[1]].append(item)
    out: list[tuple[str, str, int]] = []
    for side in ("left", "right"):
        items = sorted(by_side.get(side, []), key=lambda t: (t[2], t[0]))
        channel = [t for t in items if not is_return_port_row(t[2])]
        returns = [t for t in items if is_return_port_row(t[2])]
        for row_i, (pname, s, _sk) in enumerate(channel):
            out.append((pname, s, row_i))
        for ret_i, (pname, s, _sk) in enumerate(returns):
            out.append((pname, s, RETURN_PORT_SORT_BASE + ret_i))
    for side, items in by_side.items():
        if side in ("left", "right"):
            continue
        out.extend(items)
    return out


def ensure_unique_port_rows(node_specs: list[NodeSpec]) -> None:
    """Hard invariant: no two ports share ``(side, sort_key)`` on one symbol."""
    for s in node_specs:
        s["port_defs"] = _dedupe_port_rows_on_same_side(s["port_defs"])
        for sec in s.get("sections") or []:
            defs = sec.get("port_defs")
            if defs:
                sec["port_defs"] = _dedupe_port_rows_on_same_side(defs)


def _child_facing_net_rows(spec: NodeSpec, face_side: str) -> dict[str, int]:
    """Layout row per flow net for channel ports on the parent-facing edge."""
    terms = spec.get("terms") or {}
    net_rows: dict[str, int] = {}
    face_channel = [
        (pname, sort_key)
        for pname, side, sort_key in spec["port_defs"]
        if side == face_side and pname.startswith(("P", "N"))
    ]
    face_channel.sort(key=lambda t: (t[1], t[0]))
    for row_i, (pname, _) in enumerate(face_channel):
        net = _column_flow_net(terms.get(pname))
        if net and net not in net_rows:
            net_rows[net] = row_i
    return net_rows


def _assign_face_port_rows(
    port_defs: list[tuple[str, str, int]],
    terms: dict[str, TerminalDict],
    face_side: str,
    net_row_hints: dict[str, int],
) -> list[tuple[str, str, int]]:
    """Unique rows on ``face_side``; loop nets share the child's row index."""
    out: list[tuple[str, str, int]] = []
    face_ports: list[tuple[str, str, int]] = []
    for item in port_defs:
        if item[1] == face_side:
            face_ports.append(item)
        else:
            out.append(item)

    assigned: dict[str, int] = {}
    used_rows: set[int] = set()
    hinted: list[tuple[str, str, int, str]] = []
    pending: list[tuple[str, str, int]] = []
    for pname, side, sort_key in face_ports:
        net = _column_flow_net(terms.get(pname))
        if net and net in net_row_hints:
            hinted.append((pname, side, sort_key, net))
        else:
            pending.append((pname, side, sort_key))

    for pname, _side, _sk, net in sorted(hinted, key=lambda t: (net_row_hints[t[3]], t[0])):
        row = net_row_hints[net]
        while row in used_rows:
            row += 1
        assigned[pname] = row
        used_rows.add(row)

    next_row = 0
    for pname, _side, _sk in sorted(pending, key=lambda t: (t[2], t[0])):
        while next_row in used_rows:
            next_row += 1
        assigned[pname] = next_row
        used_rows.add(next_row)
        next_row += 1

    for pname, side, _sk in face_ports:
        out.append((pname, side, assigned[pname]))
    return out


def _orient_loop_series_ports(
    node_specs: list[NodeSpec],
    col: dict[str, int],
    loop_parent: dict[str, str],
    outputs_by_net: dict[str, list[str]],
    inputs_by_net: dict[str, list[str]],
) -> None:
    """Loop child: all channel ports on the parent-facing side (one row each).

    Loop parent: N/P ports on nets shared with the child face the child column.
    """
    spec_by_id = {s["node_id"]: s for s in node_specs}
    loop_children: dict[str, list[str]] = defaultdict(list)
    for child_id, parent_id in loop_parent.items():
        loop_children[parent_id].append(child_id)

    for s in node_specs:
        # Multi-role composites lay out their rows from the per-section
        # port_defs, so rewriting the composite-level sides/sort keys here
        # would have no visual effect and would corrupt the section-offset
        # sort-key scheme. The single-role reorder below doesn't apply.
        if not spec_has_series_role(s) or s.get("sections"):
            continue
        nid = s["node_id"]
        if nid not in loop_parent:
            continue
        parent_col = col.get(loop_parent[nid], 0)
        child_col = col.get(nid, 0)
        if parent_col < child_col:
            face = "left"
        elif parent_col > child_col:
            face = "right"
        else:
            face = "left"
        channel_ports = [
            (pname, side, sort_key)
            for pname, side, sort_key in s["port_defs"]
            if pname.startswith(("P", "N"))
        ]
        other_ports = [
            (pname, side, sort_key)
            for pname, side, sort_key in s["port_defs"]
            if not pname.startswith(("P", "N"))
        ]
        channel_ports.sort(key=lambda t: (t[2], t[0]))
        s["port_defs"] = [
            (pname, face, row_i) for row_i, (pname, _side, _sk) in enumerate(channel_ports)
        ] + other_ports

    for s in node_specs:
        if not spec_has_series_role(s) or s.get("sections"):
            continue
        nid = s["node_id"]
        children = loop_children.get(nid)
        if not children:
            continue
        child_set = set(children)
        parent_col = col.get(nid, 0)
        child_col = min(col.get(c, parent_col) for c in children)
        if child_col > parent_col:
            face_child = "right"
        elif child_col < parent_col:
            face_child = "left"
        else:
            continue
        terms = s.get("terms") or {}
        flip_p: set[str] = set()
        flip_n: set[str] = set()
        for pname, term in terms.items():
            if not term or is_ideal_return(term):
                continue
            flow_net = _column_flow_net(term)
            if not flow_net:
                continue
            if pname.startswith("N"):
                if any(c in inputs_by_net.get(flow_net, []) for c in child_set):
                    flip_n.add(pname)
            elif pname.startswith("P"):
                if any(c in outputs_by_net.get(flow_net, []) for c in child_set):
                    flip_p.add(pname)
        if not flip_p and not flip_n:
            continue
        flipped = [
            (
                pname,
                face_child if pname in flip_p or pname in flip_n else side,
                sort_key,
            )
            for pname, side, sort_key in s["port_defs"]
        ]
        child_net_rows: dict[str, int] = {}
        for child_id in children:
            child_spec = spec_by_id[child_id]
            child_col = col.get(child_id, parent_col)
            if child_col > parent_col:
                child_face = "left"
            elif child_col < parent_col:
                child_face = "right"
            else:
                continue
            child_net_rows.update(_child_facing_net_rows(child_spec, child_face))
        if child_net_rows:
            s["port_defs"] = _assign_face_port_rows(
                flipped,
                terms,
                face_child,
                child_net_rows,
            )
        else:
            s["port_defs"] = _dedupe_port_rows_on_same_side(flipped)


def _column_net(
    role: str,
    term: TerminalDict | None,
    net_to_rail: dict[str, str],
    *,
    terminal: str = "",
) -> str | None:
    """Physical net key for the column-placement / flow graph.

    Always the copper net (:func:`_column_flow_net`), never ``net_to_rail``
    canonicalization. Net-ties and 0 Ω SERIES join two physical names that the
    solver may put in one rail group; collapsing them here creates feedback
    cycles and hides real upstream drivers (e.g. ``LX.1`` vs ``VDD_24V_IN``,
    ``VDD_MCU`` vs ``VDD_3V3``).

    ``role`` / ``net_to_rail`` / ``terminal`` are kept so call sites stay stable;
    rail grouping remains for the viewer dropdown and labels only.

    Port labels use :func:`~fypa.topology.metadata.nets.port_display_net`.
    """
    del role, net_to_rail, terminal
    return _column_flow_net(term)


def _push_passive_load_columns(
    node_specs: list[NodeSpec],
    col: dict[str, int],
    inputs_by_net: dict[str, list[str]],
    loop_parent: dict[str, str],
    role_by_id: dict[str, str],
    outputs_by_net: dict[str, list[str]],
) -> None:
    """Loads on a single-channel passive's N net sit one column right of the bridge."""
    for s in node_specs:
        if not spec_has_series_role(s):
            continue
        nid = s["node_id"]
        if nid in loop_parent:
            continue
        if not _passive_upstream_cols(s, nid, outputs_by_net, col, loop_parent):
            continue
        n_terms = [
            pname
            for pname, term in spec_series_terms(s)
            if pname.startswith("N") and term and not is_ideal_return(term)
        ]
        if len(n_terms) != 1:
            continue
        pcol = col.get(nid, 0)
        flow_net = _column_flow_net((s.get("terms") or {}).get(n_terms[0]))
        if not flow_net:
            continue
        for load_id in inputs_by_net.get(flow_net, []):
            if load_id == nid:
                continue
            if role_by_id.get(load_id) != "SINK":
                continue
            col[load_id] = max(col.get(load_id, 0), pcol + 1)


def _propagation_edges(
    node_specs: list[NodeSpec],
    outputs_by_net: dict[str, list[str]],
    inputs_by_net: dict[str, list[str]],
    net_to_rail: dict[str, str],
    loop_parent: dict[str, str],
) -> dict[str, list[str]]:
    """Directed edges ``nid -> other`` walked by the column-relaxation passes.

    Mirrors the edge traversal in :func:`assign_columns` exactly (output ports,
    flow-net resolution, GND/self/loop-parent skips) so cycle detection sees the
    same graph those passes walk.
    """
    edges: dict[str, list[str]] = defaultdict(list)
    for s in node_specs:
        nid = s["node_id"]
        for pname, side, _ in s["port_defs"]:
            port_role = spec_port_role(s, pname)
            if not is_output_port(port_role, pname, side):
                continue
            term = (s["terms"] or {}).get(pname)
            if is_ideal_return(term):
                continue
            flow_net = _column_net(port_role, term, net_to_rail, terminal=pname)
            if not flow_net or flow_net == GND_NET:
                continue
            for other in inputs_by_net.get(flow_net, []):
                if other == nid or (nid in loop_parent and other == loop_parent[nid]):
                    continue
                edges[nid].append(other)
    return edges


def _detect_propagation_back_edges(
    edges: dict[str, list[str]],
    root_order: list[str],
) -> set[tuple[str, str]]:
    """DFS back-edges whose removal makes the propagation graph acyclic.

    Passive SERIES/RESISTOR loops are already broken via ``loop_parent``, but a
    non-passive cycle — e.g. two REGULATORs feeding each other on an ORing
    power-path — has no such handling: the relaxation loops would ping-pong,
    bumping each other's column until the iteration guard trips, so the final
    order depended on the guard count rather than topology. Breaking these edges
    turns the graph into a DAG and makes the longest-path relaxation converge to
    a stable order.

    ``root_order`` seeds the DFS: the column-0 sources come first so exploration
    runs *downstream* from them and the edge closing a cycle back toward a source
    is the one classified as the back-edge (the semantically correct one to drop).
    An unanchored mutual loop falls back to node order as a deterministic
    tie-break.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    back: set[tuple[str, str]] = set()
    for root in root_order:
        if color.get(root, WHITE) != WHITE:
            continue
        color[root] = GRAY
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, i = stack[-1]
            neighbors = edges.get(node, ())
            if i < len(neighbors):
                stack[-1] = (node, i + 1)
                nxt = neighbors[i]
                c = color.get(nxt, WHITE)
                if c == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, 0))
                elif c == GRAY:
                    back.add((node, nxt))
            else:
                color[node] = BLACK
                stack.pop()
    return back


def _mixed_role_node_ids(node_specs: list[NodeSpec]) -> set[str]:
    """Designators that carry more than one PDN role (e.g. SERIES + SINK)."""
    roles_by_id: dict[str, set[str]] = defaultdict(set)
    for spec in node_specs:
        nid = spec["node_id"]
        sections = spec.get("sections")
        if sections:
            roles_by_id[nid].update(sec["role"] for sec in sections)
        else:
            roles_by_id[nid].add(spec["role"])
        roles_by_id[nid].update((spec.get("port_roles") or {}).values())
    return {nid for nid, rs in roles_by_id.items() if len(rs) > 1}


def assign_columns(
    node_specs: list[NodeSpec],
    net_to_rail: dict[str, str],
) -> dict[str, int]:
    """Place nodes in columns by propagating from SOURCE outputs along nets."""
    col: dict[str, int] = {}
    role_by_id = {s["node_id"]: s["role"] for s in node_specs}
    mixed_role_ids = _mixed_role_node_ids(node_specs)

    sources = [s for s in node_specs if s["role"] in ("SOURCE",)]
    for s in node_specs:
        if s["role"] == "REGULATOR" and not sources:
            sources.append(s)
    if not sources:
        sources = node_specs[:1] if node_specs else []

    for s in sources:
        col[s["node_id"]] = 0

    outputs_by_net: dict[str, list[str]] = defaultdict(list)
    inputs_by_net: dict[str, list[str]] = defaultdict(list)
    for s in node_specs:
        nid = s["node_id"]
        for pname, side, _ in s["port_defs"]:
            term = (s["terms"] or {}).get(pname)
            if is_ideal_return(term):
                continue
            port_role = spec_port_role(s, pname)
            flow_net = _column_net(port_role, term, net_to_rail, terminal=pname)
            if not flow_net or flow_net == GND_NET:
                continue
            if is_output_port(port_role, pname, side):
                outputs_by_net[flow_net].append(nid)
            else:
                inputs_by_net[flow_net].append(nid)

    loop_parent = _detect_loop_series_parents(node_specs, outputs_by_net, inputs_by_net)
    back_edges = _detect_propagation_back_edges(
        _propagation_edges(node_specs, outputs_by_net, inputs_by_net, net_to_rail, loop_parent),
        [s["node_id"] for s in sources] + [s["node_id"] for s in node_specs],
    )

    changed = True
    guard = 0
    while changed and guard < len(node_specs) + 5:
        guard += 1
        changed = False
        for s in node_specs:
            nid = s["node_id"]
            base = col.get(nid, 0)
            for pname, side, _ in s["port_defs"]:
                port_role = spec_port_role(s, pname)
                if not is_output_port(port_role, pname, side):
                    continue
                term = (s["terms"] or {}).get(pname)
                if is_ideal_return(term):
                    continue
                flow_net = _column_net(port_role, term, net_to_rail, terminal=pname)
                if not flow_net or flow_net == GND_NET:
                    continue
                for other in inputs_by_net.get(flow_net, []):
                    if other == nid:
                        continue
                    if nid in loop_parent and other == loop_parent[nid]:
                        continue
                    if (nid, other) in back_edges:
                        continue
                    new_c = base + 1
                    if new_c > col.get(other, -1):
                        col[other] = new_c
                        changed = True

    for s in node_specs:
        nid = s["node_id"]
        if nid not in col:
            col[nid] = max(col.values(), default=0) + 1

    for child_id, parent_id in loop_parent.items():
        col[child_id] = col.get(parent_id, 0) + 1

    for s in node_specs:
        if not spec_has_series_role(s):
            continue
        nid = s["node_id"]
        if nid in loop_parent:
            continue
        upstream_cols = _passive_upstream_cols(s, nid, outputs_by_net, col, loop_parent)
        if upstream_cols:
            col[nid] = min(col.get(nid, 0), max(upstream_cols) + 1)

    for s in node_specs:
        if not spec_has_series_role(s):
            continue
        _apply_passive_column_col(
            s,
            s["node_id"],
            col,
            outputs_by_net,
            inputs_by_net,
            loop_parent,
        )

    # Parallel taps on the P-side *physical* net sit to the right of the
    # bridge (not N-side downstream loads). Do not use rail-canonical membership
    # — a net-tie's upstream and downstream names often share a rail group.
    for s in node_specs:
        if not spec_has_series_role(s):
            continue
        nid = s["node_id"]
        rcol = col.get(nid, 0)
        downstream: set[str] = set()
        for pname, term in spec_series_terms(s):
            if not term or is_ideal_return(term) or not pname.startswith("N"):
                continue
            n_net = _column_flow_net(term)
            if not n_net:
                continue
            for other in inputs_by_net.get(n_net, []):
                if other != nid:
                    downstream.add(other)
        for pname, term in spec_series_terms(s):
            if not term or is_ideal_return(term):
                continue
            if not pname.startswith("P"):
                continue
            p_net = _column_flow_net(term)
            if not p_net or p_net == GND_NET:
                continue
            for other in inputs_by_net.get(p_net, []):
                if other == nid or other in downstream:
                    continue
                if role_by_id.get(other) in ("RESISTOR", "SERIES"):
                    continue
                if col.get(other, 0) <= rcol:
                    col[other] = max(col[other], rcol + 1)

    changed = True
    guard = 0
    while changed and guard < len(node_specs) + 5:
        guard += 1
        changed = False
        for s in node_specs:
            if s["role"] in ("RESISTOR", "SERIES"):
                continue
            nid = s["node_id"]
            base = col.get(nid, 0)
            for pname, side, _ in s["port_defs"]:
                port_role = spec_port_role(s, pname)
                if not is_output_port(port_role, pname, side):
                    continue
                term = (s["terms"] or {}).get(pname)
                if is_ideal_return(term):
                    continue
                flow_net = _column_net(port_role, term, net_to_rail, terminal=pname)
                if not flow_net or flow_net == GND_NET:
                    continue
                for other in inputs_by_net.get(flow_net, []):
                    if other == nid:
                        continue
                    if nid in loop_parent and other == loop_parent[nid]:
                        continue
                    if (nid, other) in back_edges:
                        continue
                    new_c = base + 1
                    if new_c > col.get(other, -1):
                        col[other] = new_c
                        changed = True

    for child_id, parent_id in loop_parent.items():
        col[child_id] = col.get(parent_id, 0) + 1

    for s in node_specs:
        if not spec_has_series_role(s):
            continue
        _apply_passive_column_col(
            s,
            s["node_id"],
            col,
            outputs_by_net,
            inputs_by_net,
            loop_parent,
        )

    for child_id, parent_id in loop_parent.items():
        col[child_id] = col.get(parent_id, 0) + 1

    _push_passive_load_columns(
        node_specs, col, inputs_by_net, loop_parent, role_by_id, outputs_by_net
    )

    if col:
        sink_col = max(col.values())
        for s in node_specs:
            if s["role"] == "SINK" and s["node_id"] not in mixed_role_ids:
                col[s["node_id"]] = sink_col

    col = compress_column_ranks(col, GRAPH_LAYOUT_MAX_COLUMNS)
    # Pin SOURCE/SINK before passive merges so a mid-chain SOURCE rank cannot
    # sit between two RESISTOR columns and block packing (power_board).
    col = _pin_source_sink_columns(node_specs, col, mixed_role_ids)
    col = _merge_adjacent_passive_columns(node_specs, col, loop_parent)
    col = _pin_source_sink_columns(node_specs, col, mixed_role_ids)
    # Rank bucketing can co-locate a driver with its SERIES load. Restore
    # strict left→right flow for every P-side upstream.
    _ensure_passives_right_of_upstream(
        node_specs, col, outputs_by_net, loop_parent,
    )
    col = _compact_columns(col)
    col = _pin_source_sink_columns(node_specs, col, mixed_role_ids)

    # Orient each SERIES/RESISTOR so the terminal carrying the downstream loads
    # faces right. Peers are keyed by *resolved physical net* (not the canonical
    # rail — 0-Ω bridges merge a resistor's two nets onto one rail, which would
    # make both terminals look identical). Flip P→right / N→left only when P has
    # downstream nodes and NO upstream driver (a mid-rail tap keeps its driver on
    # the P side, so the default P-left is correct and must stay).
    orient_series_ports_for_columns(
        node_specs, col, net_to_rail, loop_parent, outputs_by_net, inputs_by_net,
    )
    orient_ports_toward_peers(
        node_specs, col, net_to_rail, loop_parent=loop_parent,
    )
    ensure_unique_port_rows(node_specs)

    return _compact_columns(col)


def orient_series_ports_for_columns(
    node_specs: list[NodeSpec],
    col: dict[str, int],
    net_to_rail: dict[str, str],
    loop_parent: dict[str, str] | None = None,
    outputs_by_net: dict[str, list[str]] | None = None,
    inputs_by_net: dict[str, list[str]] | None = None,
) -> None:
    """Orient SERIES/RESISTOR port faces for the given column map (in place).

    Used after graph ``assign_columns`` and again after schematic column seeds
    so P/N faces track the final column layout. When net maps / loop parents
    are omitted, they are rebuilt from *node_specs* so loop SERIES pairs still
    get parent-facing ports.
    """
    if outputs_by_net is None or inputs_by_net is None:
        outputs_by_net = defaultdict(list)
        inputs_by_net = defaultdict(list)
        for s in node_specs:
            nid = s["node_id"]
            for pname, side, _ in s["port_defs"]:
                term = (s["terms"] or {}).get(pname)
                if is_ideal_return(term):
                    continue
                port_role = spec_port_role(s, pname)
                flow_net = _column_net(port_role, term, net_to_rail, terminal=pname)
                if not flow_net or flow_net == GND_NET:
                    continue
                if is_output_port(port_role, pname, side):
                    outputs_by_net[flow_net].append(nid)
                else:
                    inputs_by_net[flow_net].append(nid)
    if loop_parent is None:
        loop_parent = _detect_loop_series_parents(
            node_specs, outputs_by_net, inputs_by_net,
        )

    wnet_cols: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for s in node_specs:
        for rp in (s.get("resolved_ports") or {}).values():
            if rp.wnet and rp.wnet != GND_NET:
                wnet_cols[rp.wnet].append((s["node_id"], col.get(s["node_id"], 0)))

    _orient_loop_series_ports(
        node_specs, col, loop_parent, outputs_by_net, inputs_by_net,
    )

    for s in node_specs:
        if not spec_has_series_role(s):
            continue
        nid = s["node_id"]
        if nid in loop_parent:
            continue
        rcol = col.get(nid, 0)
        rports = s.get("resolved_ports") or {}

        def _cols(prefix, _rports=rports, _nid=nid):
            return [
                c
                for pname, rp in _rports.items()
                if pname.startswith(prefix)
                for oid, c in wnet_cols.get(rp.wnet, [])
                if oid != _nid
            ]

        p_cols, n_cols = _cols("P"), _cols("N")
        p_up, p_down = any(c < rcol for c in p_cols), any(c > rcol for c in p_cols)
        n_down = any(c > rcol for c in n_cols)
        if p_down and not p_up and not n_down:
            s["port_defs"] = [
                (
                    pname,
                    "right" if pname.startswith("P") else "left" if pname.startswith("N") else side,
                    sort_key,
                )
                for pname, side, sort_key in s["port_defs"]
            ]


def orient_ports_toward_peers(
    node_specs: list[NodeSpec],
    col: dict[str, int],
    net_to_rail: dict[str, str],
    *,
    loop_parent: dict[str, str] | None = None,
) -> None:
    """Face each non-GND port toward its connected peers' columns (in place).

    SERIES/RESISTOR keep through-flow faces from
    :func:`orient_series_ports_for_columns` / role defaults (opposite sides).
    Loop-SERIES children keep the dedicated all-on-one-face rule from
    :func:`_orient_loop_series_ports` and are skipped here. Same-column peers
    leave the existing side unchanged.
    """
    del net_to_rail  # peers keyed by resolved physical nets already on specs
    loop_parent = loop_parent or {}
    wnet_cols: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for s in node_specs:
        for rp in (s.get("resolved_ports") or {}).values():
            if rp.wnet and rp.wnet != GND_NET:
                wnet_cols[rp.wnet].append((s["node_id"], col.get(s["node_id"], 0)))

    for s in node_specs:
        nid = s["node_id"]
        if nid in loop_parent:
            continue
        # Through-elements stay left↔right; peer-facing same-side stacks are for
        # SOURCE/SINK/REGULATOR hubs, not series passives.
        if spec_has_series_role(s):
            continue
        self_col = col.get(nid, 0)
        rports = s.get("resolved_ports") or {}
        new_defs = []
        changed = False
        for pname, side, sort_key in s["port_defs"]:
            rp = rports.get(pname)
            if rp is None or not rp.wnet or rp.wnet == GND_NET:
                new_defs.append((pname, side, sort_key))
                continue
            peer_cols = [c for oid, c in wnet_cols.get(rp.wnet, []) if oid != nid]
            if not peer_cols:
                new_defs.append((pname, side, sort_key))
                continue
            left_n = sum(1 for c in peer_cols if c < self_col)
            right_n = sum(1 for c in peer_cols if c > self_col)
            # Only commit when peers sit exclusively on one side; mixed or
            # same-column peers keep the existing face (hub rows, taps).
            if left_n > 0 and right_n == 0:
                new_side = "left"
            elif right_n > 0 and left_n == 0:
                new_side = "right"
            else:
                new_defs.append((pname, side, sort_key))
                continue
            if new_side != side:
                changed = True
            new_defs.append((pname, new_side, sort_key))
        if changed:
            s["port_defs"] = new_defs
            # Keep composite section faces in sync for non-SERIES sections.
            for sec in s.get("sections") or []:
                if sec.get("role") in ("SERIES", "RESISTOR"):
                    continue
                sec_defs = []
                for pname, side, sort_key in sec.get("port_defs") or []:
                    match = next((d for d in new_defs if d[0] == pname), None)
                    sec_defs.append(match if match is not None else (pname, side, sort_key))
                sec["port_defs"] = sec_defs


def specs_by_column(
    node_specs: list[NodeSpec],
    columns: dict[str, int],
) -> tuple[dict[int, list[NodeSpec]], int]:
    """Group component specs by column index (insertion order within each column)."""
    by_col: dict[int, list[NodeSpec]] = defaultdict(list)
    for spec in node_specs:
        by_col[columns.get(spec["node_id"], 0)].append(spec)
    max_col = max(by_col.keys(), default=0)
    return by_col, max_col


def _enrich_resolved_ports(spec: NodeSpec) -> None:
    """Resolve wire net (``wnet``) and display label (``plabel``) per port.

    ``wnet`` always comes from :func:`terminal_net` (first pin when pads
    disagree) so routing stays on one gutter/bus per connector row.
    ``plabel`` may list every pad net on multi-pin terminals — see
    :func:`port_display_net`. The tooltip keeps the full list even when the
    drawn label is truncated to fit.
    """
    resolved: dict[str, ResolvedPort] = {}
    port_directives = spec.get("port_directives") or {}
    channel_ports = set(spec.get("channel_ports") or ())
    terms = spec.get("terms") or {}
    for pname, _, _ in spec["port_defs"]:
        term = terms.get(pname)
        raw = terminal_net(term)
        wnet = wire_net(raw)
        if not wnet:
            continue
        label = port_display_net(term, raw, channel_row=pname in channel_ports)
        resolved[pname] = ResolvedPort(
            wnet=wnet,
            plabel=truncate_label(label),
            tooltip=port_tooltip(label, port_directives.get(pname), pname),
        )
    spec["resolved_ports"] = resolved


def parse_topology_directives(metadata: TopologyMetadata) -> ParsedLayoutInput:
    """Parse metadata into layout-ready component specs and rail maps."""
    # Deferred: rail_groups imports topology.constants; eager import here
    # would cycle with metadata/__init__ → layout_bridge during package init.
    from fypa.rail_groups import compute_rail_groups

    _, rail_to_members = compute_rail_groups(metadata)
    net_to_rail = net_to_rail_map(rail_to_members)
    errors = list(metadata.get("annotation_errors") or [])
    directives = sorted(
        metadata.get("directives") or [],
        key=lambda d: natural_sort_key(str(d.get("designator") or d.get("label", ""))),
    )
    node_specs = directives_to_component_specs(directives, errors, net_to_rail)
    needs_gnd = False
    for spec in node_specs:
        _enrich_resolved_ports(spec)
        for pname, _, _ in spec["port_defs"]:
            term = (spec["terms"] or {}).get(pname)
            if canonical_net(terminal_net(term), net_to_rail) == GND_NET:
                needs_gnd = True
    columns = assign_columns(node_specs, net_to_rail)
    return ParsedLayoutInput(
        node_specs=node_specs,
        net_to_rail=net_to_rail,
        driven_nets=driven_power_nets(node_specs, net_to_rail),
        needs_gnd=needs_gnd,
        columns=columns,
    )


def is_return_port_row(sort_key: int) -> bool:
    return sort_key >= RETURN_PORT_SORT_BASE


__all__ = [
    "GRAPH_LAYOUT_MAX_COLUMNS",
    "ParsedLayoutInput",
    "ResolvedPort",
    "assign_columns",
    "compress_column_ranks",
    "ensure_unique_port_rows",
    "is_return_port_row",
    "jump_row_for_directive",
    "orient_ports_toward_peers",
    "orient_series_ports_for_columns",
    "parse_topology_directives",
    "specs_by_column",
]
