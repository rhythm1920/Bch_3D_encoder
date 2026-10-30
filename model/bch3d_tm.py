#!/usr/bin/env python3
"""
Reference model of the time-multiplexed ("225 trees, N cycles") 3-D BCH encoder.

Cycle c (= output coordinate i'' = c) produces the N x N slice c of the codeword:
  * every class keeps a register S_k, loaded with its sub-message at cycle 0;
  * the multiplier bank sees S_k and, for tree u = (i, i'), the product
        Tr_d(S_k * alpha^e0(u)),  e0(u) = -(i*j1 + i'*j2) mod N      (fixed wire per tree);
  * at the end of the cycle S_k <- S_k * alpha^(-j3), a constant multiplication, so at cycle c
        S_k(c) = S_k * alpha^(-c*j3)  and the tree output equals the full-cube point (i, i', c).
This model checks that scheme against the independent separable 3-D IDFFFT.
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bch3d import BCH3DModel


def encode_tm(m: BCH3DModel, msg: int):
    """Return (codeword, per-cycle slices). Slice c is an N*N-bit int, bit i*N+i' = tree (i, i')."""
    N = m.N
    S = m.msg_to_submsg(msg)
    step = [m.exp[(-c.rep[2]) % N] for c in m.classes]          # alpha^(-j3) per class
    cw, slices = 0, []
    for cyc in range(N):
        sl = 0
        for ia in range(N):
            for ib in range(N):
                bit = 0
                for c, s in zip(m.classes, S):
                    e0 = (-(ia * c.rep[0] + ib * c.rep[1])) % N
                    bit ^= bin(s & c.trace_masks[e0]).count("1") & 1
                if bit:
                    sl |= 1 << (ia * N + ib)
                    cw |= 1 << (ia * N * N + ib * N + cyc)
        slices.append(sl)
        S = [m.mul(s, g) for s, g in zip(S, step)]
    return cw, slices


def self_test():
    random.seed(2026)
    for N, T, poly, n_rand, all_units in [(7, 1, 0b1011, 5, True), (7, 2, 0b1011, 5, True), (15, 2, 0b10011, 3, False)]:
        m = BCH3DModel(N, T, poly)
        msgs = [0, (1 << m.K) - 1] + [random.getrandbits(m.K) for _ in range(n_rand)]
        if all_units:
            msgs += [1 << b for b in range(m.K)]
        for v in msgs:
            cw, _ = encode_tm(m, v)
            assert cw == m.encode_idffft(v), f"time-multiplexed mismatch N={N} T={T} msg={v:#x}"
        print(f"[PASS] time-multiplexed model N={N:2d} T={T}: {len(msgs)} messages match 3-D IDFFFT")


if __name__ == "__main__":
    self_test()
