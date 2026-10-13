# Every invocation operates on the wrapper's frozen ASCII-path source snapshot.
foreach key {MODEL_GEMM_RUN MODEL_GEMM_TOP MODEL_GEMM_STAGE MODEL_GEMM_DESIGN_FILES MODEL_GEMM_TB} {
    if {![info exists ::env($key)]} {error "$key required"}
}
set run [file normalize $::env(MODEL_GEMM_RUN)]
cd $run
proc mark {stage} {
    set f [open [file join $::run ${stage}_pass.txt] w]
    puts $f PASS
    close $f
}
open_project model_gemm_hls
set_top $::env(MODEL_GEMM_TOP)
foreach f [split $::env(MODEL_GEMM_DESIGN_FILES) "|"] {
    add_files [file join $run source $f] -cflags "-std=c++11 -O3"
}
add_files -tb [file join $run source $::env(MODEL_GEMM_TB)] -cflags "-std=c++11 -O3"
open_solution -flow_target vivado solution1
set_part xck26-sfvc784-2LV-c
create_clock -period 10 -name default
set_clock_uncertainty 1
config_interface -m_axi_addr64=true
config_compile -pipeline_loops $::env(MODEL_GEMM_AUTO_PIPELINE)
set_directive_interface -mode $::env(MODEL_GEMM_CONTROL) $::env(MODEL_GEMM_TOP)
set stage $::env(MODEL_GEMM_STAGE)
if {$stage eq "csim" || $stage eq "all"} {
    if {$::env(MODEL_GEMM_CSIM_PROFILE) eq ""} {csim_design} else {
        csim_design -argv $::env(MODEL_GEMM_CSIM_PROFILE)
    }
    mark csim
}
if {$stage eq "csynth" || $stage eq "all"} {
    csynth_design
    mark csynth
}
if {$stage eq "cosim" || $stage eq "all"} {
    if {![file exists csynth_pass.txt]} {error "Fresh synthesis required"}
    if {$::env(MODEL_GEMM_COSIM_PROFILE) eq ""} {
        cosim_design -rtl verilog -tool xsim
    } else {
        cosim_design -rtl verilog -tool xsim -argv $::env(MODEL_GEMM_COSIM_PROFILE)
    }
    set f [open model_gemm_hls/solution1/sim/report/$::env(MODEL_GEMM_TOP)_cosim.rpt r]
    set report [read $f]
    close $f
    if {![regexp {\|[[:space:]]*Verilog\|[[:space:]]*Pass\|} $report]} {error "RTL Pass missing"}
    mark cosim
}
if {$stage eq "export" || $stage eq "all"} {
    if {![file exists csynth_pass.txt]} {error "Fresh synthesis required"}
    export_design -format ip_catalog -version 1.0.0
    if {![file exists model_gemm_hls/solution1/impl/export.zip]} {error "Export zip absent"}
    mark export
}
close_project
exit 0
