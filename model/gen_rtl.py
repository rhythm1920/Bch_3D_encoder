#!/usr/bin/env python3
"""
RTL and Testbench Generator for the 3-D BCH Spectral-Domain Encoder.

Generates two architectures from the mathematical golden model (model/bch3d.py):
  1. Baseline (`bch3d_enc_baseline`):
       bch3d_msg_to_submsg -> N^3 parallel IDFFFT point blocks (`bch3d_idfft_pt_<p>`),
       each computing its N_CLASS conjugate-block products Tr_d(S_k * alpha^(-(i.J_k)))
       and XOR-summing them to form codeword[p] (Report Fig. 2).

  2. Optimised (`bch3d_enc_opt`):
       bch3d_msg_to_submsg -> N_CLASS Computation-Class Multiplier Units (`bch3d_mul_unit_c<k>`),
       each computing ONLY the (at most N, e.g. 15, 5, 3, or 1) distinct products required
       by that class across the entire N^3 cube -> XOR routing network (`bch3d_xor_pt_<p>`)
       that fans out those precomputed products to all N^3 output points (Report Fig. 3).

Also generates self-checking Verilog testbenches (`tb_bch3d_enc`) verifying:
  - All-zero and all-one messages
  - All K unit-vector messages (1 << b for b in 0..K-1), which by GF(2) linearity
    mathematically proves correctness for all 2^K messages
  - Random messages against the independent separable 3-D IDFFFT golden model
  - Bit-for-bit equivalence between `bch3d_enc_baseline` and `bch3d_enc_opt`
"""
import argparse
import os
import random
import sys
import time
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bch3d import BCH3DModel, ClassInfo


def _xor_expr(terms: List[str], per_line: int = 16) -> str:
    if not terms:
        return "1'b0"
    if len(terms) <= per_line:
        return " ^ ".join(terms)
    chunks = [
        " ^ ".join(terms[i : i + per_line])
        for i in range(0, len(terms), per_line)
    ]
    return " ^\n        ".join(chunks)


def _s_mask_expr(s_var: str, mask: int, M: int) -> str:
    bits = [f"{s_var}[{r}]" for r in range(M) if (mask >> r) & 1]
    if not bits:
        return "1'b0"
    return " ^ ".join(bits)


def _s_mask_off(s_var: str, off: int, mask: int, M: int) -> str:
    bits = [f"{s_var}[{off + r}]" for r in range(M) if (mask >> r) & 1]
    return " ^ ".join(bits) if bits else "1'b0"


def generate_baseline_rtl(m: BCH3DModel, out_path: str):
    """
    Emit `bch3d_enc_baseline.v` for parameters (m.N, m.T, m.prim_poly).
    Architecture (Report Fig. 2):
      - `bch3d_msg_to_submsg` splits `msg` into NC M-bit sub-messages `S_k`.
      - Each of the N^3 points `p` independently evaluates its NC conjugate-block
        contributions Tr_d(S_k * alpha^(-(i.J_k))) from `sub_msg` and XOR-sums them
        inside a per-point module instance `u_pt_p<p>` (`bch3d_base_pt_<tag>_w<nb>`),
        preventing cross-point multiplier sharing in the baseline while allowing
        full LUT6 tree packing within each point.
    """
    N, T, M, K, NC, NNN = m.N, m.T, m.M, m.K, m.N_CLASS, m.NNN
    tag = f"n{N}_t{T}"
    mod_name = "bch3d_enc_baseline" if (N == 15 and T == 2) else f"bch3d_enc_baseline_{tag}"

    pt_bits_list = []
    unique_widths = set()
    for p in range(NNN):
        ia, ib, ic = m.lin_to_pt(p)
        bits_p = []
        for c in m.classes:
            e = (-(ia * c.rep[0] + ib * c.rep[1] + ic * c.rep[2])) % N
            mask = c.trace_masks[e]
            for r in range(M):
                if (mask >> r) & 1:
                    bits_p.append(f"sub_msg[{M*c.idx + r}]")
        pt_bits_list.append(bits_p)
        unique_widths.add(len(bits_p))

    lines = [
        "// -----------------------------------------------------------------------------",
        f"// Auto-generated Baseline 3-D BCH Encoder (N={N}, T={T}, M={M}, K={K}, N_CLASS={NC})",
        "// Architecture: bch3d_msg_to_submsg -> N^3 independent per-point IDFFFT units",
        "// -----------------------------------------------------------------------------",
        "`timescale 1ns/1ps",
        "",
    ]

    for w in sorted(unique_widths):
        pt_mod = f"bch3d_base_pt_{tag}_w{w}"
        lines.append(f"module {pt_mod} (")
        lines.append(f"    input  wire [{w-1}:0] in_bits,")
        lines.append("    output wire         out_bit")
        lines.append(");")
        lines.append("    assign out_bit = ^in_bits;")
        lines.append("endmodule")
        lines.append("")

    lines.extend([
        f"module {mod_name} (",
        f"    input  wire [{K-1}:0]   msg,",
        f"    output wire [{NNN-1}:0] codeword",
        ");",
        f"    wire [{M*NC-1}:0] sub_msg;",
        "",
        f"    bch3d_msg_to_submsg #(.N({N}), .T({T}), .PRIM_POLY({M+1}'d{m.prim_poly})) u_submsg (",
        "        .msg     (msg),",
        "        .sub_msg (sub_msg)",
        "    );",
        "",
    ])

    for p, bits_p in enumerate(pt_bits_list):
        w = len(bits_p)
        pt_mod = f"bch3d_base_pt_{tag}_w{w}"
        concat_expr = "{" + ", ".join(bits_p) + "}"
        lines.append(f"    {pt_mod} u_pt_p{p} (.in_bits({concat_expr}), .out_bit(codeword[{p}]));")

    lines.append("endmodule")
    lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def slice_points(m: BCH3DModel, plane: int = 0) -> List[int]:
    """Linear indices of the N^2 points with i'' == plane (one TM cycle's worth of outputs)."""
    return [p for p in range(m.NNN) if m.lin_to_pt(p)[2] == plane]


