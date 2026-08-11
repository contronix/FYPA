"""Terminal role classification for topology layout and metadata."""

from __future__ import annotations


def _terminal_matches(base: str, terminal: str) -> bool:
    """True for ``base`` or ``base`` + digits (e.g. P, P1, IN_P2)."""
    if terminal == base:
        return True
    suffix = terminal[len(base) :]
    return terminal.startswith(base) and suffix.isdigit()


def is_power_input_port(role: str, terminal: str) -> bool:
    """Load-side inputs that need an upstream PDN driver on their net."""
    if role == "SINK":
        return _terminal_matches("P", terminal)
    if role == "REGULATOR":
        return _terminal_matches("IN_P", terminal)
    return False


def is_single_net_node(role: str, port_defs) -> bool:
    """A SOURCE/SINK whose return is an ideal return (no visible N port).

    Ideal-return terminals are dropped before ``port_defs`` is built, so a
    single-net source/sink has no ``N``-family port to show.
    """
    if role not in ("SOURCE", "SINK"):
        return False
    return not any(_terminal_matches("N", pname) for pname, _side, _key in port_defs)


def is_output_port(role: str, terminal: str, side: str) -> bool:
    """Ports that drive left-to-right column placement (power flow, not returns)."""
    del side  # reserved for future side-aware rules
    if role == "SOURCE":
        return _terminal_matches("P", terminal)
    if role == "REGULATOR":
        return _terminal_matches("OUT_P", terminal)
    if role in ("RESISTOR", "SERIES"):
        return _terminal_matches("N", terminal)
    return False


def expected_port_side(role: str, terminal: str) -> str | None:
    """Canonical face for a power/return port: input=left, output=right.

    Returns ``None`` when the terminal name is not part of the role's port
    vocabulary (unknown / channel extras stay unconstrained here).
    """
    if role == "SOURCE":
        if _terminal_matches("P", terminal):
            return "right"
        if _terminal_matches("N", terminal):
            return "left"
        return None
    if role == "SINK":
        if _terminal_matches("P", terminal) or _terminal_matches("N", terminal):
            return "left"
        return None
    if role == "REGULATOR":
        if terminal.startswith("IN_"):
            return "left"
        if terminal.startswith("OUT_"):
            return "right"
        return None
    if role in ("RESISTOR", "SERIES"):
        if _terminal_matches("P", terminal):
            return "left"
        if _terminal_matches("N", terminal):
            return "right"
        return None
    return None
