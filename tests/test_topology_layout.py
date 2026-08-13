"""Layout and hit-test tests for topology."""

from pathlib import Path
import pickle


from fypa.topology import build_topology_model, find_component_at
from fypa.topology.hit_test import find_wire_at, topology_net_at, topology_tooltip_at
from fypa.topology.render import render_net_highlight_svg
from fypa.topology.constants import HEADER_H, MIN_PARALLEL_GAP, NODE_W, PORT_WIRE_STUB
from tests.topology_fixtures import project_b_compact_metadata, load_topology_fixture


def _load_probe_dir(name: str):
    probe = Path("_probe") / name / "topology.pkl"
    if not probe.is_file():
        return None
    with probe.open("rb") as f:
        return build_topology_model(pickle.load(f))


def test_topology_tooltip_only_on_elements():
    """Empty canvas areas must not produce a tooltip; wires/ports/symbols do."""
    model = build_topology_model(project_b_compact_metadata())
    assert topology_tooltip_at(model, 0.0, 0.0) is None
    j1 = next(n for n in model.nodes if n.label == "J1")
    bx, by, bw, bh = j1.bounds
    assert topology_tooltip_at(model, bx + bw / 2, by + bh / 2)
    port = j1.ports[0]
    assert topology_tooltip_at(model, port.x, port.y)
    vdd_row = next(w for w in model.wires if w.routing_kind == "hub_row")
    assert find_wire_at(model, 430.0, 75.0) is vdd_row
    assert topology_tooltip_at(model, 430.0, 75.0)


def test_find_component_at_hit_test():
    model = build_topology_model(project_b_compact_metadata())
    j1 = next(n for n in model.nodes if n.label == "J1")
    bx, by, bw, bh = j1.bounds
    hit = find_component_at(model, bx + bw / 2, by + bh / 2)
    assert hit is not None
    assert hit.label == "J1"
    assert find_component_at(model, 0, 0) is None


def test_topology_net_highlight_on_wire_hover():
    """Hovering a wire yields highlight SVG for the whole net, not symbols."""
    model = build_topology_model(project_b_compact_metadata())
    assert topology_net_at(model, 0.0, 0.0) is None
    j1 = next(n for n in model.nodes if n.label == "J1")
    bx, by, bw, bh = j1.bounds
    assert topology_net_at(model, bx + bw / 2, by + bh / 2) is None
    port = j1.ports[0]
    assert topology_net_at(model, port.x, port.y) == port.net
    net = topology_net_at(model, 430.0, 75.0)
    assert net == "VDD_3V3_PWR"
    svg = render_net_highlight_svg(model, net)
    assert "stroke=" in svg
    assert "430" not in svg or "line" in svg

    model = build_topology_model(project_b_compact_metadata())
    j1 = next(n for n in model.nodes if n.label == "J1")
    bx, by, bw, bh = j1.bounds
    hit = find_component_at(model, bx + bw / 2, by + bh / 2)
    assert hit is not None
    assert hit.label == "J1"
    assert find_component_at(model, 0, 0) is None


def test_topology_nodes_do_not_overlap_in_column():
    """Mixed single-net and two-port nodes must stack without overlapping."""
    meta = {
        "directives": [
            {
                "role": "SOURCE",
                "designator": "J19",
                "label": "J19",
                "value_str": "1 V",
                "terminals": {
                    "P": {"requested_net": "NET_A", "pins": [{"net": "NET_A", "pad": "1"}]},
                    "N": {"requested_net": "GND", "pins": [{"net": "GND", "pad": "2"}]},
                },
            },
            {
                "role": "SOURCE",
                "designator": "J21",
                "label": "J21",
                "value_str": "1 V",
                "terminals": {
                    "P": {"requested_net": "NET_B", "pins": [{"net": "NET_B", "pad": "1"}]},
                    "N": {"ideal_return": True, "pin_count": 0, "pins": []},
                },
            },
            {
                "role": "SOURCE",
                "designator": "J23",
                "label": "J23",
                "value_str": "1 V",
                "terminals": {
                    "P": {"requested_net": "NET_C", "pins": [{"net": "NET_C", "pad": "1"}]},
                    "N": {"ideal_return": True, "pin_count": 0, "pins": []},
                },
            },
        ],
    }
    model = build_topology_model(meta)
    sources = sorted(
        (n for n in model.nodes if n.role == "SOURCE"),
        key=lambda n: n.y,
    )
    for above, below in zip(sources, sources[1:]):
        assert above.y + above.height <= below.y, (
            f"{above.label} overlaps {below.label}"
        )


def test_topology_project_b_compact_layout_stays_compact():
    """REGULATOR OUT_N must not propagate columns via GND (oscillation)."""
    model = build_topology_model(project_b_compact_metadata())
    j1 = next(n for n in model.nodes if n.designator == "J1")
    u2 = next(n for n in model.nodes if n.designator == "U2")
    assert j1.x < u2.x
    assert model.width < 1200.0


def test_probe_project_a_stays_compact() -> None:
    """Deprecated alias: row/trunk connectivity is covered by test_hub_routing_regressions."""
    from tests.hub_regression_helpers import FIXTURE_ROW_DETOUR, build_hub_fixture
    from tests.test_hub_routing_regressions import TestHubRowDetourReachesTrunk

    model = build_hub_fixture(FIXTURE_ROW_DETOUR)
    TestHubRowDetourReachesTrunk().test_every_power_port_is_on_one_connected_net(model)


