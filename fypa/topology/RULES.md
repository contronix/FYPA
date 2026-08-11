# Topology layout rules

Normative rules for the PDN topology diagram (not a SchDoc/PCB mirror).
Validate codes that enforce each rule are listed in parentheses.

Generic designators (`J1`, `U1`, `R1`) and nets (`VIN`, `VOUT`, `GND`) only.

## Symbols

1. **Grid** — Components sit on a **column / row** grid.
2. **SOURCE left** — SOURCE symbols as far **left** as possible (`source_not_leftmost` when a non-SOURCE shares column 0 while a SOURCE is further right — layout invariant).
3. **SINK right** — SINK symbols as far **right** as possible (`sink_not_rightmost` for pure SINKs left of the rightmost column).

## Current flow

4. **Left → right** — Power propagates left to right along  
   `SOURCE → ([SERIES →]* [REGULATOR →]*)* SINK`.

## Wires

5. **Directed, never RTL** — Each power wire follows the chain above and must
   **never** be drawn right-to-left (`right_to_left_wire`).
6. **No overlap** — Distinct nets must not share H/V corridors closer than
   `MIN_PARALLEL_GAP`, including hub↔hub (`duplicate_vertical_x`,
   `duplicate_horizontal_y`, `parallel_vertical_gap`, `foreign_wire_crossing`).
7. **No wire through / over symbols** — Segments must not run through or under
   foreign symbol bodies (`segment_through_foreign_node`, `vertical_under_node`
   as **error**). Clearance to symbols ≥ obstacle/gutter pad.
8. **Vertical only between columns** — V segments sit in **column gutters**,
   not inside symbol x-ranges (`wire_outside_channel`,
   `vertical_bus_outside_column_gap`).
9. **Horizontal only between rows** — H segments sit in **row gutters**,
   not through symbol y-ranges (`wire_outside_channel`).
10. **Min clearance** — Wires keep a minimum distance from symbols
    (`OBSTACLE_CLEAR` / gutter pad; covered with through/under checks).
11. **No dead stubs** — A port stub tip must join continuing routing, **unless**
    the PDN definition that would continue the net is missing
    (`open_signal_stub` / `open_gnd_stub`; dashed external / missing-net
    stubs are the allowed exception). Short channel entry from pin into the
    adjacent gutter is not a dead stub.

## Ports

12. **Output = right** — Power outputs on the **right** face
    (`port_on_wrong_side`).
13. **Input = left** — Power inputs on the **left** face
    (`port_on_wrong_side`).
14. **No stacked ports** — Ports on the same face must not share the same Y
    (`ports_overlapping`).

## Edge clauses

15. **GND / ideal return** — Return is not L→R power flow. Return ports stay
    left (or bottom toward the GND rail). Same no-overlap / no-through-symbol
    rules; GND uses its own trunk/bus.
16. **Cycles** — Mutual REGULATOR feeds / loop SERIES: break with a
    SOURCE-anchored back-edge discard; remaining edges stay L→R.
    Discarded back-edge ports may show `open_signal_stub` until a dedicated
    return path exists — that is fail-closed, not peer-facing.
17. **Multi-driver nets** — Parallel drivers of one net may share a column;
    loads stay strictly right of every driver.
18. **Fail-closed** — If no legal channel geometry exists, validation reports
    errors; routing must not last-resort-draw through conflicts.

## Role → face (power)

| Role | Input (left) | Output (right) |
|------|--------------|----------------|
| SOURCE | return `N*` | `P*` |
| SINK | `P*` (and `N*` return) | — |
| REGULATOR | `IN_*` | `OUT_*` |
| SERIES / RESISTOR | `P*` | `N*` |

## Counterexamples (illegal)

```
# RTL horizontal on VIN
M──H──→  then  ←──H──  on the same power path  → right_to_left_wire

# Output on left
U1 OUT_P on left face  → port_on_wrong_side

# Vertical through body
V segment under U1 body  → vertical_under_node / segment_through_foreign_node

# Dead stub with complete PDN
Port stub tip with no junction / continuation, net fully defined
  → open_signal_stub
```

## Gap vs prior implementation

| Rule area | Prior behaviour | Target |
|-----------|-----------------|--------|
| Port faces | SERIES flip + loop “all ports face parent” | Fixed In=L / Out=R |
| RTL | Partial driver≺load; detours could wrap | No RTL H on power |
| Channels | Stack lanes, face climbs, escape columns | V in col gaps, H in row gaps |
| Hub↔hub | Exempt from `foreign_wire_crossing` | Included |
| `vertical_under_node` | Warning | Error |
| Bus full | Clamp onto occupied x | Fail / no silent overlap |
| Escape / last-resort | Draw despite conflict | Fail-closed |
