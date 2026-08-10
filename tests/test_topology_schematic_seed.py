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
        "role": "SOURCE",
        "config_label": "",
        "has_error": False,
        "terms": {},
        "port_defs": ports or [("P", "right", 0), ("N", "left", 1)],
        "port_directives": {},
        "tooltip": node_id,
        "directive": {"designator": node_id, "role": "SOURCE"},
        "directives": [{"designator": node_id, "role": "SOURCE"}],
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
        _spec("A", sch_x=100, sch_y=200),
        _spec("B", sch_x=100, sch_y=50),   # below A (smaller Y → higher order)
        _spec("C", sch_x=500, sch_y=200),  # right column
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


def test_schematic_seed_sheet_blocks_use_symbol_x():
    specs = [
        _spec("A", sch_x=10, sch_y=10, schdoc="mod/ChildB.SchDoc"),
        _spec("B", sch_x=10, sch_y=10, schdoc="mod/ChildA.SchDoc"),
    ]
    meta = {
        "sch_sheet_placements": [
            {"filename": "ChildA.SchDoc", "x": 100.0},
            {"filename": "ChildB.SchDoc", "x": 500.0},
        ],
    }
    seed = schematic_seed_placement(specs, metadata=meta)
    assert seed is not None
    # ChildA left of ChildB → B's sheet block comes first
    assert seed.columns["B"] < seed.columns["A"]


def test_schematic_seed_keeps_same_basename_sheets_separate():
    specs = [
        _spec("A", sch_x=10, sch_y=10, schdoc="pwr/Rail.SchDoc"),
        _spec("B", sch_x=10, sch_y=10, schdoc="io/Rail.SchDoc"),
    ]
    seed = schematic_seed_placement(specs)
    assert seed is not None
    assert seed.columns["A"] != seed.columns["B"]


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