def test_probe_qube_dense_pack_gate() -> None:
    """qube probe: peer stack, port ΔY, hub rail, column densify (skip if missing)."""
    from fypa.topology.constants import ROW_GAP
    from fypa.topology.report import topology_wiring_report

    model = _load_probe_dir("qube")
    if model is None:
        return
    xs = sorted({round(n.x, 1) for n in model.nodes})
    # Safe adjacent singleton pack drops ≥1 ASAP column; far ALAP is avoided.
    assert len(xs) <= 13, len(xs)
    peers = [n for n in model.nodes if (n.designator or "") in ("Q7", "Q14", "Q15")]
    if len(peers) == 3:
        span = max(n.y for n in peers) - min(n.y for n in peers)
        assert span <= 3 * (peers[0].height + ROW_GAP), span
    rep = topology_wiring_report(model)
    assert not any(
        i.get("code") == "hub_net_unrouted" and i.get("net") == "VDD_3V3"
        for i in rep.get("issues") or []
    )
    assert any(w.get("net") == "VDD_3V3" for w in rep.get("wires") or [])
    ports = rep.get("ports") or []
    dys = [
        abs(a["y"] - b["y"])
        for a in ports
        if str(a.get("node_id", "")).startswith("J14")
        for b in ports
        if str(b.get("node_id", "")).startswith("U27")
        and a.get("net")
        and a.get("net") == b.get("net")
        and a.get("net") != "GND"
    ]
    if dys:
        assert max(dys) == 0.0, max(dys)


def test_all_sinks_share_rightmost_column():
    """Pure SINK symbols align in the last column even when propagation stops early."""
    from fypa.topology.metadata.layout_bridge import (
        _mixed_role_node_ids,
        parse_topology_directives,
        specs_by_column,
    )

    parsed = parse_topology_directives(load_topology_fixture("project_b_hub_vdd"))
    _, max_col = specs_by_column(parsed.node_specs, parsed.columns)
    mixed = _mixed_role_node_ids(parsed.node_specs)
    sink_cols = {
        parsed.columns[s["node_id"]]
        for s in parsed.node_specs
        if s["role"] == "SINK" and s["node_id"] not in mixed
    }
    assert sink_cols == {max_col}


def test_rightmost_column_excludes_non_sink_loop_child():
    """Loop-series child must not share the rightmost column with pure SINKs."""
    from fypa.topology.metadata.layout_bridge import (
        _mixed_role_node_ids,
        parse_topology_directives,
        specs_by_column,
    )

    parsed = parse_topology_directives(load_topology_fixture("project_a_stepper_loop_rails"))
    by_col, max_col = specs_by_column(parsed.node_specs, parsed.columns)
    mixed = _mixed_role_node_ids(parsed.node_specs)
    for s in by_col[max_col]:
        roles = {sec["role"] for sec in (s.get("sections") or [])} or {s["role"]}
        if s["node_id"] in mixed:
            assert "SINK" in roles
        else:
            assert s["role"] == "SINK", s


def test_mixed_role_series_sink_keeps_bridge_before_pure_sink():
    """SERIES+SINK on one part: bridge column from propagation, not sink push."""
    from fypa.topology.metadata.layout_bridge import parse_topology_directives, specs_by_column

    metadata = {
        "annotation_errors": [],
        "net_canonical": {},
        "directives": [
            {
                "role": "SOURCE",
                "designator": "J1",
                "label": "J1",
                "value_str": "12 V",
                "terminals": {
                    "P": {
                        "requested_net": "+12V",
                        "pins": [{"net": "+12V", "pad": "1"}],
                    },
                    "N": {
                        "requested_net": "GND",
                        "pins": [{"net": "GND", "pad": "2"}],
                    },
                },
            },
            {
                "role": "RESISTOR",
                "designator": "U1",
                "label": "U1",
                "value_str": "0 mOhm",
                "terminals": {
                    "P": {
                        "requested_net": "+12V",
                        "pins": [{"net": "+12V", "pad": "1"}],
                    },
                    "N": {
                        "requested_net": "+5V",
                        "pins": [{"net": "+5V", "pad": "2"}],
                    },
                },
            },
            {
                "role": "SINK",
                "designator": "U1",
                "label": "U1#1",
                "channel_index": 1,
                "value_str": "10 mA",
                "terminals": {
                    "P": {
                        "requested_net": "SENSE",
                        "pins": [{"net": "SENSE", "pad": "3"}],
                    },
                    "N": {
                        "requested_net": "GND",
                        "pins": [{"net": "GND", "pad": "4"}],
                    },
                },
            },
            {
                "role": "SINK",
                "designator": "J2",
                "label": "J2",
                "value_str": "100 mA",
                "terminals": {
                    "P": {
                        "requested_net": "+5V",
                        "pins": [{"net": "+5V", "pad": "1"}],
                    },
                    "N": {
                        "requested_net": "GND",
                        "pins": [{"net": "GND", "pad": "2"}],
                    },
                },
            },
        ],
    }
    parsed = parse_topology_directives(metadata)
    cols = parsed.columns
    _, max_col = specs_by_column(parsed.node_specs, cols)
    assert cols["U1"] < cols["J2"]
    assert cols["J2"] == max_col


