"""Tests for metadata component spec parsing."""

from __future__ import annotations

from fypa.topology.metadata.specs import directives_to_component_specs


def test_sink_multi_channel_port_names():
    directives = [
        {
            "role": "SINK",
            "designator": "U1",
            "channel_index": 1,
            "terminals": {
                "P": {"pins": [{"net": "VDD", "pad": "1"}]},
                "N": {"pins": [{"net": "GND", "pad": "2"}]},
            },
        },
        {
            "role": "SINK",
            "designator": "U1",
            "channel_index": 2,
            "terminals": {
                "P": {"pins": [{"net": "VDD", "pad": "3"}]},
                "N": {"pins": [{"net": "GND", "pad": "2"}]},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    assert len(specs) == 1
    pnames = [p[0] for p in specs[0]["port_defs"]]
    assert "P1" in pnames and "P2" in pnames
    assert pnames.count("N") == 1


def test_source_multi_channel_coface_power_above_return():
    """SOURCE P*/N* share the right face; power rows pack above shared return."""
    from fypa.topology.constants import RETURN_PORT_GND_SORT_BASE, RETURN_PORT_SORT_BASE
    from fypa.topology.layout.vertical_align import port_layout_rows

    directives = [
        {
            "role": "SOURCE",
            "designator": "J1",
            "channel_index": 1,
            "terminals": {
                "P": {"pins": [{"net": "VDD_A", "pad": "1"}]},
                "N": {"pins": [{"net": "GND", "pad": "3"}]},
            },
        },
        {
            "role": "SOURCE",
            "designator": "J1",
            "channel_index": 2,
            "terminals": {
                "P": {"pins": [{"net": "VDD_B", "pad": "2"}]},
                "N": {"pins": [{"net": "GND", "pad": "3"}]},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    assert len(specs) == 1
    port_defs = specs[0]["port_defs"]
    assert all(side == "right" for _n, side, _sk in port_defs)
    pnames = [p[0] for p in port_defs]
    assert "P1" in pnames and "P2" in pnames
    assert pnames.count("N") == 1

    power = sorted(
        (sk for n, _s, sk in port_defs if n.startswith("P")),
    )
    returns = [sk for n, _s, sk in port_defs if n.startswith("N")]
    assert power == [0, 1]
    assert returns and returns[0] >= RETURN_PORT_SORT_BASE
    assert returns[0] >= RETURN_PORT_GND_SORT_BASE

    n_rows, row_map = port_layout_rows(port_defs)
    assert n_rows == 3
    assert row_map[returns[0]] == 2
    assert max(power) < row_map[returns[0]]


def test_source_ideal_return_leaves_dense_power_rows():
    """Missing/ideal N must not leave blank mid-rows between power channels."""
    from fypa.topology.layout.vertical_align import port_layout_rows

    directives = [
        {
            "role": "SOURCE",
            "designator": "J1",
            "channel_index": 1,
            "terminals": {
                "P": {"pins": [{"net": "VDD_A", "pad": "1"}]},
                "N": {"ideal_return": True},
            },
        },
        {
            "role": "SOURCE",
            "designator": "J1",
            "channel_index": 2,
            "terminals": {
                "P": {"pins": [{"net": "VDD_B", "pad": "2"}]},
                "N": {"ideal_return": True},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    port_defs = specs[0]["port_defs"]
    assert [p[0] for p in port_defs] == ["P1", "P2"]
    assert [sk for _n, _s, sk in port_defs] == [0, 1]
    n_rows, _ = port_layout_rows(port_defs)
    assert n_rows == 2


def test_regulator_dedupes_shared_in_n():
    directives = [
        {
            "role": "REGULATOR",
            "designator": "U1",
            "channel_index": 1,
            "terminals": {
                "IN_N": {"pins": [{"net": "GND", "pad": "2"}]},
            },
        },
        {
            "role": "REGULATOR",
            "designator": "U1",
            "channel_index": 2,
            "terminals": {
                "IN_N": {"pins": [{"net": "GND", "pad": "2"}]},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    in_n = [p for p in specs[0]["port_defs"] if p[0] == "IN_N"]
    assert len(in_n) == 1


def test_regulator_merges_shared_power_and_return_ports():
    """Multi-channel regulators show one port per shared pad set (e.g. VDD_5V0, GND)."""
    directives = [
        {
            "role": "REGULATOR",
            "designator": "U4",
            "channel_index": 1,
            "terminals": {
                "IN_P": {"pins": [{"net": "VDD_5V0", "pad": "8"}, {"net": "VDD_5V0", "pad": "3"}]},
                "OUT_P": {"pins": [{"net": "V+", "pad": "11"}]},
                "IN_N": {"pins": [{"net": "GND", "pad": "4"}]},
                "OUT_N": {"pins": [{"net": "GND", "pad": "4"}]},
            },
        },
        {
            "role": "REGULATOR",
            "designator": "U4",
            "channel_index": 2,
            "terminals": {
                "IN_P": {"pins": [{"net": "VDD_5V0", "pad": "8"}, {"net": "VDD_5V0", "pad": "3"}]},
                "OUT_P": {"pins": [{"net": "GND", "pad": "4"}]},
                "IN_N": {"pins": [{"net": "GND", "pad": "4"}]},
                "OUT_N": {"pins": [{"net": "V-", "pad": "6"}]},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    pnames = [p[0] for p in specs[0]["port_defs"]]
    assert pnames.count("IN_P") == 1
    assert "IN_P1" not in pnames and "IN_P2" not in pnames
    assert pnames.count("IN_N") == 1
    assert "OUT_P1" in pnames or "OUT_P" in pnames
    assert sum(1 for p in pnames if p.startswith("OUT_P")) == 1
    assert pnames.count("OUT_N") == 1


def test_passive_merge_p_when_shared_pad():
    directives = [
        {
            "role": "RESISTOR",
            "designator": "R1",
            "channel_index": 1,
            "terminals": {
                "P": {"pins": [{"net": "A", "pad": "1"}]},
                "N": {"pins": [{"net": "B", "pad": "2"}]},
            },
        },
        {
            "role": "RESISTOR",
            "designator": "R1",
            "channel_index": 2,
            "terminals": {
                "P": {"pins": [{"net": "A", "pad": "1"}]},
                "N": {"pins": [{"net": "C", "pad": "3"}]},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    pnames = [p[0] for p in specs[0]["port_defs"] if p[0].startswith("P")]
    assert pnames == ["P"]
    n_names = [p[0] for p in specs[0]["port_defs"] if p[0].startswith("N")]
    assert len(n_names) == 2


def test_multi_role_same_designator_merges_into_one_spec():
    """SERIES + SINK on one part → one stacked symbol, not two overlapping nodes."""
    directives = [
        {
            "role": "SERIES",
            "designator": "U2",
            "channel_index": 1,
            "terminals": {
                "P": {"pins": [{"net": "VIN", "pad": "1"}]},
                "N": {"pins": [{"net": "VOUT", "pad": "2"}]},
            },
        },
        {
            "role": "SINK",
            "designator": "U2",
            "channel_index": 2,
            "terminals": {
                "P": {"pins": [{"net": "VCC", "pad": "3"}]},
                "N": {"pins": [{"net": "GND", "pad": "4"}]},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    assert len(specs) == 1
    spec = specs[0]
    assert spec["node_id"] == "U2"
    sections = spec.get("sections")
    assert sections is not None
    assert len(sections) == 2
    assert sections[0]["role"] in ("SERIES", "RESISTOR")
    assert sections[1]["role"] == "SINK"
    assert spec["port_roles"]["P1"] in ("SERIES", "RESISTOR")
    assert spec["port_roles"]["P2"] == "SINK"


def test_multi_role_sections_sorted_by_channel_index():
    directives = [
        {
            "role": "SINK",
            "designator": "U2",
            "channel_index": 2,
            "terminals": {
                "P": {"pins": [{"net": "VCC", "pad": "3"}]},
                "N": {"pins": [{"net": "GND", "pad": "4"}]},
            },
        },
        {
            "role": "SERIES",
            "designator": "U2",
            "channel_index": 1,
            "terminals": {
                "P": {"pins": [{"net": "VIN", "pad": "1"}]},
                "N": {"pins": [{"net": "VOUT", "pad": "2"}]},
            },
        },
    ]
    specs = directives_to_component_specs(directives, [], {})
    sections = specs[0]["sections"]
    assert sections[0]["role"] in ("SERIES", "RESISTOR")
    assert sections[1]["role"] == "SINK"
