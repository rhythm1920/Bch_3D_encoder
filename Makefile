RTL := $(wildcard rtl/*.v)
TB  := tb/top_tb.v

lint:
	verilator --lint-only -Wall -Wno-fatal --top-module counter $(RTL)

sim:
	mkdir -p sim
	iverilog -g2012 -Wall -o sim/top_tb.vvp $(RTL) $(TB)
	cd sim && vvp top_tb.vvp | tee sim.log
	@grep -q "PASS" sim/sim.log && ! grep -q "FAIL" sim/sim.log

clean:
	rm -rf sim