def test_hub_wires_no_horizontal_backtrack_on_probe() -> None:
    """Hub routing must not zig-zag horizontally (schematic left → right)."""
    from pathlib import Path
    import pickle

    from fypa.topology.geometry import parse_wire_path

    probe = Path("_probe/project_b/topology.pkl")
    if not probe.is_file():
        probe = Path("_probe/topology.pkl")
    if not probe.is_file():
        return
    with probe.open("rb") as f:
        meta = pickle.load(f)
    model = build_topology_model(meta)
    for wire in model.wires:
        if wire.net != "VDD_5V0":
            continue
        points = parse_wire_path(wire.path_d)
        for i in range(len(points) - 2):
            x0, y0 = points[i]
            x1, y1 = points[i + 1]
            x2, y2 = points[i + 2]
            if abs(y0 - y1) < 0.5 and abs(y1 - y2) < 0.5:
                d1, d2 = x1 - x0, x2 - x1
                assert not (d1 * d2 < 0 and abs(d1) > 0.5 and abs(d2) > 0.5), (
                    f"horizontal backtrack in {wire.path_d}"
                )


def test_regulator_mutual_feed_columns_are_deterministic() -> None:
    """Two REGULATORs feeding each other must not ping-pong the column
    relaxation to a guard-limited order. The cycle is broken back toward the
    source anchor, so U1 (source-fed) sits left of U2 (fed only by U1), the
    columns stay bounded, and the result is independent of spec ordering.
    Regression for finding 5.3 (non-passive cycles in assign_columns).
    """
    from fypa.topology.metadata.layout_bridge import assign_columns

    def term(net: str) -> dict:
        return {"requested_net": net}

    ideal = {"ideal_return": True}

    def spec(nid: str, role: str, terms: dict, port_defs: list) -> dict:
        return {
            "node_id": nid, "label": nid, "designator": nid, "role": role,
            "config_label": "", "has_error": False, "terms": terms,
            "port_defs": port_defs, "port_directives": {}, "tooltip": "",
            "directive": {}, "directives": [],
        }

    v1 = spec("V1", "SOURCE", {"P": term("VIN"), "N": ideal},
              [("P", "right", 0), ("N", "right", 1)])
    # U1 <- VIN (source) and RAIL_Y (from U2); U1 -> RAIL_X.
    u1 = spec("U1", "REGULATOR",
              {"IN_P": term("VIN"), "IN_P2": term("RAIL_Y"),
               "OUT_P": term("RAIL_X"), "OUT_N": ideal},
              [("IN_P", "left", 2), ("IN_P2", "left", 3),
               ("OUT_P", "right", 0), ("OUT_N", "right", 1)])
    # U2 <- RAIL_X (from U1); U2 -> RAIL_Y (back to U1) -> mutual cycle.
    u2 = spec("U2", "REGULATOR",
              {"IN_P": term("RAIL_X"), "OUT_P": term("RAIL_Y"), "OUT_N": ideal},
              [("IN_P", "left", 2), ("OUT_P", "right", 0), ("OUT_N", "right", 1)])

    cols, _returns, _parents = assign_columns([v1, u1, u2], {})
    assert cols["V1"] < cols["U1"] < cols["U2"]
    # No guard-limited inflation: the cycle-broken DAG's longest path is
    # V1 -> U1 -> U2, so columns compact to 0..2.
    assert max(cols.values()) <= len(cols) - 1
    # Deterministic regardless of spec order.
    assert assign_columns([u2, u1, v1], {}) == (cols, _returns, _parents)


def test_regulator_switch_node_output_uses_physical_net_for_columns() -> None:
    """REGULATOR OUT on a distinct downstream net (e.g. LX) must drive column order."""
    from fypa.topology.metadata.layout_bridge import assign_columns

    def term(net: str) -> dict:
        return {"requested_net": net, "pins": [{"net": net, "pad": "1"}]}

    ideal = {"ideal_return": True}

    def spec(nid: str, role: str, terms: dict, port_defs: list) -> dict:
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": role,
            "config_label": "",
            "has_error": False,
            "terms": terms,
            "port_defs": port_defs,
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    j13 = spec(
        "J13",
        "SOURCE",
        {"P": term("VDD_IN"), "N": ideal},
        [("P", "right", 0), ("N", "right", 1)],
    )
    u17 = spec(
        "U17.1",
        "REGULATOR",
        {
            "IN_P": term("VDD_IN"),
            "OUT_P": {
                "requested_net": "LX",
                "resolved_via_local": True,
                "pins": [{"net": "LX.1", "pad": "1"}],
            },
            "IN_N": ideal,
        },
        [("IN_P", "left", 0), ("OUT_P", "right", 1), ("IN_N", "left", 2)],
    )
    l6 = spec(
        "L6.1",
        "RESISTOR",
        {"P": term("LX.1"), "N": term("VDD_OUT")},
        [("P", "left", 0), ("N", "right", 1)],
    )
    cols, _ret, _par = assign_columns([j13, u17, l6], {"LX.1": "VDD_IN"})
    assert cols["J13"] < cols["U17.1"] < cols["L6.1"]


