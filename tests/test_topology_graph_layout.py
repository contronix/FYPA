"""Graph column packing and peer-facing port orientation."""

from __future__ import annotations

from fypa.topology.metadata.layout_bridge import (
    GRAPH_LAYOUT_MAX_COLUMNS,
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
    """A long SERIES hop chain must not open one column per part."""
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
    # Enrich resolved_ports like parse_topology_directives would.
    from fypa.topology.metadata.layout_bridge import _enrich_resolved_ports

    for s in specs:
        _enrich_resolved_ports(s)
    col = assign_columns(specs, {})
    assert max(col.values()) < 10
    assert max(col.values()) <= GRAPH_LAYOUT_MAX_COLUMNS - 1
    assert col["SRC"] == 0
    assert col["SNK"] == max(col.values())


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
