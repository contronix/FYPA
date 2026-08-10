"""Port placement checks for ``validate_topology``."""

from __future__ import annotations

from collections import defaultdict

from fypa.topology.constants import PORT_R, WIRE_EPS
from fypa.topology.issues import make_issue
from fypa.topology.types import TopologyModel


def check_overlapping_ports(model: TopologyModel) -> list[dict]:
    """Flag ports on the same symbol edge whose centres are too close.

    Two ports must never share a centre (or sit closer than a port diameter);
    peer-facing can put several nets on one side, but each needs its own row.
    """
    issues: list[dict] = []
    min_gap = 2 * PORT_R
    for node in model.nodes:
        if node.role == "GND":
            continue
        by_side: dict[str, list] = defaultdict(list)
        for port in node.ports:
            by_side[port.side].append(port)
        for side, group in by_side.items():
            ordered = sorted(group, key=lambda p: (p.y, p.terminal))
            for i in range(1, len(ordered)):
                a, b = ordered[i - 1], ordered[i]
                gap = abs(b.y - a.y)
                if gap + WIRE_EPS >= min_gap:
                    continue
                issues.append(
                    make_issue(
                        "overlapping_ports",
                        (
                            f"Ports {node.designator}.{a.terminal} and "
                            f"{node.designator}.{b.terminal} on {side} "
                            f"are only {gap:.1f}px apart (min {min_gap:.1f}px)"
                        ),
                        node_id=node.node_id,
                        designator=node.designator,
                        side=side,
                        gap=round(gap, 1),
                        terminals=[a.terminal, b.terminal],
                    )
                )
    return issues