def test_connector_family_channels_share_one_column() -> None:
    """``J14.1`` / ``J14.2`` / ``J14.3`` stack in one column, not a fake L→R chain."""
    from fypa.topology.metadata.layout_bridge import assign_columns

    def term(net: str) -> dict:
        return {"requested_net": net, "pins": [{"net": net, "pad": "1"}]}

    def j_channel(nid: str, p_net: str, n_net: str) -> dict:
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": "RESISTOR",
            "config_label": "",
            "has_error": False,
            "terms": {"P1": term(p_net), "N1": term(n_net)},
            "port_defs": [("P1", "right", 0), ("N1", "right", 1)],
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    j1 = j_channel("J14.1", "AX1", "AY1")
    j2 = j_channel("J14.2", "AX2", "AY2")
    j3 = j_channel("J14.3", "AX3", "AY3")
    sink = {
        "node_id": "U27",
        "label": "U27",
        "designator": "U27",
        "role": "SINK",
        "config_label": "",
        "has_error": False,
        "terms": {
            "P1": term("AX3"),
            "N1": term("AY3"),
        },
        "port_defs": [("P1", "left", 0), ("N1", "left", 1)],
        "port_directives": {},
        "tooltip": "",
        "directive": {},
        "directives": [],
    }
    cols, _ret, _par = assign_columns([j1, j2, j3, sink], {})
    assert cols["J14.1"] == cols["J14.2"] == cols["J14.3"]
    assert cols["J14.3"] < cols["U27"]


def test_singleton_column_absorbs_left_when_safe() -> None:
    """Lone hop-1 switch beside a load column merges left when L→R allows."""
    from fypa.topology.metadata.layout_bridge import assign_columns

    def term(net: str) -> dict:
        return {"requested_net": net, "pins": [{"net": net, "pad": "1"}]}

    def series(nid: str, p_net: str, n_net: str) -> dict:
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": "SERIES",
            "config_label": "",
            "has_error": False,
            "terms": {"P": term(p_net), "N": term(n_net)},
            "port_defs": [("P", "left", 0), ("N", "right", 0)],
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    def sink(nid: str, net: str) -> dict:
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": "SINK",
            "config_label": "",
            "has_error": False,
            "terms": {"P": term(net)},
            "port_defs": [("P", "left", 0)],
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    src = {
        "node_id": "J1",
        "label": "J1",
        "designator": "J1",
        "role": "SOURCE",
        "config_label": "",
        "has_error": False,
        "terms": {"P": term("VIN")},
        "port_defs": [("P", "right", 0)],
        "port_directives": {},
        "tooltip": "",
        "directive": {},
        "directives": [],
    }
    # J1 → Q1 → L1 → U1, plus parallel Q2 that only feeds U1 (singleton mid col).
    q1 = series("Q1", "VIN", "MID")
    l1 = series("L1", "MID", "OUT")
    q2 = series("Q2", "VIN", "RAIL")
    u1 = sink("U1", "OUT")
    u2 = sink("U2", "RAIL")
    cols, _ret, _par = assign_columns([src, q1, l1, q2, u1, u2], {})
    # Q2 should not occupy a dedicated column alone between Q1 and sinks if it
    # can sit with Q1 (same driver side, loads only in sink column).
    assert cols["J1"] < cols["Q1"] <= cols["L1"] < cols["U1"]
    assert cols["Q2"] < cols["U2"]
    n_cols = len(set(cols.values()))
    assert n_cols <= 5, cols


def test_singleton_right_merges_into_next_column() -> None:
    """Compact+pack merges an ASAP singleton into the occupied column on its right."""
    from collections import defaultdict

    from fypa.topology.metadata.layout_bridge import (
        _direct_driver_ids,
        _direct_load_ids,
        _right_pack_columns,
    )

    def term(net: str) -> dict:
        return {"requested_net": net, "pins": [{"net": net, "pad": "1"}]}

    def series(nid: str, p_net: str, n_net: str) -> dict:
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": "SERIES",
            "config_label": "",
            "has_error": False,
            "terms": {"P": term(p_net), "N": term(n_net)},
            "port_defs": [("P", "left", 0), ("N", "right", 0)],
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    def sink(nid: str, net: str) -> dict:
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": "SINK",
            "config_label": "",
            "has_error": False,
            "terms": {"P": term(net)},
            "port_defs": [("P", "left", 0)],
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    # Crafted ASAP: lone Lx between Q8 and J2; Lx only loads the far sink.
    q8 = series("Q8", "A", "B")
    lx = series("Lx", "B", "FAR")
    j2 = series("J2", "X", "Y")
    u_far = sink("U_FAR", "FAR")
    u1 = sink("U1", "Y")
    specs = [q8, lx, j2, u_far, u1]
    col = {"Q8": 0, "Lx": 1, "J2": 2, "U_FAR": 3, "U1": 3}
    outputs_by_net = defaultdict(list)
    inputs_by_net = defaultdict(list)
    outputs_by_net["B"] = ["Q8"]
    inputs_by_net["B"] = ["Lx"]
    outputs_by_net["FAR"] = ["Lx"]
    inputs_by_net["FAR"] = ["U_FAR"]
    outputs_by_net["Y"] = ["J2"]
    inputs_by_net["Y"] = ["U1"]
    _right_pack_columns(
        col,
        specs,
        outputs_by_net,
        inputs_by_net,
        set(),
        {},
        set(),
        {},
    )
    assert col["Lx"] == col["J2"], col
    assert col["Q8"] < col["Lx"] < col["U_FAR"]
    assert _direct_load_ids("Lx", outputs_by_net, inputs_by_net, set(), {}) == [
        "U_FAR"
    ]
    assert "Q8" in _direct_driver_ids("Lx", outputs_by_net, inputs_by_net, set(), {})


