"""Tests for schematic-guided topology layout seeding."""

from __future__ import annotations

from fypa.topology import build_topology_model
from fypa.topology.metadata.schematic_seed import (
    SCHEMATIC_SEED_COVERAGE,
    schematic_coverage,
    schematic_seed_placement,
)
from fypa.topology.metadata_schema import NodeSpec


def _spec(
    node_id: str,
    *,
    role: str = "SOURCE",
    sch_x: float | None = None,
    sch_y: float | None = None,
    schdoc: str = "Main.SchDoc",
    orient: int = 0,
    mirrored: bool = False,
    ports: list | None = None,
) -> NodeSpec:
    d: NodeSpec = {
        "node_id": node_id,
        "label": node_id,
        "designator": node_id,
        "role": role,
        "config_label": "",
        "has_error": False,
        "terms": {},
        "port_defs": ports or [("P", "right", 0), ("N", "left", 1)],
        "port_directives": {},
        "tooltip": node_id,
        "directive": {"designator": node_id, "role": role},
        "directives": [{"designator": node_id, "role": role}],
        "schdoc": schdoc,
    }
    if sch_x is not None and sch_y is not None:
        d["sch_x"] = sch_x
        d["sch_y"] = sch_y
        d["sch_orientation_deg"] = orient
        d["sch_mirrored"] = mirrored
    return d


def test_schematic_seed_orders_columns_by_x_and_y():
    specs = [
        _spec("A", role="REGULATOR", sch_x=100, sch_y=200),
        _spec("B", role="REGULATOR", sch_x=100, sch_y=50),
        _spec("C", role="REGULATOR", sch_x=500, sch_y=200),
    ]
    seed = schematic_seed_placement(specs, graph_columns={"A": 0, "B": 0, "C": 1})
    assert seed is not None
    assert seed.columns["A"] == seed.columns["B"]
    assert seed.columns["C"] > seed.columns["A"]
    assert seed.orders["A"] < seed.orders["B"]  # A above B


def test_schematic_seed_skips_when_coverage_low():
    specs = [
        _spec("A", sch_x=1, sch_y=1),
        _spec("B"),
        _spec("C"),
    ]
    assert schematic_coverage(specs) < SCHEMATIC_SEED_COVERAGE
    assert schematic_seed_placement(specs) is None


def test_schematic_seed_flips_ports_when_mirrored():
    specs = [
        _spec("A", sch_x=10, sch_y=10, mirrored=True),
        _spec("B", sch_x=20, sch_y=10),
    ]
    seed = schematic_seed_placement(specs)
    assert seed is not None
    assert seed.port_defs["A"][0][1] == "left"   # was right
    assert seed.port_defs["A"][1][1] == "right"  # was left
    assert "B" not in seed.port_defs


def test_schematic_seed_sheets_share_columns():
    """Subsheets pack into one shared column space — no block offset per sheet."""
    specs = [
        _spec("A", role="REGULATOR", sch_x=10, sch_y=10, schdoc="mod/ChildB.SchDoc"),
        _spec("B", role="REGULATOR", sch_x=10, sch_y=10, schdoc="mod/ChildA.SchDoc"),
    ]
    meta = {
        "sch_sheet_placements": [
            {"filename": "ChildA.SchDoc", "x": 100.0},
            {"filename": "ChildB.SchDoc", "x": 500.0},
        ],
    }
    seed = schematic_seed_placement(specs, metadata=meta)
    assert seed is not None
    assert seed.columns["A"] == seed.columns["B"]
    # Sheet-symbol X only breaks within-column order (ChildA before ChildB).
    assert seed.orders["B"] < seed.orders["A"]


def test_schematic_seed_same_basename_sheets_can_share():
    specs = [
        _spec("A", role="REGULATOR", sch_x=10, sch_y=10, schdoc="pwr/Rail.SchDoc"),
        _spec("B", role="REGULATOR", sch_x=10, sch_y=10, schdoc="io/Rail.SchDoc"),
    ]
    seed = schematic_seed_placement(specs)
    assert seed is not None
    assert seed.columns["A"] == seed.columns["B"]


