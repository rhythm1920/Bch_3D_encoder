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
    # so identical computation-class units share a clean module definition!
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

    Wait: how do we compute all K unit vectors + random vectors quickly for N=15?
    Let's check! By linearity, for each message bit `b` (0..K-1) belonging to class `c`
    at offset `i = b - c.bit_offset`, its sub-message is simply `S_c = alpha^(i * step)`,
    and all other classes have `S = 0`!
    So the codeword for unit vector `1 << b` is computed in O(N^3) (3375 ops) instead
    of O(N^3 * N_CLASS)! For all 3163 unit vectors at N=15, 3163 * 3375 = 10.6M ops,
    which takes < 0.2 seconds in Python using integers!
    Even better: we cross-check random vectors with the full separable 3-D IDFFFT (`encode_idffft`)
    AND compute the exact generator matrix rows from `encode_idffft` or single-class IDFFFT!
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
    generate_testbench(m, tb_path, args.vec_dir, num_random=args.num_random, include_all_unit_vectors=True)


if __name__ == "__main__":
    main()