def test_series_peer_stack_stays_contiguous() -> None:
    """Same-column SERIES peers that share nets pack within a tight Y span."""
    from fypa.topology.constants import ROW_GAP
    from fypa.topology.layout.vertical_align import (
        _spec_layout_height,
        assign_vertical_positions,
    )
    from fypa.topology.metadata.layout_bridge import ResolvedPort

    def make_spec(nid: str, nets: list[str]) -> dict:
        port_defs = [(f"P{i}", "right", i) for i, _n in enumerate(nets)]
        resolved = {
            f"P{i}": ResolvedPort(wnet=n, plabel=n, tooltip="")
            for i, n in enumerate(nets)
        }
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": "SERIES",
            "config_label": "",
            "has_error": False,
            "terms": {},
            "port_defs": port_defs,
            "resolved_ports": resolved,
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    # Three peers sharing NET_A; one would otherwise chase a far sink.
    q7 = make_spec("Q7", ["NET_A"])
    q15 = make_spec("Q15", ["NET_A", "NET_B"])
    q14 = make_spec("Q14", ["NET_A", "NET_C"])
    sink_hi = make_spec("U_HI", ["NET_B"])
    sink_hi["role"] = "SINK"
    sink_lo = make_spec("U_LO", ["NET_C"])
    sink_lo["role"] = "SINK"
    cols = {"Q7": 0, "Q15": 0, "Q14": 0, "U_HI": 1, "U_LO": 1}
    y = assign_vertical_positions(
        [q7, q15, q14, sink_hi, sink_lo], cols, max_col=1
    )
    peers = ("Q7", "Q15", "Q14")
    tops = [y[p] for p in peers]
    heights = [_spec_layout_height(s) for s in (q7, q15, q14)]
    span = max(t + h for t, h in zip(tops, heights)) - min(tops)
    tight = sum(heights) + ROW_GAP * 2
    assert span <= tight + ROW_GAP + 1.0, (span, tight, {p: y[p] for p in peers})


def test_compress_empty_bands_closes_gaps() -> None:
    """Unused vertical gaps within a column collapse to ROW_GAP packing."""
    from fypa.topology.constants import MARGIN, ROW_GAP
    from fypa.topology.layout.vertical_align import (
        _spec_layout_height,
        compress_empty_bands,
    )
    from fypa.topology.metadata.layout_bridge import ResolvedPort

    def make_spec(nid: str) -> dict:
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": "SERIES",
            "config_label": "",
            "has_error": False,
            "terms": {},
            "port_defs": [("P", "right", 0)],
            "resolved_ports": {"P": ResolvedPort(wnet="VIN", plabel="VIN", tooltip="")},
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    a = make_spec("A")
    b = make_spec("B")
    ha = _spec_layout_height(a)
    hb = _spec_layout_height(b)
    y_in = {"A": float(MARGIN), "B": float(MARGIN) + ha + 5 * ROW_GAP}
    y_out = compress_empty_bands(y_in, [a, b], {"A": 0, "B": 0})
    assert y_out["A"] == float(MARGIN)
    assert abs(y_out["B"] - (float(MARGIN) + ha + ROW_GAP)) < 1e-6


def test_hub_tap_escape_stays_outward_of_symbol_body() -> None:
    """When the stub column is blocked by a foreign vertical, the escape column
    must leave the port *outward* (away from the symbol body), not double back
    through it. Regression for the inverted ``outward_escape_stub_x``.
    """
    from fypa.topology.placement import port_stub_x
    from fypa.topology.routing.context import RoutingContext
    from fypa.topology.routing.paths import hub_tap_path, outward_escape_stub_x
    from fypa.topology.types import TopologyPort
    from fypa.topology.geometry import parse_wire_path

    def has_backtrack(path_d: str) -> bool:
        pts = parse_wire_path(path_d)
        for i in range(len(pts) - 2):
            (x0, y0), (x1, y1), (x2, y2) = pts[i], pts[i + 1], pts[i + 2]
            if abs(y0 - y1) < 0.5 and abs(y1 - y2) < 0.5:
                d1, d2 = x1 - x0, x2 - x1
                if d1 * d2 < 0 and abs(d1) > 0.5 and abs(d2) > 0.5:
                    return True
        return False

    # A right-side downstream port fed by a trunk further east; outward is +x.
    right = TopologyPort(terminal="P", net="VDD", label="", side="right",
                         x=200.0, y=100.0, node_id="U1")
    assert outward_escape_stub_x(right) > right.x  # away from body (west)
    ctx = RoutingContext()
    ctx.reserve_vertical(port_stub_x(right), 80.0, 120.0, "OTHER")
    path, _ = hub_tap_path(right, bus_x=260.0, obstacles=[], ctx=ctx, net="VDD")
    assert not has_backtrack(path), path

    # A left-side port; outward is -x, so the escape must sit west of the port,
    # never east into the node body.
    left = TopologyPort(terminal="P", net="VDD", label="", side="left",
                        x=200.0, y=100.0, node_id="U2")
    assert outward_escape_stub_x(left) < left.x  # away from body (east)


