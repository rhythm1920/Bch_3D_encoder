# 3-D BCH Spectral-Domain Encoder (`N=15, T=2` and `N=7, T=2`)

Hardware implementation, mathematical golden model, formal linearity verification, and **Vivado 2026.1** synthesis LUT evaluation of the fully parallel **3-D BCH Spectral-Domain (IDFFFT) Encoder** over $\text{GF}(2^m)$ in both the **Baseline** and **Computation-Class Optimised** ("Compute-Once, Route-Many") architectures.

---

## 1. Mathematical Parameters ($N=15, T=2, p(x) = x^4 + x + 1$)

| Parameter | Symbol | Value (`N=15, T=2`) | Value (`N=7, T=2`) |
|---|---|---:|---:|
| Extension degree | $m$ | `4` ($\text{GF}(16)$) | `3` ($\text{GF}(8)$) |
| Primitive polynomial | $p(x)$ | $x^4 + x + 1$ (`5'b10011`) | $x^3 + x + 1$ (`4'b1011`) |
| 3-D Codeword dimensions | $N \times N \times N$ | $15 \times 15 \times 15 = \mathbf{3,375}$ bits | $7 \times 7 \times 7 = \mathbf{343}$ bits |
| Spectral-null cube | $(2T)^3$ | $4 \times 4 \times 4 = 64$ freq points (`53` classes) | $4 \times 4 \times 4 = 64$ freq points (`24` classes) |
| Total 3-D conjugate classes | $N_{\text{total}}$ | `851` | `93` |
| Message-carrying classes | $N_{\text{CLASS}}$ | **`798`** (`1` of size 1, `13` of size 2, `784` of size 4) | **`69`** (`1` of size 1, `68` of size 3) |
| Message length | $K$ | **`3,163` bits** | **`205` bits** |
| Code rate | $R = K / N^3$ | **`0.9372`** | **`0.5977`** |

### 3-D Computation-Class Breakdown (`N=15, T=2`)
By exploiting $\gcd(j_1, j_2, j_3, 15)$ across each 3-D cyclotomic coset $C_k = [(j_1, j_2, j_3)]$, each class $k$ only requires $N / \gcd(j_1, j_2, j_3, N) \in \{15, 5, 3, 1\}$ distinct trace/multiplier evaluations across the entire $15 \times 15 \times 15$ cube instead of $3,375$:

| Computation Class | Condition on $(j_1, j_2, j_3)$ | Orbit Size $d_k$ | Number of Classes | Distinct Multipliers / Class | Total Shared Outputs | Slice LUTs (`xc7v2000t`) |
|---|---|---:|---:|---:|---:|---:|
| **15-Computation** | $\gcd(j_1, j_2, j_3, 15) = 1$ | 4 | `754` | `15` (`4` wires + `11` XORs) | `11,310` | `4,524` (`754 × 6`) |
| **5-Computation** | $\gcd(j_1, j_2, j_3, 15) = 3$ | 4 | `30` | `5` (`1` wire + `4` XORs) | `150` | `60` (`30 × 2`) |
| **3-Computation** | $\gcd(j_1, j_2, j_3, 15) = 5$ | 2 | `13` | `3` (`1` wire + `2` XORs) | `39` | `13` (`13 × 1`) |
| **1-Computation** | $(j_1, j_2, j_3) = (0,0,0)$ | 1 | `1` | `1` (`1` wire, `0` XORs) | `1` | `0` |
| **Total (`bch3d_mul_bank_n15_t2`)** | — | — | **`798`** | — | **`11,500` wires** | **`4,597` Slice LUTs** (`8,440` LUT2/3/4 primitives) |

---

## 2. Repository Structure

- [`rtl/bch3d_msg_to_submsg.v`](rtl/bch3d_msg_to_submsg.v): Parameterised message-to-sub-message converter (`N`, `T`, `PRIM_POLY`) mapping $K$ message bits into $N_{\text{CLASS}}$ $m$-bit $\text{GF}(2^m)$ subfield elements (`0` LUTs for $N=15, T=2$).
- [`model/bch3d.py`](model/bch3d.py): Independent Python golden model implementing:
  - Separable 3-D IDFFFT over $\text{GF}(2^m)$ (`encode_idffft`)
  - Closed-form per-class trace evaluation (`encode_trace`)
  - Forward 3-D DFFFT spectral-null cube checker (`decode_dffft_check_nulls`)
