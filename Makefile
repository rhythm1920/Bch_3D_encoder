RTL := $(shell find rtl -name '*.v' -o -name '*.sv')
TB  := tb/top_tb.v

lint:
	verilator --lint-only -Wall -Wno-fatal --top-module counter $(RTL)

sim:
	mkdir -p sim
	verilator --binary --timing --trace -Wall -Wno-fatal \
	    --top-module top_tb -Mdir sim/obj_dir -o top_tb $(RTL) $(TB)
	cd sim && ./obj_dir/top_tb | tee sim.log
	@grep -q "PASS" sim/sim.log && ! grep -q "FAIL" sim/sim.log

clean:
	rm -rf sim