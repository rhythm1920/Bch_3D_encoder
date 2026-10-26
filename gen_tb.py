#!/usr/bin/env python3
"""Python golden model + testbench generator for the parameterised converter."""
import random, sys
random.seed(7)

def model(N, T, poly):
    M = (N + 1).bit_length() - 1
    def apow(e):
        x = 1
        for _ in range(e):
            x <<= 1
            if (x >> M) & 1:
                x ^= poly
        return x
    nxt = lambda p: (2 * (p // (N * N)) % N) * N * N + (2 * ((p // N) % N) % N) * N + 2 * (p % N) % N
    cube = lambda p: all(1 <= v <= 2 * T for v in (p // (N * N), (p // N) % N, p % N))
    cls = []
    for p in range(N ** 3):
        o = [p]; q = nxt(p)
        while q != p:
            o.append(q); q = nxt(q)
        if min(o) == p and not any(cube(x) for x in o):
            cls.append(len(o))
    K = sum(cls)
    def enc(msg):
        out = 0; off = 0
        for k, d in enumerate(cls):
            S = 0
            for i in range(d):
                if (msg >> (off + i)) & 1:
                    S ^= apow(i * (N // ((1 << d) - 1)))
            out |= S << (M * k); off += d
        return out
    return M, K, len(cls), enc

cfgs = [(15, 2, 0b10011), (15, 1, 0b10011), (7, 1, 0b1011), (7, 2, 0b1011)]
o = ["`timescale 1ns/1ps", "module tb_bch3d_msg_to_submsg;", "    integer errors = 0;"]
init = []
for n, (N, T, P) in enumerate(cfgs):
    M, K, NC, enc = model(N, T, P)
    vs = [0, (1 << K) - 1, random.getrandbits(K), random.getrandbits(K), random.getrandbits(K)]
    o += [
        f"    // config {n}: N={N} T={T} poly={P:#b}  -> M={M} K={K} classes={NC}",
        f"    reg  [{K-1}:0] m{n}; wire [{M*NC-1}:0] s{n};",
        f"    bch3d_msg_to_submsg #(.N({N}),.T({T}),.PRIM_POLY({P})) dut{n}(.msg(m{n}),.sub_msg(s{n}));",
        f"    task chk{n}(input [{K-1}:0] m, input [{M*NC-1}:0] e); begin m{n}=m; #1;",
        f"        if (s{n} !== e) begin errors=errors+1; $display(\"cfg{n} MISMATCH msg=%h\", m); end end endtask",
    ]
    init += [f"        chk{n}({K}'h{v:x}, {M*NC}'h{enc(v):x});" for v in vs]
    print(N, T, "M", M, "K", K, "NC", NC, file=sys.stderr)

o += [
    "    initial begin",
    *init,
    "        if (errors==0) $display(\"PASS\"); else $display(\"FAIL: %0d errors\", errors);",
    "        $finish;",
    "    end",
    "endmodule",
]
out_path = sys.argv[1] if len(sys.argv) > 1 else "tb_bch3d_msg_to_submsg.v"
open(out_path, "w").write("\n".join(o) + "\n")
