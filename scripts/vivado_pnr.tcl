# Vivado OOC synthesis + place & route + analysis for a clocked top (bch3d_enc_tm).
# Usage:
#   vivado -mode batch -nojournal -nolog -source scripts/vivado_pnr.tcl -tclargs <top> <part> <period_ns> <flatten> <out_dir> <v files...>
set top_mod [lindex $argv 0]
set part    [lindex $argv 1]
set period  [lindex $argv 2]
set flat    [lindex $argv 3]
set out_dir [lindex $argv 4]
set v_files [lrange $argv 5 end]
file mkdir $out_dir
set_param general.maxThreads 8
set tag "${top_mod}_${flat}_${period}ns"

foreach vf $v_files { read_verilog $vf }
synth_design -top $top_mod -part $part -mode out_of_context -flatten_hierarchy $flat

create_clock -name clk -period $period [get_ports clk]
set_input_delay  -clock clk [expr {$period * 0.2}] [get_ports -filter {NAME != clk} [all_inputs]]
set_output_delay -clock clk [expr {$period * 0.2}] [all_outputs]

opt_design
place_design
route_design

report_timing_summary -delay_type min_max -max_paths 10 -report_unconstrained -file $out_dir/pnr_timing_$tag.rpt
report_timing -delay_type max -max_paths 3 -sort_by group -path_type full_clock_expanded -file $out_dir/pnr_critical_paths_$tag.rpt
report_utilization -file $out_dir/pnr_util_$tag.rpt
report_utilization -hierarchical -hierarchical_depth 2 -file $out_dir/pnr_util_hier_$tag.rpt
report_high_fanout_nets -timing -load_types -max_nets 25 -file $out_dir/pnr_fanout_$tag.rpt
report_route_status -file $out_dir/pnr_route_status_$tag.rpt
report_power -file $out_dir/pnr_power_$tag.rpt
report_design_analysis -congestion -timing -max_paths 10 -file $out_dir/pnr_design_analysis_$tag.rpt
report_clock_utilization -file $out_dir/pnr_clock_util_$tag.rpt
write_checkpoint -force $out_dir/pnr_$tag.dcp

if {[info exists ::env(WRITE_TIMESIM)] && $::env(WRITE_TIMESIM) eq "1"} {
    write_verilog -mode funcsim -force $out_dir/pnr_funcsim_$tag.v
}
set wns [get_property SLACK [get_timing_paths -max_paths 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -hold]]
puts "PNR_SUMMARY: top=$top_mod flat=$flat period=$period WNS=$wns WHS=$whs"
