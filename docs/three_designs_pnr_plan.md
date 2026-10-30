# Plan: N=15, T=2 encoder in all three architectures, compared after place and route

Status: plan only, nothing implemented yet.

## Scope
| # | Design | Top | Architecture source | New work |
|---|---|---|---|---|
| 1 | Baseline, no sharing | `bch3d_enc_baseline` | Mondal & Garani, IEEE T-MAG 2021 (2-D paper), extended to 3-D: one IDFFFT unit per output point, one conjugate-class unit per class (hard-coded twiddle, MSB-only multiplier), XOR array | Full place and route on **Virtex-7 xc7v2000tflg1925-1** |
| 2 | Multiplier optimisation | `bch3d_enc_opt` | `encoder_optimisation.pdf` (compute-once, route-many): computation-class multiplier bank + 3,375 XOR trees | Full place and route on **Virtex-7 xc7v2000tflg1925-1** |
| 3 | Time-multiplexed | `bch3d_enc_tm_asm` | `docs/time_multiplexed_plan.md` + assembly wrapper | Place and route on **Artix-7 xc7a200tfbg676-1** (no re-verification). The existing Virtex-7 6 ns results stay as the same-device reference. |

No RTL simulation is repeated for any design: 1 and 2 already passed xsim at N=15 (3,215 vectors), 3 passed RTL and routed-netlist simulation.

The comparison is made on Virtex-7 for all three (design 3 already has Virtex-7 results); the Artix-7 run of design 3 is an extra data point on a low-cost device.

## Step 0: 225-point slice of design 1 (estimate before the full run)
Run this first, so the full design-1 run is attempted with a known per-cone timing, power and memory profile.
- Generate `bch3d_enc_baseline_slice225_reg`: the baseline restricted to the 225 output points of one `i''` plane, with the same input/output registers as the full wrapper (3,163 + 225 FFs). Expected ~77 k LUTs.
- Synthesise and route it on xc7v2000tflg1925-1 with the same flow as Step 3, at 10 ns then tightened.
- Record LUT/FF, WNS/Fmax, power, run time and peak memory.
- Extrapolate for the full design: LUTs and dynamic power x15. Timing is a best case, because in the full design every input fans out 15x more. Peak memory x15 gives a rough check on whether the full run can fit in 15.3 GB.
- Decide from this whether the full design-1 run (Step 3) is worth attempting. If the extrapolated memory is far above 15.3 GB, report the slice and the extrapolation instead.

### Step 0 result (2026-10-30, reports in `reports/baseline_slice/`)
`bch3d_enc_baseline_slice225_reg_n15_t2`, xc7v2000tflg1925-1, `rebuilt`, 10 ns. RTL xsim: 3,170 vectors, 0 errors.

| | Slice (measured) | Full design 1 (x15 estimate) |
|---|---:|---:|
| LUTs | 37,882 (3.1%) | ~568 k (46%) |
| FFs | 3,354 | ~6.5 k (msg + codeword regs, not x15) |
| WNS at 10 ns | +1.618 ns, fully routed, 0 errors | worse (inputs fan out 15x more) |
| Critical path | 8.348 ns, 5 LUT levels, 94% routing | - |
| Fmax | ~119 MHz | below 119 MHz |
| Power (vectorless) | 3.03 W (2.36 dynamic, 0.67 static) | ~36 W dynamic + static |
| Vivado peak memory | 5.3 GB (route gain 1.55 GB) | roughly 35-40 GB |
| Run time | 11.7 min | many hours |

- `rebuilt` shares logic inside the slice, so the slice is half the ~77 k expected from `flatten none`; the x15 estimate is close to design 2's synthesis size (544,597).
- Memory: synthesis + place + route added ~4.4 GB over a ~0.6 GB base for 38 k LUTs. Scaling that by 15 is far above the 15.3 GB on this machine, so a full design-1 run is very likely to run out of memory here.

## Step 0b: design 3 on Artix-7
- Same `scripts/vivado_pnr.tcl` flow, part `xc7a200tfbg676-1`, `rebuilt`, clock sweep 6 / 7 / 8 / 10 ns (Artix-7 is slower than Virtex-7, so 6 ns may fail).
- Same reports as the Virtex-7 run, routed checkpoint and flattened netlist. No simulation.
- Small (17 k LUTs), so it can run while the slice is being generated.

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
- **Fallback if a run fails:** report synthesis numbers plus the failure point (placement, routing, or memory). For design 1 the Step 0 slice numbers are the fallback; for design 2 a 225-point slice is run the same way only if you approve.

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
| 0 Design-1 slice: generate, synth, P&R | ~3-6 h |
| 0b Design 3 on Artix-7 (4 periods) | ~1-2 h |
| 1 Wrappers + wrapper check | ~1-2 h |
| 2 Synthesis (2 designs × 2 modes) | several hours (design 1 synthesis was already long) |
| 3 P&R design 2, then design 1 | up to 12 h each, may fail on memory |
| 4 Netlist simulation | 1-3 h if it fits |
| 5-6 Report + artifact | ~2-3 h |
