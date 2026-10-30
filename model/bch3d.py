#!/usr/bin/env python3
"""
Parameterised 3-D BCH spectral-domain golden model (GF(2^M), N = 2^M - 1).

Provides two mathematically independent encoding paths:
  1. Separable 3-D IDFFFT over GF(2^M) after populating the full conjugate spectrum.
  2. Closed-form per-class trace / MSB evaluation at each output point (i, i', i'').

Also computes computation-class partition (distinct twiddle exponents per conjugate class)
and the exact binary generator matrix for both baseline and optimised hardware generation.
"""
from dataclasses import dataclass
from typing import List, Tuple, Dict
import random
import time


@dataclass
class ClassInfo:
    idx: int                      # 0 .. N_CLASS-1 (ascending IP linear index)
    rep: Tuple[int, int, int]     # Insertion point (J, J', J'')
    rep_lin: int                  # J*N*N + J'*N + J''
    orbit: List[Tuple[int, int, int]]
    size: int                     # d in {1, 2, 4} (or divisor of M)
    bit_offset: int               # Starting bit index in msg[K-1:0]
    sub_masks: List[int]          # Length M: bitmask of msg[bit_offset +: size] for sub_msg bit r
    trace_masks: Dict[int, int]   # exp (0..N-1) -> M-bit mask on S_k producing Tr_d(S_k * alpha^exp)
    distinct_exps: List[int]      # Sorted list of distinct exponents e = (-(i.J)) mod N across all points


