"""SERIES/RESISTOR port faces are fixed: P left (in), N right (out).

See ``fypa/topology/RULES.md``. Peer-facing flips are not applied.
"""

from __future__ import annotations

from fypa.topology import build_topology_model


def _term(net: str, pad: str, *, pin_net: str | None = None) -> dict:
    return {"requested_net": net, "pins": [{"net": pin_net or net, "pad": pad}]}


def _resistor_sides(model) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for n in model.nodes:
        if n.role in ("RESISTOR", "SERIES"):
            out[n.designator] = {p.terminal: p.side for p in n.ports}
    return out


def test_series_keeps_p_left_n_right_even_when_loads_on_p():
    """Driver on N, loads on P — faces stay P-left / N-right (no flip)."""
    meta = {
        "directives": [
            {
                "role": "SOURCE",
                "designator": "J1",
                "label": "J1",
                "value_str": "5 V",
                "terminals": {
                    "P": _term("RAIL", "1", pin_net="PRE"),
                    "N": _term("GND", "2"),
                },
            },
            {
                "role": "RESISTOR",
                "designator": "R1",
                "label": "R1",
                "value_str": "0 mOhm",
                "terminals": {"P": _term("RAIL", "1"), "N": _term("PRE", "2")},
            },
            {
                "role": "SINK",
                "designator": "U1",
                "label": "U1",
                "value_str": "10 mA",
                "terminals": {"P": _term("RAIL", "1"), "N": _term("GND", "2")},
            },
            {
                "role": "SINK",
                "designator": "U2",
                "label": "U2",
                "value_str": "10 mA",
                "terminals": {"P": _term("RAIL", "1"), "N": _term("GND", "2")},
            },
        ]
    }
    sides = _resistor_sides(build_topology_model(meta))["R1"]
    assert sides["P"] == "left"
    assert sides["N"] == "right"


def test_mid_rail_tap_keeps_default_orientation():
    meta = {
        "directives": [
            {
                "role": "SOURCE",
                "designator": "J1",
                "label": "J1",
                "value_str": "5 V",
                "terminals": {"P": _term("RAIL", "1"), "N": _term("GND", "2")},
            },
            {
                "role": "RESISTOR",
                "designator": "R1",
                "label": "R1",
                "value_str": "0 mOhm",
                "terminals": {"P": _term("RAIL", "1"), "N": _term("PRE", "2")},
            },
            {
                "role": "SINK",
                "designator": "U1",
                "label": "U1",
                "value_str": "10 mA",
                "terminals": {"P": _term("RAIL", "1"), "N": _term("GND", "2")},
            },
        ]
    }
    sides = _resistor_sides(build_topology_model(meta))["R1"]
    assert sides["P"] == "left"
    assert sides["N"] == "right"


def test_loop_series_ports_keep_fixed_faces():
    """Loop SERIES also keep P left / N right (no all-ports-face-parent)."""
    from tests.topology_fixtures import load_topology_fixture

    model = build_topology_model(load_topology_fixture("project_a_stepper_loop_rails"))
    sides = _resistor_sides(model)
    for _des, face_map in sides.items():
        for term, side in face_map.items():
            if term.startswith("P"):
                assert side == "left", f"{_des}.{term} should be left"
            elif term.startswith("N"):
                assert side == "right", f"{_des}.{term} should be right"
