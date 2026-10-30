# Plan: N=15, T=2 encoder in all three architectures, compared after place and route

Status: plan only, nothing implemented yet.

## Scope
| # | Design | Top | Architecture source | New work |
|---|---|---|---|---|
| 1 | Baseline, no sharing | `bch3d_enc_baseline` | Mondal & Garani, IEEE T-MAG 2021 (2-D paper), extended to 3-D: one IDFFFT unit per output point, one conjugate-class unit per class (hard-coded twiddle, MSB-only multiplier), XOR array | Full place and route on **Virtex-7 xc7v2000tflg1925-1** |
| 2 | Multiplier optimisation | `bch3d_enc_opt` | `encoder_optimisation.pdf` (compute-once, route-many): computation-class multiplier bank + 3,375 XOR trees | Full place and route on **Virtex-7 xc7v2000tflg1925-1** |
| 3 | Time-multiplexed | `bch3d_enc_tm_asm` | `docs/time_multiplexed_plan.md` + assembly wrapper | **None.** Reuse the existing routed results in `reports/tm/` (same part, 6 ns). Explanation only. |

No RTL simulation is repeated for any design: 1 and 2 already passed xsim at N=15 (3,215 vectors), 3 passed RTL and routed-netlist simulation.

Using the same part for all three keeps the comparison on one device and speed grade.

## Known numbers going in
| Design | Synth LUTs (`flatten none`) | % of xc7v2000t (1,221,600) | FFs |
|---|---:|---:|---:|
| 1 | 1,156,681 | 94.7% | 0 |
| 2 | 544,597 | 44.6% | 0 |
| 3 (routed, `rebuilt`) | 17,196 | 1.4% | 10,200 |

## Step 1: make designs 1 and 2 timeable
Both are purely combinational, so a routed run has no reg-to-reg paths. Generate one wrapper per design in `model/gen_rtl.py`:
- `bch3d_enc_baseline_reg`, `bch3d_enc_opt_reg`: `clk`, input register on `msg[3162:0]`, output register on `codeword[3374:0]`, core unchanged in between.
- Adds 3,163 + 3,375 = 6,538 FFs and no LUTs. The critical path is then register → full encoder → register, which is how the 2-D paper reports its one-cycle encoder.
- Check the wrappers with the existing N=15 testbench vectors, offset by the 2-cycle latency (wrapper check only, the core is not re-verified). A short xsim run on a subset of vectors is enough.

## Step 2: synthesis
- Run `synth_design -flatten_hierarchy rebuilt` and `full` for both wrappers (the flattened design). Keep the earlier `none` numbers for reference.
- Pick the smaller result to carry into place and route. Design 3 shrank 2.5x with `rebuilt`; the N=7 baseline shrank 64%. If design 1 stays near 95% of the device, it is unlikely to route, and that is reported as a result.
- Record LUT/FF, LUT-type breakdown, run time and peak memory for each.

## Step 3: place and route (Virtex-7 xc7v2000tflg1925-1)
- Same flow as design 3 (`scripts/vivado_pnr.tcl`): OOC, `create_clock`, 20% I/O delays, `opt_design`, `place_design`, `route_design`. No `phys_opt_design` (it crashed Vivado on the 4 ns TM run).
- Start at a relaxed clock (10 ns), then tighten from the result (WNS tells the achievable period, so one or two more runs at most).
- Reports: timing summary, critical paths, utilisation (flat + hierarchical), high-fanout nets, congestion / design analysis, route status, power (vectorless), clock utilisation, routed checkpoint, flattened `funcsim` netlist.
- Order: design 2 first (smaller, tells us run time and memory), then design 1.

### Risks
- **Memory:** this machine has 15.3 GB RAM. Placing and routing 0.5-1.2 M LUTs on the 2000T can need much more. Each run is watched for peak memory; if Vivado runs out or swaps heavily, the run is stopped and that is recorded.
- **Routability:** the 2000T is four stacked dies (SLRs). Design 1 at ~95% LUT would need heavy SLR crossings; failure to route is a likely outcome.
- **Run time:** budget up to 12 h per P&R run, running one at a time in the background.
- **Fallback if a run fails:** report synthesis numbers plus the failure point (placement, routing, or memory). Then, only if you approve, route a 225-point slice of that design on the same Virtex-7 part to get per-cone timing and power, clearly labelled as a slice.

## Step 4: routed-netlist simulation
- Design 2: simulate the routed `funcsim` netlist with sampled vectors (every 10th unit vector + random), if xsim fits in memory.
- Design 1: same, attempted only if design 2's netlist simulation fits comfortably; otherwise skipped and stated.

## Step 5: comparison report
`reports/three_designs_comparison.tex` and a README table, all three on xc7v2000t-1:
- LUT, FF, slices, utilisation, LUT-type breakdown.
- WNS/WHS, Fmax, logic levels, routing share of the critical path.
- Power (static/dynamic), energy per codeword.
- Throughput: designs 1-2 give 1 codeword/cycle, design 3 gives 1 per 15 cycles; codewords/s and Gbit/s at each design's Fmax.
- Throughput per LUT and per watt, so design 3's 15x throughput cost is shown next to its area saving.
- Notes on what was measured and what failed, if anything.

## Step 6: artifact
One page, `docs/three_designs.html`, published as an Artifact:
- Each design: what it computes, block diagram as designed, structure as routed (register groups, LUT cloud, logic depth, one critical path on the placement grid, read from the checkpoint with Tcl). Design 3 reuses the existing explanation and data.
- One diagram showing what changes from 1 → 2 → 3: per-point multipliers → shared bank → shared bank + 225 trees reused over 15 cycles.
- The comparison table and charts.

## Rough run time
| Step | Time |
|---|---|
| 1 Wrappers + wrapper check | ~1-2 h |
| 2 Synthesis (2 designs × 2 modes) | several hours (design 1 synthesis was already long) |
| 3 P&R design 2, then design 1 | up to 12 h each, may fail on memory |
| 4 Netlist simulation | 1-3 h if it fits |
| 5-6 Report + artifact | ~2-3 h |
