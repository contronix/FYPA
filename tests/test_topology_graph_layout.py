"""Graph column packing and peer-facing port orientation."""

from __future__ import annotations

from fypa.topology.metadata.layout_bridge import (
    assign_columns,
    compress_column_ranks,
    orient_ports_toward_peers,
)
from fypa.topology.metadata_schema import NodeSpec


def _spec(
    node_id: str,
    *,
    role: str,
    ports: list | None = None,
    terms: dict | None = None,
) -> NodeSpec:
    port_defs = ports or [("P", "right", 0), ("N", "left", 1)]
    return {
        "node_id": node_id,
        "label": node_id,
        "designator": node_id,
        "role": role,
        "config_label": "",
        "has_error": False,
        "terms": terms or {},
        "port_defs": port_defs,
        "port_directives": {},
        "tooltip": node_id,
        "directive": {"designator": node_id, "role": role},
        "directives": [{"designator": node_id, "role": role}],
        "resolved_ports": {},
    }


def test_compress_column_ranks_buckets_sparse_chain():
    cols = {f"n{i}": i for i in range(12)}
    out = compress_column_ranks(cols, 4)
    assert max(out.values()) <= 3
    assert out["n0"] == 0
    assert out["n11"] == 3
    assert set(out.values()) == {0, 1, 2, 3}


def test_assign_columns_packs_long_series_chain():
    """A long SERIES hop chain keeps left→right flow without index holes."""
    specs = [
        _spec(
            "SRC",
            role="SOURCE",
            ports=[("P", "right", 0), ("N", "left", 1)],
            terms={
                "P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]},
                "N": {"ideal_return": True, "pins": []},
            },
        ),
    ]
    prev = "VIN"
    for i in range(1, 10):
        n_net = f"N{i}"
        specs.append(
            _spec(
                f"R{i}",
                role="RESISTOR",
                ports=[("P", "left", 0), ("N", "right", 1)],
                terms={
                    "P": {
                        "requested_net": prev,
                        "pins": [{"net": prev, "pad": "1"}],
                    },
                    "N": {
                        "requested_net": n_net,
                        "pins": [{"net": n_net, "pad": "2"}],
                    },
                },
            )
        )
        prev = n_net
    specs.append(
        _spec(
            "SNK",
            role="SINK",
            ports=[("P", "left", 0), ("N", "left", 1)],
            terms={
                "P": {
                    "requested_net": prev,
                    "pins": [{"net": prev, "pad": "1"}],
                },
                "N": {"ideal_return": True, "pins": []},
            },
        ),
    )
    from fypa.topology.metadata.layout_bridge import _enrich_resolved_ports

    for s in specs:
        _enrich_resolved_ports(s)
    col = assign_columns(specs, {})
    assert col["SRC"] == 0
    assert col["SNK"] == max(col.values())
    assert set(col.values()) == set(range(max(col.values()) + 1))
    for i in range(2, 10):
        assert col[f"R{i}"] >= col[f"R{i - 1}"], col
    # Soft packing still bounds extreme width; flow order wins over a hard cap.
    assert max(col.values()) <= 12


def test_series_load_sits_right_of_series_driver():
    """Downstream SERIES must not share a column with its SERIES driver."""
    from fypa.topology.metadata.layout_bridge import (
        _enrich_resolved_ports,
        assign_columns,
    )

    driver = _spec(
        "R_DRV",
        role="RESISTOR",
        ports=[("P", "left", 0), ("N", "right", 1)],
        terms={
            "P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]},
            "N": {"requested_net": "VMID", "pins": [{"net": "VMID", "pad": "2"}]},
        },
    )
    load = _spec(
        "R_LOAD",
        role="RESISTOR",
        ports=[("P", "left", 0), ("N", "right", 1)],
        terms={
            "P": {"requested_net": "VMID", "pins": [{"net": "VMID", "pad": "1"}]},
            "N": {"requested_net": "VOUT", "pins": [{"net": "VOUT", "pad": "2"}]},
        },
    )
    for s in (driver, load):
        _enrich_resolved_ports(s)
    cols = assign_columns([driver, load], {})
    assert cols["R_DRV"] < cols["R_LOAD"], cols


