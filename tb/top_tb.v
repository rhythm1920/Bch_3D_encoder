`timescale 1ns/1ps
module top_tb;
  reg clk, rst;
  wire [3:0] q;
  integer errors;

  counter dut (.clk(clk), .rst(rst), .q(q));

  initial clk = 0;
  always #5 clk = ~clk;

  initial begin
    errors = 0;
    rst = 1;
    $dumpfile("dump.vcd");
    $dumpvars(0, top_tb);

    repeat (2) @(posedge clk);
    #1 rst = 0;

    repeat (5) @(posedge clk);
    #1;
    if (q !== 4'd5) begin
      $display("ERROR: expected 5, got %0d", q);
      errors = errors + 1;
    end

    if (errors == 0) $display("PASS");
    else             $display("FAIL");
    $finish;
  end
endmodule
