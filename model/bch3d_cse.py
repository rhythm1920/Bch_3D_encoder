#!/usr/bin/env python3
"""
Structured XOR sharing for the 3-D BCH encoder (fully parallel, 1 codeword/cycle).

Codeword bit at p = (i,i',i'') is  XOR_k  w[k, e_k(p)],  e_k(p) = -(p . J_k) mod N,
where w[k,e] = Tr_d(S_k * alpha^e) are the multiplier-bank wires.

For N = 15 = 3*5, Z_15^3 = Z_3^3 x Z_5^3. Take a normal vector n in Z_15^3 of order 15. Every class
whose representative J satisfies J.n = 0 (mod 15) lies in the plane n-perp. For such a group G the
partial sum  V_G(p) = XOR_{k in G} w[k, e_k(p)]  depends only on p modulo the line <n> (225 values,
not 3375), because e_k(p + m*n) = e_k(p) - m*(J_k . n) = e_k(p).
So each group costs 225 x (|G|-1) XOR2, and each output XORs one value per group.

This script builds a greedy plane cover of the message classes, checks the result against the
independent 3-D IDFFFT, and prints XOR2 / LUT6 estimates versus the per-point trees.
"""
import os
import random
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bch3d import BCH3DModel


def normals_n15():
    """One normal per (projective point of F3^3) x (projective point of F5^3) -> order-15 vectors mod 15."""
    def proj(p):
        pts = set()
        for v in product(range(p), repeat=3):
            if any(v):
                inv = next(s for s in range(1, p) if (s * next(x for x in v if x)) % p == 1)
                pts.add(tuple((inv * x) % p for x in v))
        return sorted(pts)
    out = []
    for a in proj(3):
        for b in proj(5):
            # CRT: n = x mod 15 with x = a mod 3, x = b mod 5
            n = tuple(next(x for x in range(15) if x % 3 == a[t] and x % 5 == b[t]) for t in range(3))
            out.append(n)
    return out


def build_groups(m: BCH3DModel):
    assert m.N == 15
    classes = m.classes
    normals = normals_n15()
    members = {n: {c.idx for c in classes if sum(a * b for a, b in zip(c.rep, n)) % 15 == 0} for n in normals}
    unc = {c.idx for c in classes}
    groups = []
    while unc:
        n = max(normals, key=lambda x: len(members[x] & unc))
        g = sorted(members[n] & unc)
        groups.append((n, g))
        unc -= set(g)
    return groups


def key_of(p, n):
    """Canonical representative of p modulo the line <n> (15 elements)."""
    return min(tuple((p[t] + mm * n[t]) % 15 for t in range(3)) for mm in range(15))


def encode_cse(m: BCH3DModel, msg: int, groups):
    N = m.N
    S = m.msg_to_submsg(msg)
    pts = [m.lin_to_pt(p) for p in range(m.NNN)]
    cw = 0
    gvals = []
    for n, g in groups:
        keys = {}
        for p in pts:
            keys.setdefault(key_of(p, n), p)
        vals = {}
        for kk, p in keys.items():
            bit = 0
            for ci in g:
                c = m.classes[ci]
                e = (-(p[0] * c.rep[0] + p[1] * c.rep[1] + p[2] * c.rep[2])) % N
                bit ^= bin(S[ci] & c.trace_masks[e]).count("1") & 1
            vals[kk] = bit
        gvals.append((n, vals))
    for idx, p in enumerate(pts):
        bit = 0
        for n, vals in gvals:
            bit ^= vals[key_of(p, n)]
        if bit:
            cw |= 1 << idx
    return cw


def cost(m, groups):
    ceil5 = lambda x: -(-x // 5)
    nk = [len(g) for _, g in groups]
    # distinct values per group = 3375 / |<n>| = 225 (every normal here has order 15)
    xor2_l1 = sum(225 * (n - 1) for n in nk)
    xor2_l2 = m.NNN * (len(groups) - 1)
    lut_l1 = sum(225 * ceil5(n - 1) for n in nk)
    lut_l2 = m.NNN * ceil5(len(groups) - 1)
    return nk, xor2_l1, xor2_l2, lut_l1, lut_l2


def main():
    random.seed(99)
    m = BCH3DModel(15, 2, 0b10011)
    groups = build_groups(m)
    nk, x1, x2, l1, l2 = cost(m, groups)
    print(f"groups G={len(groups)} sizes={nk} (sum={sum(nk)}, classes={m.N_CLASS})")
    base_xor2 = m.NNN * (m.N_CLASS - 1)
    print(f"per-point trees : {base_xor2:,} XOR2  -> 540,000 LUT6 (measured)")
    print(f"grouped sharing : level1 {x1:,} + level2 {x2:,} = {x1 + x2:,} XOR2 "
          f"({100 * (1 - (x1 + x2) / base_xor2):.1f}% fewer)")
    print(f"LUT6 estimate   : level1 {l1:,} + level2 {l2:,} = {l1 + l2:,} (+ bank ~4,597)")
    for v in [0, (1 << m.K) - 1] + [random.getrandbits(m.K) for _ in range(3)] + [1 << random.randrange(m.K) for _ in range(3)]:
        assert encode_cse(m, v, groups) == m.encode_idffft(v), "grouped sharing mismatch"
    print("[PASS] grouped-sharing model matches 3-D IDFFFT on 8 messages")


if __name__ == "__main__":
    main()