def test_orient_ports_toward_peers_faces_neighbor_column():
    left = _spec(
        "A",
        role="SOURCE",
        ports=[("P", "left", 0)],  # wrong face on purpose
        terms={"P": {"requested_net": "VDD", "pins": [{"net": "VDD", "pad": "1"}]}},
    )
    right = _spec(
        "B",
        role="SINK",
        ports=[("P", "right", 0)],  # wrong face on purpose
        terms={"P": {"requested_net": "VDD", "pins": [{"net": "VDD", "pad": "1"}]}},
    )
    from fypa.topology.metadata.layout_bridge import ResolvedPort

    left["resolved_ports"] = {"P": ResolvedPort(wnet="VDD", plabel="VDD", tooltip="")}
    right["resolved_ports"] = {"P": ResolvedPort(wnet="VDD", plabel="VDD", tooltip="")}
    cols = {"A": 0, "B": 2}
    orient_ports_toward_peers([left, right], cols, {})
    assert left["port_defs"][0][1] == "right"
    assert right["port_defs"][0][1] == "left"


def test_same_side_ports_get_distinct_rows_after_peer_orient():
    """Peer-facing must not stack two ports on the same edge at one Y."""
    from fypa.topology.metadata.layout_bridge import (
        ResolvedPort,
        ensure_unique_port_rows,
        orient_ports_toward_peers,
    )

    src = _spec(
        "SRC",
        role="SOURCE",
        ports=[("P", "right", 0), ("N", "right", 0)],  # collide on purpose
        terms={
            "P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]},
            "N": {"requested_net": "VRET", "pins": [{"net": "VRET", "pad": "2"}]},
        },
    )
    snk_a = _spec(
        "A",
        role="SINK",
        ports=[("P", "left", 0)],
        terms={"P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]}},
    )
    snk_b = _spec(
        "B",
        role="SINK",
        ports=[("P", "left", 0)],
        terms={"P": {"requested_net": "VRET", "pins": [{"net": "VRET", "pad": "1"}]}},
    )
    src["resolved_ports"] = {
        "P": ResolvedPort(wnet="VIN", plabel="VIN", tooltip=""),
        "N": ResolvedPort(wnet="VRET", plabel="VRET", tooltip=""),
    }
    snk_a["resolved_ports"] = {"P": ResolvedPort(wnet="VIN", plabel="VIN", tooltip="")}
    snk_b["resolved_ports"] = {"P": ResolvedPort(wnet="VRET", plabel="VRET", tooltip="")}
    cols = {"SRC": 0, "A": 1, "B": 1}
    orient_ports_toward_peers([src, snk_a, snk_b], cols, {})
    ensure_unique_port_rows([src, snk_a, snk_b])
    right = [(p, sk) for p, side, sk in src["port_defs"] if side == "right"]
    assert len(right) == 2
    assert right[0][1] != right[1][1]


def test_series_keeps_through_flow_despite_peers_on_one_side():
    """SERIES/RESISTOR stay left↔right; peer-orient must not stack both faces."""
    from fypa.topology.metadata.layout_bridge import (
        ResolvedPort,
        orient_ports_toward_peers,
    )

    series = _spec(
        "R1",
        role="RESISTOR",
        ports=[("P", "left", 0), ("N", "right", 1)],
        terms={
            "P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]},
            "N": {"requested_net": "VOUT", "pins": [{"net": "VOUT", "pad": "2"}]},
        },
    )
    # Both nets only connect to peers further right → peer-orient would want
    # both ports on the right if SERIES were not exempt.
    right_a = _spec(
        "A",
        role="SINK",
        ports=[("P", "left", 0)],
        terms={"P": {"requested_net": "VIN", "pins": [{"net": "VIN", "pad": "1"}]}},
    )
    right_b = _spec(
        "B",
        role="SINK",
        ports=[("P", "left", 0)],
        terms={"P": {"requested_net": "VOUT", "pins": [{"net": "VOUT", "pad": "1"}]}},
    )
    series["resolved_ports"] = {
        "P": ResolvedPort(wnet="VIN", plabel="VIN", tooltip=""),
        "N": ResolvedPort(wnet="VOUT", plabel="VOUT", tooltip=""),
    }
    right_a["resolved_ports"] = {"P": ResolvedPort(wnet="VIN", plabel="VIN", tooltip="")}
    right_b["resolved_ports"] = {"P": ResolvedPort(wnet="VOUT", plabel="VOUT", tooltip="")}
    orient_ports_toward_peers(
        [series, right_a, right_b],
        {"R1": 0, "A": 1, "B": 1},
        {},
    )
    sides = {pname: side for pname, side, _ in series["port_defs"]}
    assert sides["P"] == "left"
    assert sides["N"] == "right"


def test_series_after_regulator_sits_right_of_switch_node():
    """Inductor on a regulator LX net must land right of the regulator.

    Rail maps sometimes fold ``LX.1`` into the upstream rail name; column
    connectivity must still see the regulator OUT as the series upstream.
    """
    from fypa.topology.metadata.layout_bridge import assign_columns

    reg = _spec(
        "U1",
        role="REGULATOR",
        ports=[("IN_P", "left", 0), ("OUT_P", "right", 1)],
        terms={
            "IN_P": {
                "requested_net": "VIN",
                "pins": [{"net": "VIN", "pad": "1"}],
            },
            "OUT_P": {
                "requested_net": "LX",
                "pins": [{"net": "LX.1", "pad": "2"}],
            },
        },
    )
    ind = _spec(
        "L1",
        role="RESISTOR",
        ports=[("P", "left", 0), ("N", "right", 1)],
        terms={
            "P": {
                "requested_net": "LX",
                "pins": [{"net": "LX.1", "pad": "1"}],
            },
            "N": {
                "requested_net": "VOUT",
                "pins": [{"net": "VOUT", "pad": "2"}],
            },
        },
    )
    snk = _spec(
        "J1",
        role="SINK",
        ports=[("P", "left", 0)],
        terms={
            "P": {
                "requested_net": "VOUT",
                "pins": [{"net": "VOUT", "pad": "1"}],
            },
        },
    )
    from fypa.topology.metadata.layout_bridge import _enrich_resolved_ports

    for s in (reg, ind, snk):
        _enrich_resolved_ports(s)
    # Hostile rail map: physical LX collapses onto the upstream rail name.
    cols = assign_columns(
        [reg, ind, snk],
        {"LX.1": "VIN", "LX": "VIN", "VOUT": "VIN", "VIN": "VIN"},
    )
    assert cols["U1"] < cols["L1"], cols
    assert cols["L1"] <= cols["J1"], cols


def test_nettie_keeps_distinct_physical_flow_columns():
    """0 Ω / net-tie SERIES must not merge upstream and downstream into one hop.

    Even when the solver puts both nets in one rail group, placement follows
    copper: SOURCE → bridge → SINK stays left-to-right on distinct nets.
    """
    from fypa.topology.metadata.layout_bridge import (
        _enrich_resolved_ports,
        assign_columns,
    )

    src = _spec(
        "J1",
        role="SOURCE",
        ports=[("P", "right", 0)],
        terms={"P": {"requested_net": "VDD_A", "pins": [{"net": "VDD_A", "pad": "1"}]}},
    )
    bridge = _spec(
        "R1",
        role="SERIES",
        ports=[("P", "left", 0), ("N", "right", 1)],
        terms={
            "P": {"requested_net": "VDD_A", "pins": [{"net": "VDD_A", "pad": "1"}]},
            "N": {"requested_net": "VDD_B", "pins": [{"net": "VDD_B", "pad": "2"}]},
        },
    )
    snk = _spec(
        "U1",
        role="SINK",
        ports=[("P", "left", 0)],
        terms={"P": {"requested_net": "VDD_B", "pins": [{"net": "VDD_B", "pad": "1"}]}},
    )
    for s in (src, bridge, snk):
        _enrich_resolved_ports(s)
    rail = {"VDD_A": "VDD_RAIL", "VDD_B": "VDD_RAIL", "VDD_RAIL": "VDD_RAIL"}
    cols = assign_columns([src, bridge, snk], rail)
    assert cols["J1"] < cols["R1"] < cols["U1"], cols


def test_canvas_fits_wires_routed_above_origin():
    """Detours above y=0 must expand/shift the canvas so nothing is clipped."""
    from fypa.topology.builder import _fit_canvas_to_content
    from fypa.topology.geometry import parse_wire_path
    from fypa.topology.types import TopologyNode, TopologyWire

    node = TopologyNode(
        node_id="U1",
        label="U1",
        designator="U1",
        role="SINK",
        x=36.0,
        y=36.0,
        width=128.0,
        height=56.0,
        config_label="",
        has_error=False,
        bounds=(36.0, 36.0, 128.0, 56.0),
    )
    wire = TopologyWire(
        net="VDD",
        path_d="M 36.0,50.0 H 20.0 V -40.0 H 200.0",
        routing_kind="hub_tap",
        bus_x=200.0,
    )
    width, height, _gy, _gx = _fit_canvas_to_content(
        [node],
        [wire],
        gnd_bus_y=None,
        gnd_symbol_x=None,
        needs_gnd=False,
    )
    ys = [y for _, y in parse_wire_path(wire.path_d)]
    assert min(ys) >= 36.0 - 0.6
    assert node.y >= 36.0 - 0.6
    assert width >= 200.0 + 36.0 - 0.6
    assert height > 56.0


def test_multi_column_gutter_does_not_inflate_every_gap():
    """Buses on a long SOURCE→SINK span must not widen every intervening gap."""
    from fypa.topology.constants import COL_GAP, NODE_W
    from fypa.topology.layout.columns import _required_gaps
    from fypa.topology.placement.plan_types import BusPlan
    from fypa.topology.types import TopologyPort

    # Three column gaps; ports span col0→col3 like a long rail.
    col_x = [36.0, 36.0 + NODE_W + COL_GAP, 36.0 + 2 * (NODE_W + COL_GAP),
             36.0 + 3 * (NODE_W + COL_GAP)]
    ports = [
        TopologyPort(terminal="P", net="VIN", label="VIN", side="right",
                     x=col_x[0] + NODE_W, y=50.0, node_id="SRC"),
        TopologyPort(terminal="P", net="VIN", label="VIN", side="left",
                     x=col_x[3], y=50.0, node_id="SNK"),
        TopologyPort(terminal="P", net="VOUT", label="VOUT", side="right",
                     x=col_x[0] + NODE_W, y=80.0, node_id="SRC"),
        TopologyPort(terminal="P", net="VOUT", label="VOUT", side="left",
                     x=col_x[3], y=80.0, node_id="SNK"),
    ]
    plan = BusPlan()
    # Two buses parked in different corridors (not one fat shared gutter).
    plan.pair_buses["VIN"] = col_x[0] + NODE_W + COL_GAP / 2
    plan.pair_buses["VOUT"] = col_x[2] + NODE_W + COL_GAP / 2
    gaps = _required_gaps(ports, col_x, 3, COL_GAP, plan)
    # Previously each gap became ~full board width; keep them near COL_GAP.
    assert max(gaps) < NODE_W + 2 * COL_GAP
