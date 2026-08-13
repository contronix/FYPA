"""Vertical node alignment within columns."""

from __future__ import annotations

from collections import defaultdict

from fypa.topology.constants import (
    BODY_PAD,
    GND_NET,
    HEADER_H,
    MARGIN,
    PORT_ROW_H,
    ROW_GAP,
    WIRE_EPS,
)
from fypa.topology.metadata.layout_bridge import is_return_port_row
from fypa.topology.metadata_schema import NodeSpec, RoleSection
from fypa.topology.metadata.specs import spec_port_role
from fypa.topology.terminal_roles import is_output_port


def node_height(n_rows: int) -> float:
    return HEADER_H + BODY_PAD + max(n_rows, 1) * PORT_ROW_H + BODY_PAD


def section_body_height(n_rows: int) -> float:
    """Body height for one stacked role block (excludes its header)."""
    return BODY_PAD + max(n_rows, 1) * PORT_ROW_H + BODY_PAD


def composite_node_height(sections: list[RoleSection]) -> float:
    """Total symbol height for a multi-role component."""
    total = 0.0
    for sec in sections:
        n_rows, _ = port_layout_rows(sec["port_defs"])
        total += HEADER_H + section_body_height(n_rows)
    return total


def section_y_offsets(sections: list[RoleSection]) -> list[tuple[RoleSection, float, float]]:
    """Return ``(section, y_offset, section_height)`` for stacked layout."""
    out: list[tuple[RoleSection, float, float]] = []
    y = 0.0
    for sec in sections:
        n_rows, _ = port_layout_rows(sec["port_defs"])
        sec_h = HEADER_H + section_body_height(n_rows)
        out.append((sec, y, sec_h))
        y += sec_h
    return out


def port_layout_rows(port_defs: list[tuple[str, str, int]]) -> tuple[int, dict[int, int]]:
    """Map sort_key to 0-based layout row; returns (n_rows, sort_key -> row)."""
    channel_rows = (
        max(
            (sk for _, _, sk in port_defs if not is_return_port_row(sk)),
            default=-1,
        )
        + 1
    )
    return_ports = sorted(
        ((pname, side, sk) for pname, side, sk in port_defs if is_return_port_row(sk)),
        key=lambda t: t[2],
    )
    row_map: dict[int, int] = {}
    for ret_i, (_, _, sk) in enumerate(return_ports):
        row_map[sk] = channel_rows + ret_i
    n_rows = max(channel_rows + len(return_ports), 1)
    return n_rows, row_map


def _spec_layout_height(spec: NodeSpec) -> float:
    sections = spec.get("sections")
    if sections:
        return composite_node_height(sections)
    n_layout_rows, _ = port_layout_rows(spec["port_defs"])
    return node_height(n_layout_rows)


def _port_center_offset_from_top(spec: NodeSpec, terminal: str) -> float | None:
    """Y offset from symbol top to the port center (matches ``columns.py``)."""
    sections = spec.get("sections")
    if sections:
        for sec, sec_y, _sec_h in section_y_offsets(sections):
            _n_rows, row_map = port_layout_rows(sec["port_defs"])
            for pname, _side, sk in sec["port_defs"]:
                if pname != terminal:
                    continue
                row_i = row_map.get(sk, sk if not is_return_port_row(sk) else 0)
                if not is_return_port_row(sk) and sk not in row_map:
                    row_i = sk
                return sec_y + HEADER_H + BODY_PAD + row_i * PORT_ROW_H + PORT_ROW_H / 2
        return None
    _n_rows, row_map = port_layout_rows(spec["port_defs"])
    for pname, _side, sk in spec["port_defs"]:
        if pname != terminal:
            continue
        row_i = row_map.get(sk, sk if not is_return_port_row(sk) else 0)
        if not is_return_port_row(sk) and sk not in row_map:
            row_i = sk
        return HEADER_H + BODY_PAD + row_i * PORT_ROW_H + PORT_ROW_H / 2
    return None


def _net_port_offsets(spec: NodeSpec) -> dict[str, float]:
    """Map wire-net → port-center offset from symbol top (first terminal wins)."""
    out: dict[str, float] = {}
    for pname, resolved in (spec.get("resolved_ports") or {}).items():
        wnet = resolved.wnet
        if not wnet or wnet == GND_NET or wnet in out:
            continue
        off = _port_center_offset_from_top(spec, pname)
        if off is not None:
            out[wnet] = off
    return out


def _shared_nets(a: NodeSpec, b: NodeSpec) -> list[str]:
    nets_a = {
        r.wnet
        for r in (a.get("resolved_ports") or {}).values()
        if r.wnet and r.wnet != GND_NET
    }
    nets_b = {
        r.wnet
        for r in (b.get("resolved_ports") or {}).values()
        if r.wnet and r.wnet != GND_NET
    }
    return sorted(nets_a & nets_b)