def test_probe_vdd_5v0_runs_above_u3() -> None:
    """VDD_5V0 gutter bus should clear U3 from above, not detour below it."""
    from pathlib import Path
    import pickle

    from fypa.topology.geometry import parse_wire_path

    probe = Path("_probe/project_b/topology.pkl")
    if not probe.is_file():
        probe = Path("_probe/topology.pkl")
    if not probe.is_file():
        return
    with probe.open("rb") as f:
        meta = pickle.load(f)
    model = build_topology_model(meta)
    by_des = {n.designator: n for n in model.nodes}
    if "U3" not in by_des or not any(w.net == "VDD_5V0" for w in model.wires):
        return
    u3_top = by_des["U3"].y
    tap = next(
        w for w in model.wires
        if w.net == "VDD_5V0"
        and w.routing_kind == "hub_tap"
        and w.src_node == by_des["U3"].node_id
    )
    ys = [y for _x, y in parse_wire_path(tap.path_d)]
    bus_y = min(ys)
    assert bus_y < u3_top - 1.0, tap.path_d


def test_probe_v_plus_minus_junction_near_connector() -> None:
    """V+/V- feeds merge near J2.1 / J2.2 (short stubs, trunk at sink column)."""
    from pathlib import Path
    import pickle

    from fypa.topology.geometry import parse_wire_path

    probe = Path("_probe/project_b/topology.pkl")
    if not probe.is_file():
        probe = Path("_probe/topology.pkl")
    if not probe.is_file():
        return
    with probe.open("rb") as f:
        meta = pickle.load(f)
    model = build_topology_model(meta)
    by_des = {n.designator: n for n in model.nodes}
    if "J2.1" not in by_des:
        return
    j21_x = next(
        p.x for n in model.nodes for p in n.ports
        if n.designator == "J2.1" and p.net == "V+"
    )
    vplus = [w for w in model.wires if w.net == "V+"]
    assert any(
        w.routing_kind == "hub_tap" and w.path_d.startswith(f"M {j21_x:.1f},")
        for w in vplus
    ), "J2.2 should drop vertically at the J2.1 column"
    vminus = [w for w in model.wires if w.net == "V-"]
    trunk = next(w for w in vminus if w.routing_kind == "hub")
    assert trunk.bus_x is not None and abs(trunk.bus_x - 712.0) < 1.0, (
        "V- trunk should sit on the J2 N-port stub column"
    )
    assert not any(
        "V 120.0" in w.path_d or "V 222.0" in w.path_d
        for w in vminus if w.routing_kind == "hub_tap"
    ), "V- J2 taps should not loop outward before joining the trunk"
    u4_vminus = next(
        w for w in vminus
        if w.src_node == by_des["U4"].node_id and w.routing_kind == "hub_tap"
    )
    verts = parse_wire_path(u4_vminus.path_d)
    assert verts[-1][0] >= 700.0, u4_vminus.path_d


def test_probe_stacked_stub_lengths_bottom_to_top() -> None:
    """Stacked edge stubs grow from bottom (short) to top (long)."""
    from pathlib import Path
    import pickle

    from fypa.topology.placement import port_stub_length

    probe = Path("_probe/project_b/topology.pkl")
    if not probe.is_file():
        probe = Path("_probe/topology.pkl")
    if not probe.is_file():
        return
    with probe.open("rb") as f:
        meta = pickle.load(f)
    model = build_topology_model(meta)
    d1 = next(n for n in model.nodes if n.designator == "D1")
    leds = sorted(
        [p for p in d1.ports if p.net.startswith("LED_")],
        key=lambda p: p.y,
    )
    assert len(leds) == 3
    lengths = [port_stub_length(p) for p in leds]
    assert lengths[0] > lengths[1] > lengths[2], lengths


def test_probe_regulator_power_gnd_share_wire_column() -> None:
    """Regulator power from above and GND below share one routing column."""
    from pathlib import Path
    import pickle

    from fypa.topology.placement import port_stub_x

    probe = Path("_probe/project_b/topology.pkl")
    if not probe.is_file():
        probe = Path("_probe/topology.pkl")
    if not probe.is_file():
        return
    with probe.open("rb") as f:
        meta = pickle.load(f)
    model = build_topology_model(meta)
    u2 = next(n for n in model.nodes if n.designator == "U2")
    left = [p for p in u2.ports if p.side == "left"]
    pwr = next(p for p in left if p.net != "__GND__")
    gnd = next(p for p in left if p.net == "__GND__")
    assert port_stub_x(pwr) == port_stub_x(gnd)