def test_schematic_seed_sources_first_sinks_last():
    specs = [
        _spec("SRC", role="SOURCE", sch_x=900, sch_y=100, schdoc="a.SchDoc"),
        _spec("MID", role="REGULATOR", sch_x=500, sch_y=100, schdoc="b.SchDoc"),
        _spec("SNK", role="SINK", sch_x=100, sch_y=100, schdoc="c.SchDoc"),
    ]
    # Graph put the source oddly far right — seed must still prefer edges.
    seed = schematic_seed_placement(
        specs,
        graph_columns={"SRC": 2, "MID": 1, "SNK": 0},
    )
    assert seed is not None
    assert seed.columns["SRC"] == 0
    assert seed.columns["SNK"] == max(seed.columns.values())
    assert seed.columns["SNK"] > seed.columns["SRC"]


def test_schematic_seed_multi_sheet_does_not_inflate_width():
    """Many single-node sheets must not each claim a new column."""
    specs = [
        _spec(f"S{i}", role="SINK", sch_x=100.0 + i, sch_y=50.0, schdoc=f"con{i}.SchDoc")
        for i in range(8)
    ] + [
        _spec("SRC", role="SOURCE", sch_x=10, sch_y=50, schdoc="pwr.SchDoc"),
    ]
    graph = {s["node_id"]: (0 if s["role"] == "SOURCE" else 3) for s in specs}
    seed = schematic_seed_placement(specs, graph_columns=graph)
    assert seed is not None
    assert max(seed.columns.values()) <= 3
    assert seed.columns["SRC"] == 0
    assert all(seed.columns[s["node_id"]] == max(seed.columns.values()) for s in specs if s["role"] == "SINK")


def test_schematic_seed_compresses_sparse_graph_columns():
    """Long graph column chains are bucketed into SCHEMATIC_MAX_COLUMNS."""
    from fypa.topology.metadata.schematic_seed import SCHEMATIC_MAX_COLUMNS

    specs = [
        _spec("SRC", role="SOURCE", sch_x=0, sch_y=0, schdoc="a.SchDoc"),
    ]
    graph = {"SRC": 0}
    for i in range(1, 12):
        nid = f"R{i}"
        specs.append(
            _spec(nid, role="RESISTOR", sch_x=float(i * 10), sch_y=0, schdoc=f"s{i}.SchDoc"),
        )
        graph[nid] = i
    specs.append(_spec("SNK", role="SINK", sch_x=200, sch_y=0, schdoc="z.SchDoc"))
    graph["SNK"] = 12
    seed = schematic_seed_placement(specs, graph_columns=graph)
    assert seed is not None
    assert max(seed.columns.values()) < 12
    assert max(seed.columns.values()) <= SCHEMATIC_MAX_COLUMNS - 1
    assert seed.columns["SRC"] == 0
    assert seed.columns["SNK"] == max(seed.columns.values())
    # No empty column holes after compaction.
    used = set(seed.columns.values())
    assert used == set(range(max(used) + 1))


def test_build_model_toggle_off_ignores_sch_coords():
    meta = {
        "directives": [
            {
                "role": "SOURCE",
                "designator": "JA",
                "label": "JA",
                "value_str": "1 V",
                "schdoc": "Pwr.SchDoc",
                "sch_x": 10.0,
                "sch_y": 100.0,
                "terminals": {
                    "P": {"requested_net": "NET_A", "pins": [{"net": "NET_A", "pad": "1"}]},
                    "N": {"ideal_return": True, "pin_count": 0, "pins": []},
                },
            },
            {
                "role": "SOURCE",
                "designator": "JB",
                "label": "JB",
                "value_str": "1 V",
                "schdoc": "Pwr.SchDoc",
                "sch_x": 10.0,
                "sch_y": 10.0,
                "terminals": {
                    "P": {"requested_net": "NET_B", "pins": [{"net": "NET_B", "pad": "1"}]},
                    "N": {"ideal_return": True, "pin_count": 0, "pins": []},
                },
            },
        ],
    }
    on = build_topology_model(meta, use_schematic_layout=True)
    off = build_topology_model(meta, use_schematic_layout=False)
    by_on = {n.designator: n for n in on.nodes if n.role != "GND"}
    by_off = {n.designator: n for n in off.nodes if n.role != "GND"}
    # With schematic on, JA (higher Y) is above JB.
    assert by_on["JA"].y < by_on["JB"].y
    # Toggle off still builds; positions come from graph heuristics (may differ).
    assert by_off["JA"].node_id and by_off["JB"].node_id