def _port_aligned_top_y(
    spec: NodeSpec,
    partner: NodeSpec,
    partner_top: float,
) -> float:
    """Symbol top so a shared-net port lines up with the partner's port."""
    offs_self = _net_port_offsets(spec)
    offs_part = _net_port_offsets(partner)
    shared = [n for n in _shared_nets(spec, partner) if n in offs_self and n in offs_part]
    if not shared:
        return partner_top
    best_net = min(shared, key=lambda n: (abs(offs_part[n] - offs_self[n]), n))
    return partner_top + offs_part[best_net] - offs_self[best_net]


def _clamp_top_y(y: float) -> float:
    """Keep symbol tops on-canvas (port align can undershoot ``MARGIN``)."""
    return max(float(MARGIN), y)


def _intervals_overlap(
    y: float,
    height: float,
    occupied: list[tuple[float, float]],
) -> bool:
    y_end = y + height
    for y0, y1 in occupied:
        if y_end + ROW_GAP > y0 and y < y1 + ROW_GAP:
            return True
    return False


def _alloc_free_y(
    occupied: list[tuple[float, float]],
    height: float,
    *,
    preferred: float | None = None,
) -> float:
    """Pick a free top Y; prefer the free slot nearest ``preferred``."""
    if preferred is not None and not _intervals_overlap(preferred, height, occupied):
        return max(float(MARGIN), preferred)

    candidates: list[float] = [float(MARGIN)]
    y = float(MARGIN)
    for y0, y1 in sorted(occupied):
        if y + height + ROW_GAP <= y0 + WIRE_EPS:
            candidates.append(y)
        y = max(y, y1 + ROW_GAP)
    candidates.append(y)

    free = [c for c in candidates if not _intervals_overlap(c, height, occupied)]
    if not free:
        return y
    if preferred is None:
        return free[0]
    pref = max(float(MARGIN), preferred)
    return min(free, key=lambda c: (abs(c - pref), c))


def _direct_alignment_pairs(
    node_specs: list[NodeSpec],
    columns: dict[str, int],
) -> list[frozenset[str]]:
    """Node pairs on the same net with no other node on that net between them."""
    net_members: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for spec in node_specs:
        nid = spec["node_id"]
        col = columns[nid]
        for resolved in (spec.get("resolved_ports") or {}).values():
            wnet = resolved.wnet
            if not wnet or wnet == GND_NET:
                continue
            net_members[wnet].append((nid, col))

    pairs: set[frozenset[str]] = set()
    for members in net_members.values():
        unique: dict[str, int] = {}
        for nid, col in members:
            unique.setdefault(nid, col)
        by_col = sorted(unique.items(), key=lambda t: t[1])
        for i, (a, ca) in enumerate(by_col):
            for b, cb in by_col[i + 1 :]:
                if ca == cb:
                    continue
                if any(ca < c < cb for _, c in by_col if _ not in (a, b)):
                    continue
                pairs.add(frozenset((a, b)))
    return list(pairs)


def _port_row_delta(spec: NodeSpec, partner: NodeSpec) -> float:
    """Absolute port-row mismatch for the best shared net (inf if none)."""
    offs_self = _net_port_offsets(spec)
    offs_part = _net_port_offsets(partner)
    shared = [n for n in _shared_nets(spec, partner) if n in offs_self and n in offs_part]
    if not shared:
        return float("inf")
    return min(abs(offs_part[n] - offs_self[n]) for n in shared)


def _pick_downstream_align_partner(
    spec: dict,
    candidates: list[str],
    columns: dict[str, int],
    specs_by_id: dict[str, dict],
) -> str | None:
    """Nearest downstream column; prefer shared nets / small port-row delta."""
    if not candidates:
        return None

    next_col = min(columns[o] for o in candidates)
    in_next = [o for o in candidates if columns[o] == next_col]
    if len(in_next) == 1:
        return in_next[0]
    output_nets: set[str] = set()
    for pname, side, _ in spec["port_defs"]:
        if not is_output_port(spec_port_role(spec, pname), pname, side):
            continue
        resolved = (spec.get("resolved_ports") or {}).get(pname)
        if resolved and resolved.wnet:
            output_nets.add(resolved.wnet)
    pool = in_next
    if output_nets:
        shared = [
            o
            for o in in_next
            if any(
                (specs_by_id[o].get("resolved_ports") or {}).get(pn)
                and (specs_by_id[o].get("resolved_ports") or {})[pn].wnet in output_nets
                for pn in (specs_by_id[o].get("resolved_ports") or {})
            )
        ]
        if shared:
            pool = shared
    return min(
        pool,
        key=lambda o: (_port_row_delta(spec, specs_by_id[o]), str(o)),
    )


