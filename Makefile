# ---------------------------------------------------------------------------
# Targets
#   make test              model self-test + converter sim + encoder sim (N=7,T=1 and N=7,T=2)
#   make model-test        python golden model self-test (all four configs, incl. N=15) + time-multiplexed model
#   make conv-test         generate + simulate the msg->sub_msg converter testbench (Verilator)
#   make enc-gen N=15 T=2  generate RTL + testbench into build/ (generator cross-checks vs 3-D IDFFFT)
#   make enc-sim N=7 T=2   generate, then simulate baseline + optimised vs golden (Verilator)
#   make lint / sim        original counter template flow
# ---------------------------------------------------------------------------
PY    ?= python3
N     ?= 7
T     ?= 2
RAND  ?= 20
TAG   := n$(N)_t$(T)
ifeq ($(N)_$(T),15_2)
SFX   :=
else
SFX   := _$(TAG)
endif
BUILD := build
ENC_SRC := rtl/bch3d_msg_to_submsg.v $(BUILD)/rtl/bch3d_enc_baseline$(SFX).v $(BUILD)/rtl/bch3d_enc_opt$(SFX).v
VFLAGS  := --binary --timing -Wno-fatal -Wno-lint -Wno-style

RTL := $(shell find rtl -name '*.v' -o -name '*.sv')
TB  := tb/top_tb.v

.PHONY: test model-test conv-test enc-gen enc-sim lint sim clean

test: model-test conv-test
	$(MAKE) enc-sim N=7 T=1
	$(MAKE) enc-sim N=7 T=2

model-test:
	$(PY) model/bch3d.py
	$(PY) model/bch3d_tm.py
	$(PY) model/bch3d_cse.py

conv-test:
	mkdir -p $(BUILD)
	$(PY) scripts/gen_tb.py $(BUILD)/tb_bch3d_msg_to_submsg.v
	verilator $(VFLAGS) --top-module tb_bch3d_msg_to_submsg -Mdir $(BUILD)/obj_conv -o tb_conv \
	    rtl/bch3d_msg_to_submsg.v $(BUILD)/tb_bch3d_msg_to_submsg.v
	$(BUILD)/obj_conv/tb_conv | tee $(BUILD)/sim_conv.log
	grep -qx "PASS" $(BUILD)/sim_conv.log && ! grep -q "FAIL\|MISMATCH" $(BUILD)/sim_conv.log

enc-gen:
	$(PY) model/gen_rtl.py --n $(N) --t $(T) --rtl-dir $(BUILD)/rtl --tb-dir $(BUILD)/tb \
	    --vec-dir $(BUILD)/vec --num-random $(RAND)

enc-sim: enc-gen
	verilator $(VFLAGS) --top-module tb_bch3d_enc_$(TAG) -Mdir $(BUILD)/obj_$(TAG) -o tb_enc \
	    $(ENC_SRC) $(BUILD)/tb/tb_bch3d_enc_$(TAG).v
	$(BUILD)/obj_$(TAG)/tb_enc | tee $(BUILD)/sim_$(TAG).log
	grep -qx "PASS" $(BUILD)/sim_$(TAG).log && ! grep -q "FAIL\|ERROR" $(BUILD)/sim_$(TAG).log
	$(MAKE) enc-tm-sim N=$(N) T=$(T)

# time-multiplexed encoder (225 trees x 15 cycles for N=15): clocked TB vs golden + bch3d_enc_opt
enc-tm-sim:
	verilator $(VFLAGS) --top-module tb_bch3d_enc_tm_$(TAG) -Mdir $(BUILD)/obj_tm_$(TAG) -o tb_tm \
	    rtl/bch3d_msg_to_submsg.v $(BUILD)/rtl/bch3d_enc_opt$(SFX).v $(BUILD)/rtl/bch3d_enc_tm$(SFX).v \
	    $(BUILD)/tb/tb_bch3d_enc_tm_$(TAG).v
	$(BUILD)/obj_tm_$(TAG)/tb_tm | tee $(BUILD)/sim_tm_$(TAG).log
	grep -qx "PASS" $(BUILD)/sim_tm_$(TAG).log && ! grep -q "FAIL\|ERROR" $(BUILD)/sim_tm_$(TAG).log
	verilator $(VFLAGS) --top-module tb_bch3d_enc_tm_asm_$(TAG) -Mdir $(BUILD)/obj_tm_asm_$(TAG) -o tb_tm_asm \
	    rtl/bch3d_msg_to_submsg.v $(BUILD)/rtl/bch3d_enc_tm$(SFX).v $(BUILD)/rtl/bch3d_enc_tm_asm$(SFX).v \
	    $(BUILD)/tb/tb_bch3d_enc_tm_asm_$(TAG).v
	$(BUILD)/obj_tm_asm_$(TAG)/tb_tm_asm | tee $(BUILD)/sim_tm_asm_$(TAG).log
	grep -qx "PASS" $(BUILD)/sim_tm_asm_$(TAG).log && ! grep -q "FAIL\|ERROR" $(BUILD)/sim_tm_asm_$(TAG).log

lint:
	verilator --lint-only -Wall -Wno-fatal --top-module counter $(RTL)

sim:
	mkdir -p sim
	verilator --binary --timing --trace -Wall -Wno-fatal \
	    --top-module top_tb -Mdir sim/obj_dir -o top_tb $(RTL) $(TB)
	cd sim && ./obj_dir/top_tb | tee sim.log
	@grep -q "PASS" sim/sim.log && ! grep -q "FAIL" sim/sim.log

clean:
	rm -rf sim build