def test_probe_project_b_gutter_leds_route_via_stub_columns() -> None:
    """Stacked gutter LEDs use stub or bus columns with MIN_PARALLEL_GAP separation."""
    from fypa.topology import parse_wire_path, path_to_segments
    from fypa.topology.constants import MIN_PARALLEL_GAP
    from fypa.topology.placement import port_stub_x

    model = _load_probe_dir("project_b")
    if model is None:
        return
    d1 = next(n for n in model.nodes if n.designator == "D1")
    vertical_x: list[float] = []
    for net in ("LED_R", "LED_G", "LED_B"):
        port = next(p for p in d1.ports if p.net == net)
        wire = next(w for w in model.wires if w.net == net)
        segs = path_to_segments(net, parse_wire_path(wire.path_d))
        assert segs[0].orient == "H"
        v_segs = [s for s in segs if s.orient == "V"]
        assert v_segs, f"{net} should turn vertical toward the load"
        v_x = v_segs[0].x1
        vertical_x.append(v_x)
        assert v_x == port_stub_x(port) or v_x == wire.bus_x
    gaps = [abs(vertical_x[i + 1] - vertical_x[i]) for i in range(len(vertical_x) - 1)]
    assert all(g >= MIN_PARALLEL_GAP - 0.6 for g in gaps), gaps


def test_direct_neighbors_share_row_y():
    """Resistors align vertically with their directly connected sink load."""
    from pathlib import Path
    import pickle

    probe = Path("_probe/project_b/topology.pkl")
    if not probe.is_file():
        probe = Path("_probe/topology.pkl")
    if not probe.is_file():
        return
    with probe.open("rb") as f:
        meta = pickle.load(f)
    model = build_topology_model(meta)
    by_des = {n.designator: n for n in model.nodes if n.role != "GND"}
    if not all(d in by_des for d in ("L2", "L3", "L4", "U2", "U5", "U6")):
        return
    for left, right in (("L2", "U2"), ("L3", "U5"), ("L4", "U6")):
        assert abs(by_des[left].y - by_des[right].y) < 0.5, (
            f"{left} should align with {right}"
        )


def test_topology_project_b_hub_gutter_wide_enough():
    """Measured bus span must fit in the gutter between D1 and the U-stack."""

    meta = load_topology_fixture("project_b_hub_vdd")
    model = build_topology_model(meta)
    d1 = next(n for n in model.nodes if n.designator == "D1")
    u_stack = sorted(
        (n for n in model.nodes if n.designator.startswith("U")),
        key=lambda n: n.x,
    )[0]
    gap_width = u_stack.x - (d1.x + d1.width)
    bus_xs = sorted({
        round(w.bus_x, 1)
        for w in model.wires
        if w.bus_x is not None and w.net != "__GND__"
        and d1.x + d1.width <= w.bus_x <= u_stack.x
    })
    if len(bus_xs) >= 2:
        span = bus_xs[-1] - bus_xs[0]
        min_gap = (len(bus_xs) - 1) * MIN_PARALLEL_GAP
        assert span >= min_gap - 0.6
        assert gap_width >= span + 2 * PORT_WIRE_STUB - 4.0
    gutter_lo = d1.x + d1.width
    gutter_hi = u_stack.x
    for w in model.wires:
        if w.bus_x is None:
            continue
        bx = w.bus_x
        if not (gutter_lo <= bx <= gutter_hi):
            continue
        for n in model.nodes:
            if n.role == "GND":
                continue
            nx = n.x
            if nx <= bx <= nx + NODE_W:
                assert not (d1.x <= nx <= u_stack.x), (
                    f"bus_x={bx} inside node {n.designator}"
                )


def test_multi_role_stacked_symbol_geometry():
    """Section bands tile the composite symbol; each port sits in its own band."""
    meta = {
        "directives": [
            {
                "role": "SOURCE",
                "designator": "J1",
                "label": "J1",
                "value_str": "5 V",
                "terminals": {
                    "P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]},
                    "N": {"ideal_return": True, "pin_count": 0, "pins": []},
                },
            },
            {
                "role": "SERIES",
                "designator": "U2",
                "label": "U2",
                "channel_index": 1,
                "terminals": {
                    "P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]},
                    "N": {"requested_net": "VOUT", "pins": [{"net": "VOUT", "pad": "2"}]},
                },
            },
            {
                "role": "SINK",
                "designator": "U2",
                "label": "U2",
                "channel_index": 2,
                "terminals": {
                    "P": {"requested_net": "VCC", "pins": [{"net": "VCC", "pad": "3"}]},
                    "N": {"requested_net": "GND", "pins": [{"net": "GND", "pad": "4"}]},
                },
            },
        ],
    }
    model = build_topology_model(meta)
    u2_nodes = [n for n in model.nodes if n.designator == "U2"]
    assert len(u2_nodes) == 1
    u2 = u2_nodes[0]
    assert [s.role for s in u2.sections] == ["SERIES", "SINK"]

    # Bands start at the symbol top and tile it exactly.
    assert u2.sections[0].y == 0.0
    for above, below in zip(u2.sections, u2.sections[1:]):
        assert above.y + above.height == below.y
    last = u2.sections[-1]
    assert last.y + last.height == u2.height

    # Every port lands inside its own role band, below the band's header.
    assert u2.ports
    for port in u2.ports:
        sec = next(s for s in u2.sections if s.role == port.role)
        assert u2.y + sec.y + HEADER_H < port.y < u2.y + sec.y + sec.height, (
            f"port {port.terminal} ({port.role}) outside its section band"
        )