class BCH3DModel:
    def __init__(self, N: int = 15, T: int = 2, prim_poly: int = 0b10011):
        self.N = N
        self.T = T
        self.prim_poly = prim_poly
        self.M = (N + 1).bit_length() - 1
        assert (1 << self.M) - 1 == N, f"N={N} must be 2^M - 1"
        self.NNN = N * N * N

        # Build GF(2^M) exp/log tables
        self.exp = [0] * (2 * N + 2)
        self.log = [0] * (N + 1)
        x = 1
        for e in range(N):
            self.exp[e] = x
            self.exp[e + N] = x
            self.log[x] = e
            x <<= 1
            if (x >> self.M) & 1:
                x ^= prim_poly

        # Build conjugate classes (matching bch3d_msg_to_submsg.v ordering)
        self.classes: List[ClassInfo] = []
        self.null_classes: List[List[Tuple[int, int, int]]] = []
        self._build_classes()
        self.K = sum(c.size for c in self.classes)
        self.N_CLASS = len(self.classes)

    def mul(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp[self.log[a] + self.log[b]]

    def sq(self, a: int) -> int:
        return 0 if a == 0 else self.exp[2 * self.log[a]]

    def trace_d(self, a: int, d: int) -> int:
        """Sum_{r=0}^{d-1} a^(2^r) in GF(2^M). For a in GF(2^d)*coset, result is in {0, 1}."""
        acc = 0
        y = a
        for _ in range(d):
            acc ^= y
            y = self.sq(y)
        assert acc in (0, 1), f"trace_d({a}, {d}) = {acc} not in GF(2)"
        return acc

    def lin_to_pt(self, p: int) -> Tuple[int, int, int]:
        N = self.N
        return (p // (N * N), (p // N) % N, p % N)

    def pt_to_lin(self, pt: Tuple[int, int, int]) -> int:
        N = self.N
        return pt[0] * N * N + pt[1] * N + pt[2]

    def _in_null_cube(self, pt: Tuple[int, int, int]) -> bool:
        return all(1 <= v <= 2 * self.T for v in pt)

    def _build_classes(self):
        N, M = self.N, self.M
        nxt = lambda pt: ((2 * pt[0]) % N, (2 * pt[1]) % N, (2 * pt[2]) % N)
        off = 0
        idx = 0
        all_pts = [self.lin_to_pt(p) for p in range(self.NNN)]

        for p in range(self.NNN):
            pt = all_pts[p]
            orbit = [pt]
            q = nxt(pt)
            while q != pt:
                orbit.append(q)
                q = nxt(q)
            min_lin = min(self.pt_to_lin(x) for x in orbit)
            if min_lin != p:
                continue
            if any(self._in_null_cube(x) for x in orbit):
                self.null_classes.append(orbit)
                continue

            d = len(orbit)
            step = N // ((1 << d) - 1)
            sub_masks = []
            for r in range(M):
                mask = 0
                for i in range(d):
                    g = self.exp[(i * step) % N]
                    if (g >> r) & 1:
                        mask |= (1 << i)
                sub_masks.append(mask)

            # Distinct twiddle exponents e = (-(i.J)) mod N across all N^3 output points
            exps_set = set()
            for ia, ib, ic in all_pts:
                e = (-(ia * pt[0] + ib * pt[1] + ic * pt[2])) % N
                exps_set.add(e)
            distinct_exps = sorted(exps_set)

            # trace_masks[e]: M-bit mask m such that parity(S_k & m) == Tr_d(S_k * alpha^e)
            # for every S_k in GF(2^d). Bit 0 of sum_{u<d} (alpha^r * alpha^e)^(2^u) is the
            # contribution of basis bit r; the higher bits cancel once S_k lies in GF(2^d).
            trace_masks: Dict[int, int] = {}
            for e in distinct_exps:
                tw = self.exp[e]
                m_mask = 0
                for r in range(M):
                    # Contribution of S_k bit r to sum_{u=0}^{d-1} ((alpha^r * tw)^(2^u)) bit 0:
                    elem = self.mul(1 << r, tw)
                    acc = 0
                    y = elem
                    for _ in range(d):
                        acc ^= y
                        y = self.sq(y)
                    if acc & 1:
                        m_mask |= (1 << r)
                trace_masks[e] = m_mask

            self.classes.append(
                ClassInfo(
                    idx=idx,
                    rep=pt,
                    rep_lin=p,
                    orbit=orbit,
                    size=d,
                    bit_offset=off,
                    sub_masks=sub_masks,
                    trace_masks=trace_masks,
                    distinct_exps=distinct_exps,
                )
            )
            off += d
            idx += 1

    def msg_to_submsg(self, msg_int: int) -> List[int]:
        """Convert K-bit integer message into list of N_CLASS M-bit sub-messages (matches bch3d_msg_to_submsg.v)."""
        subs = []
        for c in self.classes:
            bits = (msg_int >> c.bit_offset) & ((1 << c.size) - 1)
            s = 0
            for r in range(self.M):
                if bin(bits & c.sub_masks[r]).count("1") & 1:
                    s |= (1 << r)
            subs.append(s)
        return subs

    def encode_idffft(self, msg_int: int) -> int:
        """
        Golden reference path 1: Full 3-D spectrum + separable 3-D IDFFFT.
        Returns N^3-bit integer codeword where bit p corresponds to linear point p = i*N*N + i'*N + i''.
        """
        N = self.N
        subs = self.msg_to_submsg(msg_int)
        spec = [[[0] * N for _ in range(N)] for _ in range(N)]
        for c, s in zip(self.classes, subs):
            v = s
            for (ja, jb, jc) in c.orbit:
                spec[ja][jb][jc] = v
                v = self.sq(v)

        def idft1d(vec):
            out = [0] * N
            for i in range(N):
                acc = 0
                for j in range(N):
                    if vec[j]:
                        acc ^= self.mul(vec[j], self.exp[(-i * j) % N])
                out[i] = acc
            return out

        A = spec
        for a in range(N):
            for b in range(N):
                A[a][b] = idft1d(A[a][b])
        for a in range(N):
            for c_idx in range(N):
                col = idft1d([A[a][b][c_idx] for b in range(N)])
                for b in range(N):
                    A[a][b][c_idx] = col[b]
        for b in range(N):
            for c_idx in range(N):
                col = idft1d([A[a][b][c_idx] for a in range(N)])
                for a in range(N):
                    A[a][b][c_idx] = col[a]

        cw = 0
        for p in range(self.NNN):
            ia, ib, ic = self.lin_to_pt(p)
            val = A[ia][ib][ic]
            assert val in (0, 1), f"Non-binary IDFFFT output {val} at {(ia, ib, ic)}"
            if val:
                cw |= (1 << p)
        return cw

    def encode_trace(self, msg_int: int) -> int:
        """
        Golden reference path 2: Direct hardware trace/conjugate-block model using sub_msg and trace_masks.
        """
        N = self.N
        subs = self.msg_to_submsg(msg_int)
        cw = 0
        for p in range(self.NNN):
            ia, ib, ic = self.lin_to_pt(p)
            bit = 0
            for c, s in zip(self.classes, subs):
                e = (-(ia * c.rep[0] + ib * c.rep[1] + ic * c.rep[2])) % N
                mask = c.trace_masks[e]
                if bin(s & mask).count("1") & 1:
                    bit ^= 1
            if bit:
                cw |= (1 << p)
        return cw

    def decode_dffft_check_nulls(self, cw_int: int) -> bool:
        """Apply forward 3-D DFFFT to a binary codeword and verify the 2T x 2T x 2T spectral-null cube is all zero."""
        N = self.N
        B = [[[0] * N for _ in range(N)] for _ in range(N)]
        for p in range(self.NNN):
            ia, ib, ic = self.lin_to_pt(p)
            B[ia][ib][ic] = (cw_int >> p) & 1

        def dft1d(vec):
            out = [0] * N
            for j in range(N):
                acc = 0
                for i in range(N):
                    if vec[i]:
                        acc ^= self.mul(vec[i], self.exp[(i * j) % N])
                out[j] = acc
            return out

        for a in range(N):
            for b in range(N):
                B[a][b] = dft1d(B[a][b])
        for a in range(N):
            for c_idx in range(N):
                col = dft1d([B[a][b][c_idx] for b in range(N)])
                for b in range(N):
                    B[a][b][c_idx] = col[b]
        for b in range(N):
            for c_idx in range(N):
                col = dft1d([B[a][b][c_idx] for a in range(N)])
                for a in range(N):
                    B[a][b][c_idx] = col[a]

        for p in range(self.NNN):
            pt = self.lin_to_pt(p)
            if self._in_null_cube(pt) and B[pt[0]][pt[1]][pt[2]] != 0:
                return False
        return True


def self_test():
    random.seed(42)
    for N, T, poly in [(7, 1, 0b1011), (7, 2, 0b1011), (15, 1, 0b10011), (15, 2, 0b10011)]:
        t0 = time.time()
        m = BCH3DModel(N, T, poly)
        comp_hist = {}
        for c in m.classes:
            comp_hist[len(c.distinct_exps)] = comp_hist.get(len(c.distinct_exps), 0) + 1
        for v in [0, (1 << m.K) - 1, 1, 1 << (m.K - 1), random.getrandbits(m.K), random.getrandbits(m.K)]:
            cw1 = m.encode_idffft(v)
            cw2 = m.encode_trace(v)
            assert cw1 == cw2, f"Mismatch between IDFFFT and trace formula for N={N}, T={T}"
            assert m.decode_dffft_check_nulls(cw1), f"Null cube check failed for N={N}, T={T}"
        if (N, T) == (15, 2):
            assert m.K == 3163 and m.N_CLASS == 798 and len(m.null_classes) == 53
            assert comp_hist == {15: 754, 5: 30, 3: 13, 1: 1}, f"Unexpected comp_hist: {comp_hist}"
        print(
            f"[PASS] N={N:2d} T={T} -> M={m.M} K={m.K:4d} msg_classes={m.N_CLASS:3d} "
            f"null_classes={len(m.null_classes):2d} comp_classes={comp_hist} ({time.time()-t0:.2f}s)"
        )


if __name__ == "__main__":
    self_test()