- [`model/gen_rtl.py`](model/gen_rtl.py): RTL and self-checking testbench generator for both:
  - **Baseline (`bch3d_enc_baseline`)**: Per-point independent IDFFFT evaluation (Report Fig. 2).
  - **Optimised (`bch3d_enc_opt`)**: Shared Computation-Class Multiplier Bank (`bch3d_mul_bank_*`) + routing network + per-point $N_{\text{CLASS}}$-input XOR adder trees (Report Fig. 3).
- [`model/bch3d_tm.py`](model/bch3d_tm.py): Reference model of the planned time-multiplexed encoder (225 trees, 15 cycles); see [`docs/time_multiplexed_plan.md`](docs/time_multiplexed_plan.md).
- [`scripts/gen_tb.py`](scripts/gen_tb.py) / [`tb/tb_bch3d_msg_to_submsg.v`](tb/tb_bch3d_msg_to_submsg.v): converter golden model and generated testbench.
- [`Makefile`](Makefile) + [`.github/workflows/sim.yml`](.github/workflows/sim.yml): `make test` runs the model self-tests, converter sim and N=7 encoder sims (Verilator); CI also generates and cross-checks N=15,T=2.
- [`scripts/sim.ps1`](scripts/sim.ps1): Vivado `xvlog` $\to$ `xelab` $\to$ `xsim` batch verification script.
- [`scripts/vivado_ooc.tcl`](scripts/vivado_ooc.tcl): Vivado Out-of-Context (`-mode out_of_context`) batch synthesis and utilization report script targeting Virtex-7 `xc7v2000tflg1925-1`.
- [`reports/`](reports/): Official Vivado 2026.1 synthesis utilization reports for `N=15, T=2` and `N=7, T=2`.

---

## 3. Verification Summary (Vivado 2026.1 `xsim`)

Because the 3-D BCH encoder over $\text{GF}(2)$ is purely linear (XOR-only), verifying the all-zero vector plus **all $K$ unit vectors** ($e_b = 1 \ll b$ for $b \in [0, K-1]$) against the independent 3-D IDFFFT golden model **mathematically proves correctness for all $2^K$ possible messages**:

| Design / Configuration | Test Vectors Verified in `xsim` | Result |
|---|---|---|
| `bch3d_msg_to_submsg` (`(15,2), (15,1), (7,1), (7,2)`) | Directed + random vectors across all 4 parameter sets | **PASS (`0` errors)** |
| `N=7, T=1` (`bch3d_enc_baseline_n7_t1` & `bch3d_enc_opt_n7_t1`) | `344` vectors (all-0, all-1, all `322` unit vectors, `20` random) | **PASS (`0` errors)** |
| `N=7, T=2` (`bch3d_enc_baseline_n7_t2` & `bch3d_enc_opt_n7_t2`, RTL + Post-Synth `funcsim`) | `227` vectors (all-0, all-1, all `205` unit vectors, `20` random) | **PASS (`0` errors)** |
| **`N=15, T=2` (`bch3d_enc_opt` & `bch3d_enc_baseline`)** | **`3,215` vectors** (all-0, all-1, **all `3,163` unit vectors**, `50` random) | **PASS (`0` errors)** |

---

## 4. Vivado 2026.1 Synthesis LUT Results (Target: Virtex-7 `xc7v2000tflg1925-1`, `1,221,600` Slice LUTs)

### A. Full-Chip `N=15, T=2` (`K=3,163` $\to$ `3,375` bits, Out-of-Context Synthesis)

