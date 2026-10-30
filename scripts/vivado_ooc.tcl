# Vivado Out-Of-Context (OOC) Synthesis Script for 3-D BCH Encoder
# Usage:
#   vivado -mode batch -nojournal -nolog -source scripts/vivado_ooc.tcl -tclargs <top_module> <part> <flatten_mode> <out_dir> <verilog_file1> [<verilog_file2> ...]
# where <flatten_mode> is one of: rebuilt, none, full

set top_mod      [lindex $argv 0]
set part_name    [lindex $argv 1]
set flatten_mode [lindex $argv 2]
set out_dir      [lindex $argv 3]
set v_files      [lrange $argv 4 end]

file mkdir $out_dir
set_param general.maxThreads 8

puts "INFO: Reading Verilog sources for top=$top_mod part=$part_name flatten=$flatten_mode"
foreach vf $v_files {
    read_verilog $vf
}

set t0 [clock seconds]
synth_design -top $top_mod -part $part_name -mode out_of_context -flatten_hierarchy $flatten_mode
set t1 [clock seconds]

# Optional timing run for clocked tops (e.g. bch3d_enc_tm): CLOCK_PERIOD_NS=<ns> in the environment.
# Post-synthesis (pre-route) estimate only.
if {[info exists ::env(CLOCK_PERIOD_NS)] && [llength [get_ports -quiet clk]] > 0} {
    create_clock -name clk -period $::env(CLOCK_PERIOD_NS) [get_ports clk]
    report_timing_summary -delay_type max -max_paths 5 -file "$out_dir/timing_${top_mod}_${flatten_mode}.rpt"
    puts "INFO: timing report written to $out_dir/timing_${top_mod}_${flatten_mode}.rpt"
}
puts [format "INFO: synth_design completed in %d seconds" [expr {$t1 - $t0}]]

report_utilization -file "$out_dir/utilization_${top_mod}_${flatten_mode}.rpt"
report_utilization -hierarchical -hierarchical_depth 2 -file "$out_dir/utilization_hier_${top_mod}_${flatten_mode}.rpt"

# Extract summary LUT counts to stdout
set lut_total [get_property PRIMITIVE_COUNT [get_design]]
set luts [llength [get_cells -hierarchical -filter {PRIMITIVE_GROUP == LUT || REF_NAME =~ LUT*}]]
puts "SUMMARY: top=$top_mod flatten=$flatten_mode LUT_CELLS=$luts total_primitives=$lut_total elapsed_sec=[expr {$t1 - $t0}]"

# Write post-synthesis functional simulation netlist if requested via env or for smaller designs
if {[info exists ::env(WRITE_FUNCSIM)] && $::env(WRITE_FUNCSIM) eq "1"} {
    write_verilog -mode funcsim -force "$out_dir/${top_mod}_${flatten_mode}_funcsim.v"
}
