# Topology layout rules

Normative rules for the PDN topology diagram (not a SchDoc/PCB mirror).
Validate codes that enforce each rule are listed in parentheses.

Generic designators (`J1`, `U1`, `R1`) and nets (`VIN`, `VOUT`, `GND`, `OUTA`, `RETA`) only.

## Symbols

1. **Grid** — Components sit on a **column / row** grid.
2. **SOURCE left** — After column compaction, SOURCE occupies the **leftmost
   occupied** column (`source_not_leftmost` when a non-SOURCE shares column 0
   while a SOURCE is further right). Empty indices are removed after placement;
   SERIES/RESISTOR nodes may first take maximal columns subject to L→R (ALAP),
   then trailing singletons may merge left when L→R allows.
3. **SINK right** — Pure SINK symbols occupy the **rightmost** column alone
   (with multi-role parts that include SINK also allowed there). Non-SINK
   symbols must not share that column (`sink_not_rightmost`,
   `non_sink_in_rightmost`).
3a. **Adjacent singleton pack** — After compacting empty indices, a non-SOURCE
   singleton (not a connector-family member) may merge into the immediate right
   neighbour when loads stay strictly right, or into the left neighbour when
   L→R allows. Far ALAP jumps across mid columns are avoided (they inflate
   gutters and can fail-close pair nets).
3b. **Compact columns** — Within a column, unused vertical bands ≫ `ROW_GAP`
   with no routing need are closed. SERIES/RESISTOR peers that share nets pack
   contiguously (no orphan at `MARGIN` while siblings sit at the bottom).
3c. **Port-align vs density** — Straight shared-net ports win for small ΔY;
   nearest free slot is preferred on collision; peer re-pack may override a
   far align partner so local stacks stay tight.

## Current flow

4. **Left → right** — Power propagates left to right along  
   `SOURCE → ([SERIES →]* [REGULATOR →]*)* SINK`.

## Wires

5. **Directed, never RTL** — Each power wire follows the chain above and must
   **never** be drawn right-to-left (`right_to_left_wire`). Loop-pair return
   nets (rule 20) are exempt, like GND.
6. **No overlap** — Distinct nets must not share H/V corridors closer than
   `MIN_PARALLEL_GAP`, including hub↔hub (`duplicate_vertical_x`,
   `duplicate_horizontal_y`, `parallel_vertical_gap`, `foreign_wire_crossing`).
7. **No wire through / over symbols** — Segments must not run through or under
   foreign symbol bodies (`segment_through_foreign_node`, `vertical_under_node`
   as **error**). Clearance to symbols ≥ obstacle/gutter pad.
8. **Vertical only between columns** — V segments sit in **column gutters**,
   not inside symbol x-ranges (`wire_outside_channel`,
   `vertical_bus_outside_column_gap`).
9. **Horizontal only between rows** — H segments sit in **row gutters**
   (between symbol y-bands), not through symbol y-ranges and not merely
   `OBSTACLE_CLEAR` past a body edge (`wire_outside_channel` with
   `orient="H"`).
10. **Min clearance** — Wires keep a minimum distance from symbols
    (`OBSTACLE_CLEAR` / gutter pad; covered with through/under checks).
11. **No dead stubs** — A port stub tip must join continuing routing, **unless**
    the PDN definition that would continue the net is missing
    (`open_signal_stub` / `open_gnd_stub`; dashed external / missing-net
    stubs are the allowed exception). Short channel entry from pin into the
    adjacent gutter is not a dead stub.

## Ports

12. **Output = right** — Power outputs on the **right** face
    (`port_on_wrong_side`). Exception: loop-pair return outputs (rule 20).
13. **Input = left** — Power inputs on the **left** face
    (`port_on_wrong_side`). Exception: loop-pair return inputs (rule 20).
14. **SOURCE co-face** — SOURCE `P*` and `N*` share the **right** face
    (power above return; `port_on_wrong_side` when either is elsewhere).
15. **No stacked ports** — Ports on the same face must not share the same Y
    (`ports_overlapping`).

## Edge clauses

16. **GND / ideal return** — Return is not L→R power flow. Return ports stay
    left (or bottom toward the GND rail), except SOURCE `N*` on the right
    (rule 14). Same no-overlap / no-through-symbol rules; GND uses its own
    trunk/bus.
17. **Cycles** — Mutual REGULATOR feeds / loop SERIES: break with a
    SOURCE-anchored back-edge discard; remaining forward edges stay L→R.
    Loop SERIES pairs use rule 20 for the return path (not open stubs).
18. **Multi-driver nets** — Parallel drivers of one net may share a column;
    loads stay strictly right of every driver.
