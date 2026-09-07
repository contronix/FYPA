"""SERIES/RESISTOR port faces: P left / N right; loop returns peer-facing.

See ``fypa/topology/RULES.md`` rules 12–13 and 19.
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
                "terminals": {"P": _term("PRE", "1"), "N": _term("GND", "2")},
            },
        ]
    }
    sides = _resistor_sides(build_topology_model(meta))["R1"]
    assert sides["P"] == "left"
    assert sides["N"] == "right"


def test_loop_series_return_ports_face_peer():
    """Loop return nets: parent P* → right, child N* → left (RULES.md §19)."""
    from tests.topology_fixtures import load_topology_fixture

    model = build_topology_model(load_topology_fixture("project_a_stepper_loop_rails"))
    assert "AY" in model.loop_return_nets
    assert "BY" in model.loop_return_nets
    assert model.loop_parent.get("J7") == "U1"
    sides = _resistor_sides(model)
    # Forward nets keep default faces.
    assert sides["U1"]["N1"] == "right"  # AX out
    assert sides["J7"]["P1"] == "left"  # AX in
    # Return nets face the peer gutter.
    assert sides["U1"]["P2"] == "right"  # AY in on parent
    assert sides["J7"]["N1"] == "left"  # AY out on child
    assert sides["U1"]["P4"] == "right"  # BY in on parent
    assert sides["J7"]["N2"] == "left"  # BY out on child