| Architecture / Module | Flatten Mode | Slice LUTs | LUT Primitives Breakdown | `%` of `xc7v2000t` | Reduction vs. Baseline |
|---|---|---:|---|---:|---:|
| **Baseline (Unflattened Conjugate Blocks + XOR Tree, Analytical)** | — | `~1,616,344` (`2,516,400` prims) | `1,976,400` CB LUT2/3/4 + `540,000` XOR LUTs | `132.31%` *(exceeds chip)* | — |
| **Baseline (`bch3d_enc_baseline`, Per-Point LUT6-Merged Vivado OOC)** | `none` | **`1,156,681`** | `1,126,396` LUT6, `19,333` LUT5, `3,613` LUT4, `7,336` LUT3, `3` LUT2 | **`94.69%`** | Reference (`1.00×`) |
| **Optimised (`bch3d_enc_opt`, Computation-Class Bank + XOR Network)** | `none` | **`544,597`** | `533,250` LUT6, `3,375` LUT5, `4,129` LUT4, `3,106` LUT3, `4,567` LUT2 | **`44.58%`** | **`-612,084` Slice LUTs (`-52.92%`)** |
| ├─ Sub-message Converter (`bch3d_msg_to_submsg`) | `none` | **`0`** | `0` (pure wiring & constants) | `0.00%` | — |
| ├─ Computation-Class Multiplier Bank (`bch3d_mul_bank_n15_t2`) | `none` | **`4,597`** | `8,440` (`4,554` LUT2, `3,119` LUT3, `767` LUT4) | `0.38%` | **`-99.57%`** vs. `1,976,400` unshared CB LUTs |
| └─ 3,375-Point 798-Input XOR Network (`3,375 × bch3d_xor_tree_n15_t2`) | `none` | **`540,000`** | `3,375 × 160` Slice LUTs (`533,250` LUT6, `3,375` LUT5, `3,362` LUT4, `13` LUT2/3) | `44.20%` | — |

### B. `N=7, T=2` (`K=205` $\to$ `343` bits, Out-of-Context Synthesis)

| Architecture / Module | Flatten Mode | Slice LUTs | LUT Primitives Breakdown | Reduction vs. Baseline |
|---|---|---:|---|---:|
| `bch3d_enc_baseline_n7_t2` | `none` | **`8,135`** | `7,673` LUT6, `313` LUT5, `69` LUT4, `80` LUT3 | Reference |
| `bch3d_enc_opt_n7_t2` | `none` | **`4,938`** | `4,116` LUT6, `686` LUT5, `68` LUT3, `204` LUT2 (`5,074` prims) | **`-3,197` Slice LUTs (`-39.30%`)** |
| `bch3d_enc_baseline_n7_t2` | `rebuilt` | **`2,909`** | `2,146` LUT6, `428` LUT5, `310` LUT4, `344` LUT3, `223` LUT2 (`3,451` prims) | Reference (`rebuilt`) |
| `bch3d_enc_opt_n7_t2` | `rebuilt` | **`2,348`** | `1,714` LUT6, `49` LUT5, `256` LUT4, `210` LUT3, `643` LUT2 (`2,872` prims) | **`-561` Slice LUTs (`-19.28%`)** |

---

## 5. Reproducing the Flow

```powershell
# 1. Run Python golden model self-checks ((7,1), (7,2), (15,1), (15,2))
python model/bch3d.py

# 2. Generate RTL and self-checking unit-vector + random testbenches
python model/gen_rtl.py --n 15 --t 2 --rtl-dir build/rtl --tb-dir build/tb --vec-dir build/vec --num-random 50

# 3. Simulate in Vivado xsim
./scripts/sim.ps1 -WorkDir build/sim_n15_t2 -Top tb_bch3d_enc_n15_t2 -Defines @("ONLY_OPT") `
  -Files @("rtl/bch3d_msg_to_submsg.v", "build/rtl/bch3d_enc_opt.v", "build/tb/tb_bch3d_enc_n15_t2.v")

# 4. Run Out-of-Context synthesis in Vivado 2026.1
& "D:\Rhythm\2026.1\Vivado\bin\vivado.bat" -mode batch -nojournal -nolog `
  -source scripts/vivado_ooc.tcl -tclargs bch3d_enc_opt xc7v2000tflg1925-1 none reports `
  rtl/bch3d_msg_to_submsg.v build/rtl/bch3d_enc_opt.v