def base_slice_mod_name(m: BCH3DModel) -> str:
    return f"bch3d_enc_baseline_slice{m.N * m.N}_reg_n{m.N}_t{m.T}"


def generate_baseline_slice_reg_rtl(m: BCH3DModel, out_path: str, plane: int = 0):
    """
    Registered N^2-point slice of the baseline, for place-and-route estimates of the full design.
    Same per-point units as `bch3d_enc_baseline`, but only for points with i'' == plane;
    `msg` is registered on input and the slice on output, so timing is reg -> encoder -> reg.
    Output bit k is codeword[slice_points(m)[k]].
    """
    N, T, M, K, NC = m.N, m.T, m.M, m.K, m.N_CLASS
    tag = f"n{N}_t{T}"
    pts = slice_points(m, plane)
    S = len(pts)
    mod_name = base_slice_mod_name(m)

    pt_bits_list = []
    for p in pts:
        ia, ib, ic = m.lin_to_pt(p)
        bits_p = []
        for c in m.classes:
            e = (-(ia * c.rep[0] + ib * c.rep[1] + ic * c.rep[2])) % N
            mask = c.trace_masks[e]
            for r in range(M):
                if (mask >> r) & 1:
                    bits_p.append(f"sub_msg[{M*c.idx + r}]")
        pt_bits_list.append(bits_p)

    lines = [
        "// -----------------------------------------------------------------------------",
        f"// Auto-generated registered baseline slice (N={N}, T={T}): {S} points with i''={plane}",
        "// msg_q <= msg; slice <= per-point IDFFFT units(msg_q). Output bit k = codeword[pts[k]]",
        "// pts = " + " ".join(str(p) for p in pts),
        "// -----------------------------------------------------------------------------",
        "`timescale 1ns/1ps",
        "",
    ]
    for w in sorted({len(b) for b in pt_bits_list}):
        lines += [
            f"module bch3d_base_pt_{tag}_w{w} (",
            f"    input  wire [{w-1}:0] in_bits,",
            "    output wire         out_bit",
            ");",
            "    assign out_bit = ^in_bits;",
            "endmodule",
            "",
        ]
    lines += [
        f"module {mod_name} (",
        "    input  wire          clk,",
        f"    input  wire [{K-1}:0] msg,",
        f"    output reg  [{S-1}:0] slice",
        ");",
        f"    reg  [{K-1}:0] msg_q;",
        f"    wire [{M*NC-1}:0] sub_msg;",
        f"    wire [{S-1}:0] slice_c;",
        "",
        f"    bch3d_msg_to_submsg #(.N({N}), .T({T}), .PRIM_POLY({M+1}'d{m.prim_poly})) u_submsg (",
        "        .msg     (msg_q),",
        "        .sub_msg (sub_msg)",
        "    );",
        "",
    ]
    for k, bits_p in enumerate(pt_bits_list):
        concat_expr = "{" + ", ".join(bits_p) + "}"
        lines.append(f"    bch3d_base_pt_{tag}_w{len(bits_p)} u_pt_p{pts[k]} (.in_bits({concat_expr}), .out_bit(slice_c[{k}]));")
    lines += [
        "",
        "    always @(posedge clk) begin",
        "        msg_q <= msg;",
        "        slice <= slice_c;",
        "    end",
        "endmodule",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_baseline_slice_testbench(m: BCH3DModel, tb_path: str, n_vecs: int, msg_mem: str, cw_mem: str,
                                      plane: int = 0):
    """Pipelined TB: one vector per cycle, slice compared against golden codeword bits 2 cycles later."""
    N, T, K, NNN = m.N, m.T, m.K, m.NNN
    tag = f"n{N}_t{T}"
    pts = slice_points(m, plane)
    S = len(pts)
    sel = "{" + ", ".join(f"cw[{p}]" for p in reversed(pts)) + "}"
    L = [
        "`timescale 1ns/1ps",
        f"module tb_{base_slice_mod_name(m)};",
        f"    localparam integer K_BITS = {K}, NNN = {NNN}, S = {S}, N_VECS = {n_vecs};",
        "    reg clk = 0;",
        "    reg  [K_BITS-1:0] msg = 0;",
        "    wire [S-1:0] slice;",
        "    reg  [K_BITS-1:0] vec_msg [0:N_VECS-1];",
        "    reg  [NNN-1:0]    vec_cw  [0:N_VECS-1];",
        "    reg  [NNN-1:0]    cw;",
        "    reg  [S-1:0]      exp_s;",
        "    integer errors = 0, k;",
        "    always #5 clk = ~clk;",
        f"    {base_slice_mod_name(m)} dut (.clk(clk), .msg(msg), .slice(slice));",
        "",
        "    initial begin",
        f'        $readmemh("{msg_mem}", vec_msg);',
        f'        $readmemh("{cw_mem}",  vec_cw);',
        "        for (k = 0; k < N_VECS + 2; k = k + 1) begin",
        "            @(negedge clk);",
        "            if (k >= 2) begin",
        "                cw = vec_cw[k-2];",
        f"                exp_s = {sel};",
        "                if (slice !== exp_s) begin",
        '                    if (errors < 10) $display("ERROR vec %0d: slice mismatch", k-2);',
        "                    errors = errors + 1;",
        "                end",
        "            end",
        "            if (k < N_VECS) msg = vec_msg[k];",
        "        end",
        '        $display("INFO: Checked %0d vectors (registered baseline slice), errors=%0d", N_VECS, errors);',
        '        if (errors == 0) $display("PASS");',
        '        else             $display("FAIL: %0d errors", errors);',
        "        $finish;",
        "    end",
        "endmodule",
        "",
    ]
    with open(tb_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def generate_opt_rtl(m: BCH3DModel, out_path: str):
    """
    Emit `bch3d_enc_opt.v` for parameters (m.N, m.T, m.prim_poly).
    Architecture (Report Fig. 3):
      1. `bch3d_msg_to_submsg` splits `msg` into NC M-bit sub-messages `S_k`.
      2. For each conjugate class k, a dedicated reduced multiplier unit computes
         ONLY the `|distinct_exps|` distinct products Tr_d(S_k * alpha^e) (15, 5, 3, or 1).
         Furthermore, within a class, exponents that map to the exact same bit-mask
         (if any) or all distinct exponents in the computation class are evaluated once
         in the multiplier bank `bch3d_mul_bank_<tag>`.
      3. The routing + XOR network wires the appropriate precomputed product from each
         class to each of the N^3 output points `(i, i', i'')` and XORs them.
    """
    N, T, M, K, NC, NNN = m.N, m.T, m.M, m.K, m.N_CLASS, m.NNN
    tag = f"n{N}_t{T}"

    # Assign a flat wire index in `mul_out` for every (class_idx, exp) pair in c.distinct_exps
    wire_map = {}  # (class_idx, exp) -> flat index in mul_out
    total_mul_wires = 0
    for c in m.classes:
        for e in c.distinct_exps:
            wire_map[(c.idx, e)] = total_mul_wires
            total_mul_wires += 1

    lines = [
        "// -----------------------------------------------------------------------------",
        f"// Auto-generated Optimised 3-D BCH Encoder (N={N}, T={T}, M={M}, K={K}, N_CLASS={NC})",
        "// Architecture: bch3d_msg_to_submsg -> Computation-Class Multiplier Bank -> XOR Routing Network",
        f"// Total distinct class-twiddle outputs across all {NC} classes: {total_mul_wires}",
        "// -----------------------------------------------------------------------------",
        "`timescale 1ns/1ps",
        "",
    ]

    # Emit one Multiplier Unit module per unique (class_size, tuple(distinct_exps)) signature
    # so identical computation-class units share a module definition.
    sig_to_mod = {}
    for c in m.classes:
        sig = (c.size, tuple(c.distinct_exps))
        if sig not in sig_to_mod:
            mod_id = f"bch3d_mul_unit_{tag}_sz{c.size}_cc{len(c.distinct_exps)}_t{len(sig_to_mod)}"
            sig_to_mod[sig] = mod_id
            n_out = len(c.distinct_exps)
            lines.append(f"// Multiplier unit: orbit size={c.size}, computation class={n_out}, exps={list(c.distinct_exps)}")
            lines.append(f"module {mod_id} (")
            lines.append(f"    input  wire [{M-1}:0] s,")
            lines.append(f"    output wire [{n_out-1}:0] p")
            lines.append(");")
            for idx_e, e in enumerate(c.distinct_exps):
                mask = c.trace_masks[e]
                expr = _s_mask_expr("s", mask, M)
                lines.append(f"    assign p[{idx_e}] = {expr}; // alpha^{e}")
            lines.append("endmodule")
            lines.append("")

    # Multiplier bank instantiating the NC reduced multiplier units
    lines.append(f"module bch3d_mul_bank_{tag} (")
    lines.append(f"    input  wire [{M*NC-1}:0] sub_msg,")
    lines.append(f"    output wire [{total_mul_wires-1}:0] mul_out")
    lines.append(");")
    for c in m.classes:
        sig = (c.size, tuple(c.distinct_exps))
        mod_id = sig_to_mod[sig]
        lo = wire_map[(c.idx, c.distinct_exps[0])]
        n_out = len(c.distinct_exps)
        lines.append(
            f"    {mod_id} u_mul_c{c.idx} ("
            f".s(sub_msg[{M*c.idx} +: {M}]), "
            f".p(mul_out[{lo} +: {n_out}]));"
        )
    lines.append("endmodule")
    lines.append("")

    # Per-point NC-input XOR tree module (reused across all N^3 output points)
    xor_mod = f"bch3d_xor_tree_{tag}"
    lines.append(f"// Per-point {NC}-input XOR adder tree")
    lines.append(f"module {xor_mod} (")
    lines.append(f"    input  wire [{NC-1}:0] in_bits,")
    lines.append("    output wire         out_bit")
    lines.append(");")
    lines.append("    assign out_bit = ^in_bits;")
    lines.append("endmodule")
    lines.append("")

    # Top-level optimised encoder with routing + per-point XOR trees
    mod_name = "bch3d_enc_opt" if (N == 15 and T == 2) else f"bch3d_enc_opt_{tag}"
    lines.append(f"module {mod_name} (")
    lines.append(f"    input  wire [{K-1}:0]   msg,")
    lines.append(f"    output wire [{NNN-1}:0] codeword")
    lines.append(");")
    lines.append(f"    wire [{M*NC-1}:0]       sub_msg;")
    lines.append(f"    wire [{total_mul_wires-1}:0] mul_out;")
    lines.append("")
    lines.append(f"    bch3d_msg_to_submsg #(.N({N}), .T({T}), .PRIM_POLY({M+1}'d{m.prim_poly})) u_submsg (")
    lines.append("        .msg     (msg),")
    lines.append("        .sub_msg (sub_msg)")
    lines.append("    );")
    lines.append("")
    lines.append(f"    bch3d_mul_bank_{tag} u_mul_bank (")
    lines.append("        .sub_msg (sub_msg),")
    lines.append("        .mul_out (mul_out)")
    lines.append("    );")
    lines.append("")
    lines.append("    // Routing + XOR network: route precomputed class products to each output point")
    for p in range(NNN):
        ia, ib, ic = m.lin_to_pt(p)
        terms = []
        for c in m.classes:
            e = (-(ia * c.rep[0] + ib * c.rep[1] + ic * c.rep[2])) % N
            w_idx = wire_map[(c.idx, e)]
            terms.append(f"mul_out[{w_idx}]")
        concat_expr = "{" + ", ".join(terms) + "}"
        lines.append(f"    {xor_mod} u_xor_p{p} (.in_bits({concat_expr}), .out_bit(codeword[{p}]));")
    lines.append("endmodule")
    lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def tm_mod_name(m: BCH3DModel) -> str:
    return "bch3d_enc_tm" if (m.N == 15 and m.T == 2) else f"bch3d_enc_tm_n{m.N}_t{m.T}"


def generate_tm_rtl(m: BCH3DModel, out_path: str):
    """
    Emit `bch3d_enc_tm[_nX_tY].v`: time-multiplexed encoder (see model/bch3d_tm.py).
    Cycle c computes the N x N slice i'' = c with N^2 XOR trees (tree u = (i, i')).
    Tree u always reads bank wire e0(u) = -(i*j1 + i'*j2) mod N; the per-class register S_k is
    rotated by alpha^(-j3) each cycle so that the bank output equals the (i, i', c) twiddle product.
    The sub-message is latched into S on `start`, so `msg` need not be held.
    """
    N, T, M, K, NC = m.N, m.T, m.M, m.K, m.N_CLASS
    NN = N * N
    tag = f"n{N}_t{T}"
    mod_name = tm_mod_name(m)

    # Bank wires: only exponents reachable at i'' = 0
    wire_map = {}
    nw = 0
    for c in m.classes:
        e0s = sorted({(-(ia * c.rep[0] + ib * c.rep[1])) % N for ia in range(N) for ib in range(N)})
        for e in e0s:
            wire_map[(c.idx, e)] = nw
            nw += 1

    lines = [
        "// -----------------------------------------------------------------------------",
        f"// Auto-generated Time-Multiplexed 3-D BCH Encoder (N={N}, T={T}, M={M}, K={K}, N_CLASS={NC})",
        f"// {NN} XOR trees reused over {N} cycles; bank wires (pruned to i''=0 exponents): {nw}",
        "// Interface: pulse `start` with `msg` (latched); slices i''=0..N-1 follow on consecutive",
        "// cycles with slice_valid/slice_idx (1 cycle after the internal state). `start` is accepted",
        "// when idle or in the last busy cycle (back-to-back, 1 codeword per N cycles).",
        "// -----------------------------------------------------------------------------",
        "`timescale 1ns/1ps",
        "",
        f"module bch3d_tm_bank_{tag} (",
        f"    input  wire [{M*NC-1}:0] S,",
        f"    output wire [{nw-1}:0] mul_out",
        ");",
    ]
    for (ci, e), w in wire_map.items():
        c = m.classes[ci]
        expr = _s_mask_off("S", M*ci, c.trace_masks[e], M)
        lines.append(f"    assign mul_out[{w}] = {expr}; // class {ci} alpha^{e}")
    lines += ["endmodule", ""]

    tree_mod = f"bch3d_tm_tree_{tag}"
    lines += [
        f"module {tree_mod} (input wire [{NC-1}:0] in_bits, output wire out_bit);",
        "    assign out_bit = ^in_bits;",
        "endmodule",
        "",
        f"module {mod_name} (",
        "    input  wire clk,",
        "    input  wire rst,",
        "    input  wire start,",
        f"    input  wire [{K-1}:0] msg,",
        f"    output reg  [{NN-1}:0] slice,",
        f"    output reg  [{M-1}:0] slice_idx,",
        "    output reg  slice_valid,",
        "    output wire done",
        ");",
        f"    wire [{M*NC-1}:0] sub_msg;",
        f"    reg  [{M*NC-1}:0] S;",
        f"    wire [{M*NC-1}:0] S_rot;",
        f"    wire [{nw-1}:0] mul_out;",
        f"    wire [{NN-1}:0] slice_c;",
        "    reg busy;",
        f"    reg [{M-1}:0] cyc;",
        f"    wire last   = busy && (cyc == {M}'d{N-1});",
        "    wire accept = start && (!busy || last);",
        "",
        f"    bch3d_msg_to_submsg #(.N({N}), .T({T}), .PRIM_POLY({M+1}'d{m.prim_poly})) u_submsg (",
        "        .msg     (msg),",
        "        .sub_msg (sub_msg)",
        "    );",
        "",
        "    // S <- S * alpha^(-j3) per class (constant GF multiplier; wiring only when j3 = 0)",
    ]
    for c in m.classes:
        g = m.exp[(-c.rep[2]) % N]
        cols = [m.mul(1 << r, g) for r in range(M)]
        for q in range(M):
            rmask = sum(1 << r for r in range(M) if (cols[r] >> q) & 1)
            lines.append(
                f"    assign S_rot[{M*c.idx + q}] = {_s_mask_off('S', M*c.idx, rmask, M)};"
            )
    lines += [
        "",
        f"    bch3d_tm_bank_{tag} u_bank (.S(S), .mul_out(mul_out));",
        "",
        "    // One XOR tree per (i, i'); fixed wiring from bank wire e0(i, i')",
    ]
    for ia in range(N):
        for ib in range(N):
            terms = []
            for c in m.classes:
                e0 = (-(ia * c.rep[0] + ib * c.rep[1])) % N
                terms.append(f"mul_out[{wire_map[(c.idx, e0)]}]")
            lines.append(
                f"    {tree_mod} u_tree_{ia}_{ib} (.in_bits({{{', '.join(terms)}}}), .out_bit(slice_c[{ia*N+ib}]));"
            )
    lines += [
        "",
        "    always @(posedge clk) begin",
        "        if (rst) begin",
        "            busy        <= 1'b0;",
        f"            cyc         <= {M}'d0;",
        "            slice_valid <= 1'b0;",
        f"            slice_idx   <= {M}'d0;",
        "        end else begin",
        "            slice_valid <= busy;",
        "            slice_idx   <= cyc;",
        f"            if (accept) begin busy <= 1'b1; cyc <= {M}'d0; end",
        f"            else if (last) begin busy <= 1'b0; cyc <= {M}'d0; end",
        f"            else if (busy) cyc <= cyc + {M}'d1;",
        "        end",
        "        if (accept)    S <= sub_msg;",
        "        else if (busy) S <= S_rot;",
        "        slice <= slice_c;",
        "    end",
        "",
        f"    assign done = slice_valid && (slice_idx == {M}'d{N-1});",
        "endmodule",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return nw


def tm_asm_mod_name(m: BCH3DModel) -> str:
    return tm_mod_name(m).replace("bch3d_enc_tm", "bch3d_enc_tm_asm", 1)


def generate_tm_asm_rtl(m: BCH3DModel, out_path: str):
    """
    Emit the assembly wrapper `bch3d_enc_tm_asm[_nX_tY].v` around the (unchanged) TM core.
      * cw_sr: N^2 groups of N bits; group g = i*N + i' is an N-bit shift register that takes
        slice[g] whenever slice_valid is high, so after N slices cw_sr[g*N + c] = bit (i, i', c)
        (the flat codeword layout i*N^2 + i'*N + i''). Shift/enable wiring only, no LUTs.
      * codeword (hold register): loaded from cw_sr one cycle after the last shift, so it is stable
        for N cycles per codeword. cw_valid pulses for one cycle when a new codeword appears.
    """
    N, T, K, NNN = m.N, m.T, m.K, m.NNN
    NN = N * N
    core, mod = tm_mod_name(m), tm_asm_mod_name(m)
    M = m.M
    lines = [
        "// -----------------------------------------------------------------------------",
        f"// Auto-generated assembly wrapper for the time-multiplexed encoder (N={N}, T={T})",
        f"// {NN} x {N}-bit slice shift registers -> {NNN}-bit hold register (stable for {N} cycles)",
        "// Latency from the core's `done` cycle t: cw_sr complete in t+1, codeword/cw_valid in t+2.",
        "// -----------------------------------------------------------------------------",
        "`timescale 1ns/1ps",
        f"module {mod} (",
        "    input  wire clk,",
        "    input  wire rst,",
        "    input  wire start,",
        f"    input  wire [{K-1}:0] msg,",
        f"    output wire [{NN-1}:0] slice,",
        f"    output wire [{M-1}:0] slice_idx,",
        "    output wire slice_valid,",
        "    output wire done,",
        f"    output reg  [{NNN-1}:0] codeword,",
        "    output reg  cw_valid",
        ");",
        f"    {core} u_core (.clk(clk), .rst(rst), .start(start), .msg(msg),",
        "        .slice(slice), .slice_idx(slice_idx), .slice_valid(slice_valid), .done(done));",
        "",
        f"    reg [{NNN-1}:0] cw_sr;",
        "    reg done_d;",
        "",
        "    genvar g;",
        "    generate for (g = 0; g < %d; g = g + 1) begin : grp" % NN,
        "        always @(posedge clk) if (slice_valid) begin",
        f"            cw_sr[g*{N} +: {N-1}] <= cw_sr[g*{N}+1 +: {N-1}];",
        f"            cw_sr[g*{N}+{N-1}]    <= slice[g];",
        "        end",
        "    end endgenerate",
        "",
        "    always @(posedge clk) begin",
        "        if (rst) begin done_d <= 1'b0; cw_valid <= 1'b0; end",
        "        else begin done_d <= done; cw_valid <= done_d; end",
        "        if (done_d) codeword <= cw_sr;",
        "    end",
        "endmodule",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_tm_asm_testbench(m: BCH3DModel, tb_path: str, n_vecs: int, msg_mem: str, cw_mem: str):
    """Clocked TB for the assembly wrapper: codeword compared on cw_valid, held for N cycles."""
    N, T, M, K, NNN = m.N, m.T, m.M, m.K, m.NNN
    NN = N * N
    tag = f"n{N}_t{T}"
    L = [
        "`timescale 1ns/1ps",
        f"module tb_bch3d_enc_tm_asm_{tag};",
        f"    localparam integer N = {N}, K_BITS = {K}, NNN = {NNN}, NN = {NN}, N_VECS = {n_vecs}, MW = {M};",
        "    reg clk = 0, rst = 1, start = 0;",
        "    reg  [K_BITS-1:0] msg = 0;",
        "    wire [NN-1:0] slice; wire [MW-1:0] slice_idx; wire slice_valid, done, cw_valid;",
        "    wire [NNN-1:0] codeword;",
        "    reg  [K_BITS-1:0] vec_msg [0:N_VECS-1];",
        "    reg  [NNN-1:0]    vec_cw  [0:N_VECS-1];",
        "    integer errors = 0, exp_idx = 0, k, n, age = 1000;",
        "    reg [NNN-1:0] cap;",
        "    reg done_q1 = 0, done_q2 = 0;",
        "    always #5 clk = ~clk;",
        "",
        f"    {tm_asm_mod_name(m)} dut (.clk(clk), .rst(rst), .start(start), .msg(msg),",
        "        .slice(slice), .slice_idx(slice_idx), .slice_valid(slice_valid), .done(done),",
        "        .codeword(codeword), .cw_valid(cw_valid));",
        "",
        "    // Monitor: cw_valid must be done delayed by 2 cycles; codeword must match golden and",
        "    // then stay stable for the next N-1 cycles (until the next codeword can be loaded).",
        "    always @(posedge clk) begin",
        "        if (!rst) begin",
        "            if (cw_valid !== done_q2) begin",
        '                if (errors < 10) $display("ERROR: cw_valid not done delayed by 2"); errors = errors + 1;',
        "            end",
        "        end",
        "        done_q2 <= done_q1; done_q1 <= done;",
        "        if (cw_valid) begin",
        "            if (exp_idx >= N_VECS) begin",
        '                if (errors < 10) $display("ERROR: unexpected codeword"); errors = errors + 1;',
        "            end else if (codeword !== vec_cw[exp_idx]) begin",
        '                if (errors < 10) $display("ERROR vec %0d: assembled codeword mismatch vs golden", exp_idx);',
        "                errors = errors + 1;",
        "            end",
        "            cap = codeword; age = 0; exp_idx = exp_idx + 1;",
        "        end else if (age < N - 1) begin",
        "            age = age + 1;",
        "            if (codeword !== cap) begin",
        '                if (errors < 10) $display("ERROR: codeword not held for N cycles"); errors = errors + 1;',
        "            end",
        "        end",
        "    end",
        "",
        "    task run_pass(input integer nvec, input integer g);",
        "        begin",
        "            exp_idx = 0; age = 1000;",
        "            for (k = 0; k < nvec; k = k + 1) begin",
        "                @(negedge clk); msg = vec_msg[k]; start = 1;",
        "                @(negedge clk); start = 0; msg = ~vec_msg[k];",
        "                repeat (N - 2 + g) @(negedge clk);",
        "            end",
        "            repeat (N + 6) @(negedge clk);",
        "            if (exp_idx !== nvec) begin",
        '                $display("ERROR: pass produced %0d codewords, expected %0d", exp_idx, nvec); errors = errors + 1;',
        "            end",
        "        end",
        "    endtask",
        "",
        "    initial begin",
        f'        $readmemh("{msg_mem}", vec_msg);',
        f'        $readmemh("{cw_mem}",  vec_cw);',
        "        repeat (30) @(negedge clk); rst = 0;",
        "        run_pass(N_VECS, 3);   // gapped",
        "        run_pass(N_VECS, 0);   // back-to-back, 1 codeword per N cycles",
        "        // reset mid-run: no codeword may appear, then a clean run",
        "        exp_idx = 0;",
        "        @(negedge clk); msg = vec_msg[1]; start = 1; @(negedge clk); start = 0;",
        "        repeat (3) @(negedge clk);",
        "        rst = 1; @(negedge clk); rst = 0;",
        "        repeat (2*N+4) @(negedge clk);",
        '        if (exp_idx !== 0) begin $display("ERROR: codeword after reset"); errors = errors + 1; end',
        "        run_pass(N_VECS < 20 ? N_VECS : 20, 1);",
        '        $display("INFO: Checked %0d vectors x2 passes + reset test (assembled codeword), errors=%0d", N_VECS, errors);',
        '        if (errors == 0) $display("PASS");',
        '        else             $display("FAIL: %0d errors", errors);',
        "        $finish;",
        "    end",
        "endmodule",
        "",
    ]
    with open(tb_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def generate_tm_testbench(m: BCH3DModel, tb_path: str, n_vecs: int, msg_mem: str, cw_mem: str):
    """Clocked TB for the TM encoder: gapped, back-to-back, mid-run reset, vs golden and `bch3d_enc_opt`."""
    N, T, M, K, NNN = m.N, m.T, m.M, m.K, m.NNN
    NN = N * N
    tag = f"n{N}_t{T}"
    opt_mod = "bch3d_enc_opt" if (N == 15 and T == 2) else f"bch3d_enc_opt_{tag}"
    L = [
        "`timescale 1ns/1ps",
        f"module tb_bch3d_enc_tm_{tag};",
        f"    localparam integer N = {N}, K_BITS = {K}, NNN = {NNN}, NN = {NN}, N_VECS = {n_vecs}, MW = {M};",
        "    reg clk = 0, rst = 1, start = 0;",
        "    reg  [K_BITS-1:0] msg = 0;",
        "    wire [NN-1:0] slice; wire [MW-1:0] slice_idx; wire slice_valid, done;",
        "    reg  [K_BITS-1:0] vec_msg [0:N_VECS-1];",
        "    reg  [NNN-1:0]    vec_cw  [0:N_VECS-1];",
        "    integer errors = 0, exp_idx = 0, i, k, a, b, gap, n;",
        "    reg [NNN-1:0] asm;",
        "    wire [NNN-1:0] cw_opt;",
        "    wire [K_BITS-1:0] msg_exp = vec_msg[exp_idx < N_VECS ? exp_idx : 0];",
        "    always #5 clk = ~clk;",
        "",
        f"    {tm_mod_name(m)} dut (.clk(clk), .rst(rst), .start(start), .msg(msg),",
        "        .slice(slice), .slice_idx(slice_idx), .slice_valid(slice_valid), .done(done));",
        f"    {opt_mod} u_opt (.msg(msg_exp), .codeword(cw_opt));",
        "",
        "    // Monitor: assemble slices; on done compare against golden and the optimised encoder",
        "    always @(posedge clk) if (slice_valid) begin",
        "        for (a = 0; a < N; a = a + 1) for (b = 0; b < N; b = b + 1)",
        "            asm[a*N*N + b*N + slice_idx] = slice[a*N + b];",
        "        if (done) begin",
        "            if (exp_idx >= N_VECS) begin",
        '                if (errors < 10) $display("ERROR: unexpected codeword"); errors = errors + 1;',
        "            end else begin",
        "                if (asm !== vec_cw[exp_idx]) begin",
        '                    if (errors < 10) $display("ERROR vec %0d: tm mismatch vs golden", exp_idx);',
        "                    errors = errors + 1;",
        "                end",
        "                if (asm !== cw_opt) begin",
        '                    if (errors < 10) $display("ERROR vec %0d: tm !== opt", exp_idx);',
        "                    errors = errors + 1;",
        "                end",
        "            end",
        "            exp_idx = exp_idx + 1;",
        "        end",
        "    end",
        "",
        "    // Issue `n` vectors, one start every N+gap cycles; msg is scrambled right after start",
        "    // (the encoder must have latched it).",
        "    task run_pass(input integer nvec, input integer g);",
        "        begin",
        "            exp_idx = 0;",
        "            for (k = 0; k < nvec; k = k + 1) begin",
        "                @(negedge clk); msg = vec_msg[k]; start = 1;",
        "                @(negedge clk); start = 0; msg = ~vec_msg[k];",
        "                repeat (N - 2 + g) @(negedge clk);",
        "            end",
        "            repeat (N + 4) @(negedge clk);",
        "            if (exp_idx !== nvec) begin",
        '                $display("ERROR: pass produced %0d codewords, expected %0d", exp_idx, nvec); errors = errors + 1;',
        "            end",
        "        end",
        "    endtask",
        "",
        "    initial begin",
        f'        $readmemh("{msg_mem}", vec_msg);',
        f'        $readmemh("{cw_mem}",  vec_cw);',
        "        repeat (3) @(negedge clk); rst = 0;",
        "        run_pass(N_VECS, 3);   // gapped",
        "        run_pass(N_VECS, 0);   // back-to-back, 1 codeword per N cycles",
        "        // reset mid-run: no further valid slices, then a clean run",
        "        exp_idx = 0;",
        "        @(negedge clk); msg = vec_msg[1]; start = 1; @(negedge clk); start = 0;",
        "        repeat (3) @(negedge clk);",
        "        rst = 1; @(negedge clk); rst = 0;",
        "        n = 0; repeat (2*N) begin @(negedge clk); if (slice_valid) n = n + 1; end",
        '        if (n !== 0 || exp_idx !== 0) begin $display("ERROR: activity after reset"); errors = errors + 1; end',
        "        run_pass(N_VECS < 20 ? N_VECS : 20, 1);",
        '        $display("INFO: Checked %0d vectors x2 passes + reset test, errors=%0d", N_VECS, errors);',
        '        if (errors == 0) $display("PASS");',
        '        else             $display("FAIL: %0d errors", errors);',
        "        $finish;",
        "    end",
        "endmodule",
        "",
    ]
    with open(tb_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def generate_testbench(
    m: BCH3DModel,
    tb_path: str,
    vec_dir: str,
    num_random: int = 20,
    include_all_unit_vectors: bool = True,
):
    """
    Generate a fast `$readmemh`-based self-checking testbench that tests BOTH
    `bch3d_enc_baseline` and `bch3d_enc_opt` against the golden model.

    Unit-vector codewords are built per class in O(N^3): a unit vector touches one class
    with S = alpha^(i*step), so every other class contributes 0. A random message is the XOR
    of its unit-vector codewords (GF(2) linearity) and is cross-checked against the
    independent separable 3-D IDFFFT (`encode_idffft`) and the spectral-null check.
    """
    os.makedirs(vec_dir, exist_ok=True)
    N, T, M, K, NC, NNN = m.N, m.T, m.M, m.K, m.N_CLASS, m.NNN
    tag = f"n{N}_t{T}"
    base_mod = "bch3d_enc_baseline" if (N == 15 and T == 2) else f"bch3d_enc_baseline_{tag}"
    opt_mod = "bch3d_enc_opt" if (N == 15 and T == 2) else f"bch3d_enc_opt_{tag}"

    # Precompute the N^3-bit codeword for each of the K unit vectors using the class trace table,
    # and verify random combinations against the independent separable 3-D IDFFFT (`encode_idffft`).
    t0 = time.time()
    unit_cws: List[int] = [0] * K
    # For each class c and each exponent e in c.distinct_exps, build the N^3-bit point mask
    # indicating which output points p have (-(i.J_c)) mod N == e:
    for c in m.classes:
        exp_to_pts_mask = {e: 0 for e in c.distinct_exps}
        for p in range(NNN):
            ia, ib, ic = m.lin_to_pt(p)
            e = (-(ia * c.rep[0] + ib * c.rep[1] + ic * c.rep[2])) % N
            exp_to_pts_mask[e] |= (1 << p)

        for i in range(c.size):
            b_idx = c.bit_offset + i
            # Sub-message when only bit i of class c is 1:
            s_val = 0
            for r in range(M):
                if (c.sub_masks[r] >> i) & 1:
                    s_val |= (1 << r)
            cw = 0
            for e, pts_mask in exp_to_pts_mask.items():
                if bin(s_val & c.trace_masks[e]).count("1") & 1:
                    cw ^= pts_mask
            unit_cws[b_idx] = cw

    def fast_encode(msg_int: int) -> int:
        acc = 0
        x = msg_int
        while x:
            lsb = x & -x
            b = lsb.bit_length() - 1
            acc ^= unit_cws[b]
            x ^= lsb
        return acc

    # Cross-check `fast_encode` against independent separable 3-D IDFFFT (`encode_idffft`)
    random.seed(12345 + N * 10 + T)
    check_msgs = [0, (1 << K) - 1, 1, 1 << (K // 2), 1 << (K - 1)]
    for _ in range(min(num_random, 10)):
        check_msgs.append(random.getrandbits(K))
    for v in check_msgs:
        cw_fast = fast_encode(v)
        cw_idft = m.encode_idffft(v)
        assert cw_fast == cw_idft, f"Generator matrix mismatch against separable 3-D IDFFFT for N={N}, T={T}!"
        assert m.decode_dffft_check_nulls(cw_fast), f"Spectral null check failed for N={N}, T={T}!"

    # Build vector list: directed + (all K unit vectors if requested) + random vectors
    vectors = []
    vectors.append((0, fast_encode(0)))
    vectors.append(((1 << K) - 1, fast_encode((1 << K) - 1)))
    if include_all_unit_vectors:
        for b in range(K):
            vectors.append((1 << b, unit_cws[b]))
    for _ in range(num_random):
        v = random.getrandbits(K)
        vectors.append((v, fast_encode(v)))

    n_vecs = len(vectors)
    msg_hex_w = (K + 3) // 4
    cw_hex_w = (NNN + 3) // 4
    msg_mem_path = os.path.join(vec_dir, f"msg_{tag}.mem").replace("\\", "/")
    cw_mem_path = os.path.join(vec_dir, f"cw_{tag}.mem").replace("\\", "/")

    with open(msg_mem_path, "w", encoding="utf-8") as fm, open(cw_mem_path, "w", encoding="utf-8") as fc:
        for v, cw in vectors:
            fm.write(f"{v:0{msg_hex_w}x}\n")
            fc.write(f"{cw:0{cw_hex_w}x}\n")

    tb_mod = f"tb_bch3d_enc_{tag}"
    tb_lines = [
        "`timescale 1ns/1ps",
        f"module {tb_mod};",
        f"    localparam integer K_BITS = {K};",
        f"    localparam integer NNN    = {NNN};",
        f"    localparam integer N_VECS = {n_vecs};",
        "",
        "    reg  [K_BITS-1:0] msg;",
        "    wire [NNN-1:0]    cw_base;",
        "    wire [NNN-1:0]    cw_opt;",
        "",
        "    reg  [K_BITS-1:0] vec_msg [0:N_VECS-1];",
        "    reg  [NNN-1:0]    vec_cw  [0:N_VECS-1];",
        "    integer i, errors;",
        "",
        "    `ifdef ONLY_BASE",
        f"    {base_mod} u_base (.msg(msg), .codeword(cw_base));",
        "    assign cw_opt = cw_base;",
        "    `elsif ONLY_OPT",
        f"    {opt_mod} u_opt  (.msg(msg), .codeword(cw_opt));",
        "    assign cw_base = cw_opt;",
        "    `else",
        f"    {base_mod} u_base (.msg(msg), .codeword(cw_base));",
        f"    {opt_mod} u_opt  (.msg(msg), .codeword(cw_opt));",
        "    `endif",
        "",
        "    initial begin",
        "        errors = 0;",
        f'        $readmemh("{msg_mem_path}", vec_msg);',
        f'        $readmemh("{cw_mem_path}",  vec_cw);',
        "        for (i = 0; i < N_VECS; i = i + 1) begin",
        "            msg = vec_msg[i];",
        "            #1;",
        "            if (cw_opt !== vec_cw[i]) begin",
        '                if (errors < 10) $display("ERROR vec %0d: opt mismatch vs golden", i);',
        "                errors = errors + 1;",
        "            end",
        "            if (cw_base !== vec_cw[i]) begin",
        '                if (errors < 10) $display("ERROR vec %0d: base mismatch vs golden", i);',
        "                errors = errors + 1;",
        "            end",
        "            if (cw_base !== cw_opt) begin",
        '                if (errors < 10) $display("ERROR vec %0d: base !== opt", i);',
        "                errors = errors + 1;",
        "            end",
        "        end",
        '        $display("INFO: Checked %0d vectors (K=%0d, NNN=%0d), errors=%0d", N_VECS, K_BITS, NNN, errors);',
        '        if (errors == 0) $display("PASS");',
        '        else             $display("FAIL: %0d errors", errors);',
        "        $finish;",
        "    end",
        "endmodule",
        "",
    ]
    with open(tb_path, "w", encoding="utf-8") as f:
        f.write("\n".join(tb_lines))

    print(
        f"[GEN] N={N} T={T}: {n_vecs} vectors (incl. all {K} unit vectors + {num_random} random, "
        f"cross-checked vs 3-D IDFFFT) written in {time.time()-t0:.2f}s"
    )
    return n_vecs, msg_mem_path, cw_mem_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--t", type=int, default=2)
    ap.add_argument("--rtl-dir", type=str, required=True)
    ap.add_argument("--tb-dir", type=str, required=True)
    ap.add_argument("--vec-dir", type=str, required=True)
    ap.add_argument("--num-random", type=int, default=20)
    args = ap.parse_args()

    poly = {3: 0b11, 7: 0b1011, 15: 0b10011}[args.n]
    m = BCH3DModel(args.n, args.t, poly)
    os.makedirs(args.rtl_dir, exist_ok=True)
    os.makedirs(args.tb_dir, exist_ok=True)
    os.makedirs(args.vec_dir, exist_ok=True)

    tag = f"n{args.n}_t{args.t}"
    base_name = "bch3d_enc_baseline.v" if (args.n == 15 and args.t == 2) else f"bch3d_enc_baseline_{tag}.v"
    opt_name = "bch3d_enc_opt.v" if (args.n == 15 and args.t == 2) else f"bch3d_enc_opt_{tag}.v"

    base_path = os.path.join(args.rtl_dir, base_name)
    opt_path = os.path.join(args.rtl_dir, opt_name)
    tb_path = os.path.join(args.tb_dir, f"tb_bch3d_enc_{tag}.v")

    t0 = time.time()
    generate_baseline_rtl(m, base_path)
    generate_opt_rtl(m, opt_path)
    print(
        f"[GEN] N={args.n} T={args.t} RTL written in {time.time()-t0:.2f}s: "
        f"base={os.path.getsize(base_path)//1024}KB, opt={os.path.getsize(opt_path)//1024}KB"
    )
    tm_path = os.path.join(args.rtl_dir, tm_mod_name(m) + ".v")
    nw = generate_tm_rtl(m, tm_path)
    print(f"[GEN] TM RTL: {tm_path} ({os.path.getsize(tm_path)//1024}KB, {nw} bank wires)")
    n_vecs, msg_mem, cw_mem = generate_testbench(
        m, tb_path, args.vec_dir, num_random=args.num_random, include_all_unit_vectors=True
    )
    generate_tm_testbench(
        m, os.path.join(args.tb_dir, f"tb_bch3d_enc_tm_{tag}.v"), n_vecs, msg_mem, cw_mem
    )
    generate_tm_asm_rtl(m, os.path.join(args.rtl_dir, tm_asm_mod_name(m) + ".v"))
    generate_tm_asm_testbench(
        m, os.path.join(args.tb_dir, f"tb_bch3d_enc_tm_asm_{tag}.v"), n_vecs, msg_mem, cw_mem
    )
    generate_baseline_slice_reg_rtl(m, os.path.join(args.rtl_dir, base_slice_mod_name(m) + ".v"))
    generate_baseline_slice_testbench(
        m, os.path.join(args.tb_dir, f"tb_{base_slice_mod_name(m)}.v"), n_vecs, msg_mem, cw_mem
    )


if __name__ == "__main__":
    main()
