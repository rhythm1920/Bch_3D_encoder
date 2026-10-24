// -----------------------------------------------------------------------------
// bch3d_msg_to_submsg.v  --  PARAMETERISED message -> sub-message converter
// 3-D BCH encoder, baseline architecture, spectral-domain (IDFFFT) encoding.
//
// Parameters
//   N          : code length per dimension, N = 2^M - 1      (default 15)
//   T          : error-correction parameter, null cube is 1..2T per axis (default 2)
//   PRIM_POLY  : primitive polynomial of GF(2^M) incl. x^M term, e.g. x^4+x+1 = 5'b10011
//
// Everything below is derived at elaboration time by constant functions:
//   * conjugate classes = orbits of (j,j',j'') -> (2j,2j',2j'') mod N
//   * insertion point (IP) = smallest linear index j*N*N + j'*N + j'' in the orbit
//   * spectral-null classes = orbits that hit the cube 1<=j,j',j''<=2T  (skipped)
//   * message-carrying classes are taken in ascending IP order, message bits LSB-first
//   * a class of size d holds d bits in the subfield GF(2^d):
//         S = XOR_i  b_i * gamma^i ,  gamma = alpha^(N/(2^d-1))
//     (d = M -> S = bits as-is;  d = 1 -> S = {0..0,b0};
//      N=15,d=2 -> S = b0 + b1*alpha^5)
//
// Ports (widths for the default N=15,T=2: K_BITS=3163, N_CLASS=798, M=4)
//   msg      [K_BITS-1 : 0]        input message
//   sub_msg  [M*N_CLASS-1 : 0]     sub_msg[M*k +: M] = S_cc^IP of the k-th message class
// Use the localparam formulas below (or the functions in the TB) to size your wires.
// Pure wiring plus small XOR fan-in for classes with 1 < size < M.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
module bch3d_msg_to_submsg #(
    parameter N         = 15,
    parameter T         = 2,
    parameter PRIM_POLY = 5'b10011
)(
    msg,
    sub_msg
);
    // ---------------------------------------------------------------- field size
    function integer log2_np1;           // M such that 2^M = N+1
        input integer n;
        integer v;
        begin
            log2_np1 = 0; v = n + 1;
            while (v > 1) begin v = v >> 1; log2_np1 = log2_np1 + 1; end
        end
    endfunction
    localparam M = log2_np1(N);
    localparam NNN = N*N*N;

    // ---------------------------------------------------------------- field helpers
    function integer alpha_pow;          // alpha^e in polynomial basis
        input integer e;
        integer x, i;
        begin
            x = 1;
            for (i = 0; i < e; i = i + 1) begin
                x = x << 1;
                if ((x >> M) & 1) x = x ^ PRIM_POLY;
            end
            alpha_pow = x;
        end
    endfunction

    // ---------------------------------------------------------------- class helpers
    function integer dbl;                // 2*x mod N
        input integer x;
        begin dbl = (2*x) % N; end
    endfunction

    function integer next_pt;            // Frobenius step on linear index
        input integer p;
        integer a, b, c;
        begin
            a = p / (N*N); b = (p / N) % N; c = p % N;
            next_pt = dbl(a)*N*N + dbl(b)*N + dbl(c);
        end
    endfunction

    function integer in_null_cube;
        input integer p;
        integer a, b, c;
        begin
            a = p / (N*N); b = (p / N) % N; c = p % N;
            in_null_cube = (a>=1 && a<=2*T && b>=1 && b<=2*T && c>=1 && c<=2*T);
        end
    endfunction

    function integer orbit_size;
        input integer p;
        integer q;
        begin
            orbit_size = 1; q = next_pt(p);
            while (q != p) begin orbit_size = orbit_size + 1; q = next_pt(q); end
        end
    endfunction

    function integer is_rep;             // p is the smallest member of its orbit
        input integer p;
        integer q;
        begin
            is_rep = 1; q = next_pt(p);
            while (q != p) begin
                if (q < p) is_rep = 0;
                q = next_pt(q);
            end
        end
    endfunction

    function integer orbit_is_null;
        input integer p;
        integer q;
        begin
            orbit_is_null = in_null_cube(p); q = next_pt(p);
            while (q != p) begin
                if (in_null_cube(q)) orbit_is_null = 1;
                q = next_pt(q);
            end
        end
    endfunction

    function integer count_classes;      // message-carrying classes
        input integer dummy;
        integer p;
        begin
            count_classes = 0;
            for (p = 0; p < NNN; p = p + 1)
                if (is_rep(p) && !orbit_is_null(p)) count_classes = count_classes + 1;
        end
    endfunction

    function integer count_bits;         // total message bits K
        input integer dummy;
        integer p;
        begin
            count_bits = 0;
            for (p = 0; p < NNN; p = p + 1)
                if (is_rep(p) && !orbit_is_null(p)) count_bits = count_bits + orbit_size(p);
        end
    endfunction

    // table: per message class k, bits [16k*2 +: 16] = bit offset, [16k*2+16 +: 16] = size
    localparam N_CLASS = count_classes(0);
    localparam K_BITS  = count_bits(0);

    function [32*NNN-1:0] build_table;
        input integer dummy;
        integer p, k, off, sz;
        reg [32*NNN-1:0] t;
        begin
            t = 0; k = 0; off = 0;
            for (p = 0; p < NNN; p = p + 1)
                if (is_rep(p) && !orbit_is_null(p)) begin
                    sz = orbit_size(p);
                    t[32*k      +: 16] = off;
                    t[32*k + 16 +: 16] = sz;
                    off = off + sz; k = k + 1;
                end
            build_table = t;
        end
    endfunction
    localparam [32*NNN-1:0] TABLE = build_table(0);

    // mask for output bit r of a size-d class: bit i set if bit r of gamma^i is 1
    function integer sub_mask;
        input integer r;
        input integer d;
        integer i, g;
        begin
            sub_mask = 0;
            for (i = 0; i < d; i = i + 1) begin
                g = alpha_pow(i * (N / ((1 << d) - 1)));
                if ((g >> r) & 1) sub_mask = sub_mask | (1 << i);
            end
        end
    endfunction

    // ---------------------------------------------------------------- ports
    input  wire [K_BITS-1:0]     msg;
    output wire [M*N_CLASS-1:0]  sub_msg;

    // ---------------------------------------------------------------- wiring
    genvar k, r;
    generate
        for (k = 0; k < N_CLASS; k = k + 1) begin : g_cls
            localparam integer OFF = TABLE[32*k      +: 16];
            localparam integer SZ  = TABLE[32*k + 16 +: 16];
            wire [31:0] bits = msg[OFF +: SZ];           // zero-extended
            for (r = 0; r < M; r = r + 1) begin : g_bit
                localparam [31:0] MASK = sub_mask(r, SZ);
                assign sub_msg[M*k + r] = ^(bits & MASK);
            end
        end
    endgenerate
endmodule