```

---

## 6. Time-Multiplexed Encoder (`bch3d_enc_tm`, branch `time-multiplexed`)

225 XOR trees reused over 15 cycles instead of 3,375 trees (plan: [docs/time_multiplexed_plan.md](docs/time_multiplexed_plan.md),
model: `model/bch3d_tm.py`). Cycle `c` emits the 15x15 slice `i''=c`; tree `(i,i')` always reads bank wire
`e0 = -(i*j1 + i'*j2) mod 15`, and each class register `S_k` is multiplied by `alpha^(-j3)` every cycle, so no routing mux is needed.

- Ports: `clk, rst, start, msg -> slice[224:0], slice_idx, slice_valid, done` (slices are registered, 1 cycle after the internal state).
- `msg` is latched on `start` (it does **not** need to be held). `start` is accepted when idle or in the last busy cycle, giving 1 codeword per 15 cycles back-to-back.
- Multiplier bank pruned to the `i''=0` exponents (10,608 wires for N=15).
- Verification: `make enc-sim N=7 T=2` also runs `tb_bch3d_enc_tm_*` (gapped, back-to-back and mid-run-reset passes; checked against golden and `bch3d_enc_opt`). Also simulated with xsim: N=7 T=1, N=7 T=2, and N=15 T=2 (all 3,163 unit vectors + 5 random), all PASS.
- Synthesis: `CLOCK_PERIOD_NS=10` makes `scripts/vivado_ooc.tcl` also write a post-synthesis timing report.

### Place & route results (Vivado 2026.1, `xc7v2000tflg1925-1`, OOC, `rebuilt`, `scripts/vivado_pnr.tcl`; reports in `reports/tm/`)

| Target clock | Slice LUTs | FFs | WNS | WHS | Status |
|---|---:|---:|---:|---:|---|
| 6.0 ns (167 MHz) | 17,197 | 3,416 | +0.127 ns | +0.125 ns | met, 0 routing errors, 3.28 W (medium confidence) |
| 5.0 ns (200 MHz) | 17,659 | 3,416 | -0.316 ns | +0.118 ns | fails; worst path S reg -> bank -> tree -> slice reg, 5.3 ns, 87% routing delay |

- Pre-route `flatten none` synthesis gave 42,772 LUTs; `rebuilt` lets Vivado optimise across the 225 tree modules (17.2k LUTs). Both are valid; the post-route netlist was simulated (324 sampled vectors, gapped + back-to-back + reset) with 0 errors.
- Throughput at 6 ns: 1 codeword per 15 cycles = 11.1 Mcodeword/s (about 37.5 Gbit/s of codeword output).
- Achievable clock about 190 MHz (5 ns target misses by 0.32 ns); the 6 ns critical path is `start` -> `accept` (fanout 1,218) -> S load, a control path that could be pipelined.

### Assembly wrapper (`bch3d_enc_tm_asm`): full 3,375-bit codeword output (option B)

Wraps the unchanged core. `slice` feeds 225 groups of 15-bit shift registers (group `i*15+i'`, enabled by `slice_valid`), so after 15 slices `cw_sr[g*15 + c]` = bit `(i,i',c)` of the flat codeword. A 3,375-bit hold register `codeword` loads from it one cycle after the last shift (`done_d`), and `cw_valid` pulses 2 cycles after the core's `done`. `codeword` is stable for 15 cycles, so a consumer can sample it any time after `cw_valid` without back-to-back hazards.

| Run (routed, `rebuilt`, 6.0 ns) | LUTs | FFs | WNS | WHS | Power |
|---|---:|---:|---:|---:|---:|
| core `bch3d_enc_tm` | 17,197 | 3,416 | +0.127 ns | +0.125 ns | 3.28 W |
| wrapper `bch3d_enc_tm_asm` | 17,196 | 10,200 | +0.062 ns | +0.081 ns | 3.35 W |

- Verification (N=15 only): RTL xsim, all 3,170 vectors (gapped, back-to-back, mid-run reset) 0 errors; post-route netlist, 324 sampled vectors, 0 errors. Checks include `cw_valid == done` delayed by 2 cycles and the codeword held for 15 cycles.
- New critical path: `slice_valid` -> clock-enable of the 3,375 shift FFs (95% routing). Registering/replicating that enable would be the first fix; not applied.
- `make enc-tm-sim` also runs the wrapper testbench (CI/N=7); that Makefile path was not run locally.