def _primary_non_gnd_net(
    spec: NodeSpec,
    *,
    peer_net_counts: dict[str, int] | None = None,
) -> str:
    """Pack key net: prefer supply rails shared with many column peers."""
    nets = [
        r.wnet
        for r in (spec.get("resolved_ports") or {}).values()
        if r.wnet and r.wnet != GND_NET
    ]
    if not nets:
        return ""
    supply = [
        n
        for n in nets
        if n.startswith(("VDD", "VCC", "VBUS", "VIN", "VBAT", "VAA", "VDDA"))
    ]
    pool = supply if supply else nets
    counts = peer_net_counts or {}
    return min(pool, key=lambda n: (-counts.get(n, 0), n))


def _peer_net_counts(pending: list[NodeSpec]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for spec in pending:
        seen: set[str] = set()
        for resolved in (spec.get("resolved_ports") or {}).values():
            wnet = resolved.wnet
            if not wnet or wnet == GND_NET or wnet in seen:
                continue
            seen.add(wnet)
            counts[wnet] += 1
    return counts


def _sink_pack_sort_key(
    spec: NodeSpec,
    peer_net_counts: dict[str, int],
) -> tuple:
    """Order sinks by shared primary net, then designator."""
    return (
        _primary_non_gnd_net(spec, peer_net_counts=peer_net_counts),
        spec.get("designator") or spec["node_id"],
    )


def compress_empty_bands(
    y_assign: dict[str, float],
    node_specs: list[NodeSpec],
    columns: dict[str, int],
    *,
    protect_ids: set[str] | None = None,
) -> dict[str, float]:
    """Close unused vertical gaps within each column (top-down pack)."""
    protect_ids = protect_ids or set()
    heights = {s["node_id"]: _spec_layout_height(s) for s in node_specs}
    out = dict(y_assign)
    by_col: dict[int, list[NodeSpec]] = defaultdict(list)
    for spec in node_specs:
        by_col[columns[spec["node_id"]]].append(spec)

    for _c, specs in by_col.items():
        ordered = sorted(specs, key=lambda s: (out[s["node_id"]], s["node_id"]))
        if ordered and all(s["node_id"] in protect_ids for s in ordered):
            continue
        y = float(MARGIN)
        for spec in ordered:
            nid = spec["node_id"]
            nh = heights[nid]
            if nid in protect_ids:
                y = max(y, out[nid] + nh + ROW_GAP)
                continue
            if out[nid] > y + WIRE_EPS:
                out[nid] = y
            y = out[nid] + nh + ROW_GAP
    return out


def assign_vertical_positions(
    node_specs: list[NodeSpec],
    columns: dict[str, int],
    max_col: int,
) -> dict[str, float]:
    """Place node tops so shared-net ports align with the downstream neighbour."""
    heights = {s["node_id"]: _spec_layout_height(s) for s in node_specs}
    specs_by_id = {s["node_id"]: s for s in node_specs}
    pairs = _direct_alignment_pairs(node_specs, columns)
    higher_partners: dict[str, list[str]] = defaultdict(list)
    for pair in pairs:
        a, b = tuple(pair)
        ca, cb = columns[a], columns[b]
        if ca < cb:
            higher_partners[a].append(b)
        elif cb < ca:
            higher_partners[b].append(a)

    occupied: dict[int, list[tuple[float, float]]] = defaultdict(list)
    y_assign: dict[str, float] = {}

    for c in range(max_col, -1, -1):
        pending = [s for s in node_specs if columns[s["node_id"]] == c]
        if c == max_col:
            peer_counts = _peer_net_counts(pending)
            pending = sorted(
                pending,
                key=lambda s: _sink_pack_sort_key(s, peer_counts),
            )
        while pending:
            progressed = False
            for spec in list(pending):
                nid = spec["node_id"]
                nh = heights[nid]
                downstream = [
                    o
                    for o in higher_partners.get(nid, [])
                    if columns[o] > c and o in y_assign
                ]
                if downstream:
                    partner_id = _pick_downstream_align_partner(
                        spec,
                        downstream,
                        columns,
                        specs_by_id,
                    )
                    if partner_id is None:
                        continue
                    y = _port_aligned_top_y(
                        spec,
                        specs_by_id[partner_id],
                        y_assign[partner_id],
                    )
                    y = _clamp_top_y(y)
                elif c == max_col:
                    y = _alloc_free_y(occupied[c], nh)
                else:
                    continue
                if _intervals_overlap(y, nh, occupied[c]):
                    y = _alloc_free_y(occupied[c], nh, preferred=y)
                    y = _clamp_top_y(y)
                y_assign[nid] = y
                occupied[c].append((y, y + nh))
                pending.remove(spec)
                progressed = True
            if not progressed:
                for spec in pending:
                    nid = spec["node_id"]
                    nh = heights[nid]
                    y = _alloc_free_y(occupied[c], nh)
                    y_assign[nid] = y
                    occupied[c].append((y, y + nh))
                break

    # Protect port-aligned upstream nodes and multi-channel mid connectors.
    protect: set[str] = set()
    for spec in node_specs:
        nets = {
            r.wnet
            for r in (spec.get("resolved_ports") or {}).values()
            if r.wnet and r.wnet != GND_NET
        }
        if len(nets) >= 3 and 0 < columns[spec["node_id"]] < max_col:
            protect.add(spec["node_id"])
    for nid, partners in higher_partners.items():
        if any(p in y_assign for p in partners):
            protect.add(nid)

    y_assign = compress_empty_bands(
        y_assign, node_specs, columns, protect_ids=protect
    )

    peer_repacked: set[str] = set()
    for c in range(max_col):
        pending = [s for s in node_specs if columns[s["node_id"]] == c]
        if len(pending) < 2:
            continue
        parent = {s["node_id"]: s["node_id"] for s in pending}

        def find(a: str) -> str:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        nets_of = {
            s["node_id"]: {
                r.wnet
                for r in (s.get("resolved_ports") or {}).values()
                if r.wnet and r.wnet != GND_NET
            }
            for s in pending
        }
        for i, a in enumerate(pending):
            for b in pending[i + 1 :]:
                if nets_of[a["node_id"]] & nets_of[b["node_id"]]:
                    union(a["node_id"], b["node_id"])
        groups: dict[str, list[NodeSpec]] = defaultdict(list)
        for s in pending:
            groups[find(s["node_id"])].append(s)
        occupied = [
            (y_assign[s["node_id"]], y_assign[s["node_id"]] + heights[s["node_id"]])
            for s in pending
        ]
        for members in groups.values():
            if len(members) < 2:
                continue
            # Only SERIES/RESISTOR peer stacks (Q15-class); do not re-pack
            # regulator columns used by hub-escape fixtures.
            if not all(s.get("role") in ("SERIES", "RESISTOR") for s in members):
                continue
            members = sorted(members, key=lambda s: y_assign[s["node_id"]])
            tops = [y_assign[s["node_id"]] for s in members]
            bots = [y_assign[s["node_id"]] + heights[s["node_id"]] for s in members]
            span = max(bots) - min(tops)
            tight = sum(heights[s["node_id"]] for s in members) + ROW_GAP * (
                len(members) - 1
            )
            if span <= tight + ROW_GAP + WIRE_EPS:
                continue
            # Pull the block up toward the highest member (avoid parking at canvas bottom).
            preferred = min(tops)
            block_h = tight
            others = [
                (a, b)
                for s, (a, b) in zip(pending, occupied)
                if s["node_id"] not in {m["node_id"] for m in members}
            ]
            top = _alloc_free_y(others, block_h, preferred=preferred)
            top = _clamp_top_y(top)
            y = top
            for spec in members:
                nid = spec["node_id"]
                nh = heights[nid]
                y_assign[nid] = y
                peer_repacked.add(nid)
                y += nh + ROW_GAP

    # Re-align west columns to peer-packed SERIES stacks (J13/Q17 → Q7).
    # Do not move peer-repacked members themselves.
    for c in range(max_col - 1, -1, -1):
        pending = [s for s in node_specs if columns[s["node_id"]] == c]
        col_occ = [
            (y_assign[s["node_id"]], y_assign[s["node_id"]] + heights[s["node_id"]])
            for s in pending
        ]
        for spec in pending:
            nid = spec["node_id"]
            if nid in peer_repacked:
                continue
            downstream = [
                o
                for o in higher_partners.get(nid, [])
                if columns[o] > c and o in y_assign
            ]
            if not downstream:
                continue
            partner_id = _pick_downstream_align_partner(
                spec, downstream, columns, specs_by_id
            )
            if partner_id is None:
                continue
            y = _clamp_top_y(
                _port_aligned_top_y(
                    spec, specs_by_id[partner_id], y_assign[partner_id]
                )
            )
            nh = heights[nid]
            others = [
                (a, b)
                for a, b in col_occ
                if abs(a - y_assign[nid]) > WIRE_EPS
            ]
            if not _intervals_overlap(y, nh, others):
                y_assign[nid] = y
                col_occ = [
                    (
                        y_assign[s["node_id"]],
                        y_assign[s["node_id"]] + heights[s["node_id"]],
                    )
                    for s in pending
                ]
    return y_assign
