# Plan: time-multiplexed 3-D BCH encoder (225 XOR trees, 15 cycles)

## Goal
Cut the 3,375 per-point 798-input XOR trees (540,000 LUTs, 99% of `bch3d_enc_opt`) to **225 trees reused over 15 cycles**. Everything up to and including the multiplier bank stays fully parallel.

## Key idea: pick the cycle axis so no routing mux is needed
Split the cube as cycle `c = i''` and tree `u = (i, i')`, giving 15 x 225 = 3,375 points. For class k with representative `(j1,j2,j3)` the exponent at point `(i,i',c)` is

    e = -(i*j1 + i'*j2) - c*j3  (mod 15)  =  e0(u) - c*j3

Tree `u` therefore always reads the same bank wire (exponent `e0(u)`), provided the bank sees a **rotated sub-message**:

    S_k(c) = S_k * alpha^(-c*j3)      Tr_d(S_k(c) * alpha^e0) = Tr_d(S_k * alpha^e)

So one 4-bit register per class, updated by a constant GF multiplier (`S_k <- S_k * alpha^-j3`, a few XORs, wiring only when `j3 = 0`), replaces a 15:1 routing mux in front of every tree input. A mux would cost about 225 x 798 x ~5 LUTs, roughly 900k, which is worse than what we save.

**Already checked:** `model/bch3d_tm.py` implements exactly this and matches the independent 3-D IDFFFT. It was tested on all unit vectors plus random messages for N=7 (T=1, T=2) and on random and all-0/all-1 messages for N=15, T=2. Sub-message values stay inside their subfield GF(2^d) for the d=1 and d=2 classes, because their `j3` is 0 or a multiple of 5.

## Architecture
| Block | Content | Status |
|---|---|---|
| `bch3d_msg_to_submsg` | unchanged, 0 LUTs | reuse |
| `S` register bank | 798 x 4 FF (<= 3,192 FF); `cycle==0` loads sub_msg, otherwise `S <- S * alpha^-j3` (one LUT level) | new |
| Multiplier bank | unchanged function, fully parallel, fed from `S` registers; can be pruned to exponents reachable at `i''=0` (10,608 of 11,500 outputs, -7.8%) | reuse + prune |
| 225 XOR trees | one per `(i,i')`, 798 inputs each, fixed wiring from bank outputs | 225 x 160 = 36,000 LUT |
| Control | 4-bit cycle counter, `start`, `busy`, `slice_valid`, `slice_idx` | new |
| Output | streaming `slice[224:0]` + `slice_idx`; optional 3,375-bit assembly shift register (FFs only, no LUTs) | new |

Interface: `msg` must be held for 15 cycles (or latched, +3,163 FF). Input rate is 1 codeword per 15 cycles. Optional pipeline registers between S -> bank -> tree -> output (adds latency, not cycles).

## Expected resources (estimates, to be confirmed by Vivado OOC)
| | Slice LUTs | Notes |
|---|---:|---|
| 225 trees | 36,000 | exact: 225 x 160 |
| Multiplier bank | ~4,200 to 4,600 | measured 4,597 unpruned |
| S rotate/load logic | ~3,000 to 6,000 | estimate, constant multipliers + load select |
| **Total** | **~44k to 47k** | ~3.6 to 3.9% of xc7v2000t |

That is about 91% below the current optimised design (544,597) and about 25x below the baseline (1,156,681). Flip-flops: ~3.2k (S) plus 225 or 3,375 (output). Cost: throughput drops 15x (1 codeword per 15 cycles instead of per cycle), and `msg` must be stable for 15 cycles.

## Work breakdown
1. **Model:** done, `model/bch3d_tm.py` (now in `make model-test`).
2. **Generator:** add `generate_tm_rtl()` to `model/gen_rtl.py` producing `bch3d_enc_tm[_nX_tY].v`.
   - per-class constant multiplier by `alpha^-j3`, emitted as XOR masks;
   - bank pruned to the `i''=0` exponent set;
   - 225 tree instances with wires chosen by `e0(u)`;
   - clocked wrapper: `clk, rst, start, msg -> slice, slice_idx, slice_valid, done`.
3. **Testbench:** generated, clocked, reusing `unit_cws`. Per vector: pulse `start`, collect 15 slices, assemble, compare with golden. Also check back-to-back messages, reset mid-run, and equivalence with `bch3d_enc_opt`.
4. **CI:** add the TM RTL to `make enc-sim` (N=7 full sim). N=15 stays generate-and-check, with an optional Verilator run if it fits CI time.
5. **Synthesis:** extend `scripts/vivado_ooc.tcl` to run the TM top (flatten `none` and `rebuilt`); add a timing run with a clock constraint (the design currently has no timing numbers). Record utilisation in `reports/` and update README and the article tables.
6. **Optional:** latch `msg`, add output assembly register, try cross-tree XOR sharing (not needed for this plan).

## Risks
- High fanout: each bank wire feeds about 15.6 tree inputs on average (179,550 inputs / 11,500 wires). Check routing and timing.
- Tree depth: 798-input XOR is about 5 LUT6 levels, plus the bank (1 to 2 levels) and S register; likely fine at 100 to 200 MHz, but unmeasured.
- Estimates for the S-update logic may move after synthesis.

## Which Claude model to use
- **Plan review / architecture decisions:** already done here; no stronger model needed.
- **Implementation (generator, testbench, CI, Vivado scripts):** **Claude Sonnet 5.5**. The task is well specified by this plan and the golden model gives a hard pass/fail, so a mid-tier model is enough and is the most cost-efficient.
- **Mechanical cleanup, README or table updates, report text:** **Claude Haiku 5.5** is sufficient.
- **Escalate to Opus 5.5** only if timing closure or an unexplained simulation mismatch takes more than a couple of attempts. Fable/Mythos are not warranted for this work.