def test_shared_net_ports_align_across_unequal_channel_rows() -> None:
    """Connector and tall sink share AX on different channel indices → same port Y."""
    from fypa.topology.constants import BODY_PAD, HEADER_H, PORT_ROW_H
    from fypa.topology.layout.vertical_align import (
        _port_aligned_top_y,
        assign_vertical_positions,
    )
    from fypa.topology.metadata.layout_bridge import ResolvedPort

    def make_spec(nid: str, role: str, ports: list[tuple[str, str, int, str]]) -> dict:
        port_defs = [(pn, side, sk) for pn, side, sk, _net in ports]
        resolved = {
            pn: ResolvedPort(wnet=net, plabel=net, tooltip="")
            for pn, _side, _sk, net in ports
        }
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": role,
            "config_label": "",
            "has_error": False,
            "terms": {},
            "port_defs": port_defs,
            "resolved_ports": resolved,
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    # J14: AX on row 0; U27: VDD on row 0, AX on row 2 → tops must shift.
    j14 = make_spec(
        "J14.1",
        "RESISTOR",
        [("P1", "right", 0, "AX1"), ("N1", "right", 1, "AY1")],
    )
    u27 = make_spec(
        "U27.1",
        "SINK",
        [
            ("P1", "left", 0, "VDD"),
            ("N2", "left", 1, "OTHER"),
            ("N3", "left", 2, "AX1"),
        ],
    )
    cols = {"J14.1": 0, "U27.1": 1}
    y = assign_vertical_positions([j14, u27], cols, max_col=1)
    j_off = HEADER_H + BODY_PAD + 0 * PORT_ROW_H + PORT_ROW_H / 2
    u_off = HEADER_H + BODY_PAD + 2 * PORT_ROW_H + PORT_ROW_H / 2
    assert abs((y["J14.1"] + j_off) - (y["U27.1"] + u_off)) < 0.5
    # Helper used by the placer.
    aligned = _port_aligned_top_y(j14, u27, partner_top=100.0)
    assert abs(aligned - (100.0 + u_off - j_off)) < 0.5


def test_port_align_clamps_symbol_top_to_margin() -> None:
    """Deep shared-net rows must not push the upstream symbol above the canvas."""
    from fypa.topology.constants import BODY_PAD, HEADER_H, MARGIN, PORT_ROW_H
    from fypa.topology.layout.vertical_align import assign_vertical_positions
    from fypa.topology.metadata.layout_bridge import ResolvedPort

    def make_spec(nid: str, role: str, ports: list[tuple[str, str, int, str]]) -> dict:
        port_defs = [(pn, side, sk) for pn, side, sk, _net in ports]
        resolved = {
            pn: ResolvedPort(wnet=net, plabel=net, tooltip="")
            for pn, _side, _sk, net in ports
        }
        return {
            "node_id": nid,
            "label": nid,
            "designator": nid,
            "role": role,
            "config_label": "",
            "has_error": False,
            "terms": {},
            "port_defs": port_defs,
            "resolved_ports": resolved,
            "port_directives": {},
            "tooltip": "",
            "directive": {},
            "directives": [],
        }

    src = make_spec("J1", "RESISTOR", [("P1", "right", 0, "AX1")])
    sink = make_spec(
        "U1",
        "SINK",
        [("P1", "left", 0, "VDD"), ("N2", "left", 1, "OTHER"), ("N3", "left", 2, "AX1")],
    )
    y = assign_vertical_positions([src, sink], {"J1": 0, "U1": 1}, max_col=1)
    assert y["U1"] >= MARGIN
    assert y["J1"] >= MARGIN
    j_off = HEADER_H + BODY_PAD + 0 * PORT_ROW_H + PORT_ROW_H / 2
    u_off = HEADER_H + BODY_PAD + 2 * PORT_ROW_H + PORT_ROW_H / 2
    # Alignment may yield to the margin floor when the ideal top undershoots.
    assert y["J1"] + j_off >= y["U1"] + u_off - 0.5 or abs(y["J1"] - MARGIN) < 0.5


def test_hub_bus_nominal_prefers_densest_stub_cluster() -> None:
    """Trunk sits on the stub column shared by most ports, not the far destination."""
    from fypa.topology.placement.hub_planning import hub_bus_nominal_x
    from fypa.topology.types import TopologyPort

    def p(nid: str, x: float, side: str) -> TopologyPort:
        return TopologyPort(
            terminal="P",
            net="VDD",
            label="VDD",
            side=side,
            x=x,
            y=100.0,
            node_id=nid,
        )

    # Five left stubs at x=3168 (stub 3148) + one far sink at 3872 (stub 3852)
    # + one driver at 2912 (stub 2932).
    ports = [
        p("U7", 2912.0, "right"),
        p("L1", 3168.0, "left"),
        p("L2", 3168.0, "left"),
        p("U27.1", 3168.0, "left"),
        p("U27.2", 3168.0, "left"),
        p("U27.3", 3168.0, "left"),
        p("U3", 3872.0, "left"),
    ]
    nominal = hub_bus_nominal_x(ports, bus_lo=2900.0, bus_hi=3900.0)
    assert abs(nominal - 3148.0) < 1.0, nominal