19. **Fail-closed** — If no legal channel geometry exists, validation reports
    errors; routing must not last-resort-draw through conflicts. Hub nets that
    cannot form one connected component emit no wires (`hub_net_unrouted`)
    instead of a disconnected drawing.
20. **Loop-pair / return face** — When two SERIES/RESISTOR symbols form a loop
    pair (parent drives child on forward nets, child drives parent on return
    nets), return nets are **return-class** like GND for flow rules:
    - Parent return **inputs** face **right** (toward the child).
    - Child return **outputs** face **left** (toward the parent).
    - All pair nets (forward and return) route in the shared column gutter
      between the two symbols (`loop_return_outside_pair_gutter` when a return
      leaves that gutter).
    - Exempt from rule 5 / `driver_not_left_of_load`; still subject to rules 6–7.
21. **Short and few bends** — Prefer the lowest-cost legal corridor: drawn
    length + bend count (clearance violation = infinite; grazing a body within
    `WIRE_GUTTER_PAD + MIN_PARALLEL_GAP` is expensive). Hard fail when drawn
    length exceeds Manhattan end-to-end distance by more than `MAX_DETOUR_RATIO`
    (`wire_detour_excessive`), or when bend count exceeds the Manhattan minimum
    (0 if endpoints share an axis, else 1) by more than `MAX_EXTRA_BENDS`
    (`wire_bends_excessive`).
22. **No redundant parallel runs** — Two H (or V) segments of the **same** net
    with overlapping span and axis gap in `(0, MIN_PARALLEL_GAP)` are illegal
    unless collinear (`redundant_parallel_run`). Foreign near-parallels stay
    under rule 6 (`duplicate_horizontal_y` / `duplicate_vertical_x`).
23. **Few foreign crossings** — Prefer corridors that avoid extra H∩V crossings
    with other nets (soft cost). Hard fail for illegal crossings remains rule 6
    (`foreign_wire_crossing`).

## Role → face (power)

| Role | Input (left) | Output (right) |
|------|--------------|----------------|
| SOURCE | — | `P*` and `N*` (co-face) |
| SINK | `P*` (and `N*` return) | — |
| REGULATOR | `IN_*` | `OUT_*` |
| SERIES / RESISTOR | `P*` | `N*` |

Loop-pair return exception (rule 20): parent return `P*` → right; child return `N*` → left.

## Counterexamples (illegal)

```
# RTL horizontal on VIN
M──H──→  then  ←──H──  on the same power path  → right_to_left_wire

# Output on left
U1 OUT_P on left face  → port_on_wrong_side

# SOURCE return on left
J1 N on left face  → port_on_wrong_side

# Vertical through body
V segment under U1 body  → vertical_under_node / segment_through_foreign_node

# Dead stub with complete PDN
Port stub tip with no junction / continuation, net fully defined
  → open_signal_stub

# Loop return leaving the pair gutter
RETA vertical west of parent U1 while child J1 is east of U1
  → loop_return_outside_pair_gutter

# Excessive detour
Path length > MAX_DETOUR_RATIO × Manhattan(ends)  → wire_detour_excessive

# Too many bends
Wire with bends > Manhattan-min + MAX_EXTRA_BENDS  → wire_bends_excessive

# Same-net parallel H runs
VIN H at y=100 and y=105 overlapping in x  → redundant_parallel_run
```

## Gap vs prior implementation

| Rule area | Prior behaviour | Target |
|-----------|-----------------|--------|
| Port faces | SERIES flip + loop “all ports face parent” | Fixed In=L / Out=R; loop returns peer-facing (rule 20) |
| RTL | Partial driver≺load; detours could wrap | No RTL H on power (loop returns / GND exempt) |
| Channels | Stack lanes, face climbs, escape columns | V in col gaps, H in row gaps |
| Hub↔hub | Exempt from `foreign_wire_crossing` | Included |
| `vertical_under_node` | Warning | Error |
| Bus full | Clamp onto occupied x | Fail / no silent overlap |
| Escape / last-resort | Draw despite conflict | Fail-closed |
| Hub partial | Row/tap drawn without trunk feed | No wires + `hub_net_unrouted` |
| Corridor pick | First-fit / nearest Δy | Cost (length + bends + graze + crossings) |
| Detour length | Unbounded legal detours | Cap via `wire_detour_excessive` |
| Bend count | Soft only in corridor cost | Cap via `wire_bends_excessive` |
| Same-net parallels | Allowed near-twins | `redundant_parallel_run` |
| Foreign crossings | Hard only when illegal | Soft prefer fewer (rule 23) |
| Rightmost col | Loop child could share SINK column | Pure SINKs alone rightmost (`non_sink_in_rightmost`) |
| Row→bus skip | Any same-net V in row span skipped feed | Only skip when trunk/bus already met |
