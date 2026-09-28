# F8 platform only. Board loading is a separate, explicitly reviewed operation.
foreach key {F8_RUN_DIR F8_BOARD_REPO F8_IP_DIR F8_SOURCE_DIR F8_TAG0 F8_TAG1 F8_BUILD} {
    if {![info exists ::env($key)] || $::env($key) eq ""} {error "$key is required"}
}
if {[version -short] ne "2020.2.2"} {error "Validated Vivado 2020.2.2 is required"}
set run_dir [file normalize $::env(F8_RUN_DIR)]
set source_dir [file normalize $::env(F8_SOURCE_DIR)]
set part xck26-sfvc784-2LV-c
set_param board.repoPaths [list [file normalize $::env(F8_BOARD_REPO)]]
proc mark {name value} {
    set f [open [file join $::run_dir $name] w]; puts $f $value; close $f
}
cd $run_dir
create_project status_sim [file join $run_dir status_sim] -part $part
add_files [file join $source_dir f8_status.v]
add_files -fileset sim_1 [file join $source_dir tb_f8_status.sv]
set_property top tb_f8_status [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
launch_simulation
close_sim
set log [file join $run_dir status_sim status_sim.sim sim_1 behav xsim simulate.log]
set f [open $log r]; set body [read $f]; close $f
if {[string first "F8_STATUS_RTL_PASS" $body] < 0 || [regexp -nocase {fatal:|error:} $body]} {
    error "Status RTL simulation failed"
}
mark status_rtl_pass.txt "F8_STATUS_RTL_PASS"
close_project

create_project f8_kv260 [file join $run_dir project] -part $part
set_property board_part xilinx.com:kv260:part0:1.1 [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list [file normalize $::env(F8_IP_DIR)]] [current_project]
update_ip_catalog
add_files [file join $source_dir f8_status.v]
update_compile_order -fileset sources_1
create_bd_design kv260_f8
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__USE__M_AXI_GP1 {0} CONFIG.PSU__USE__M_AXI_GP2 {0} \
    CONFIG.PSU__MAXIGP0__DATA_WIDTH {128} \
    CONFIG.PSU__USE__S_AXI_GP0 {0} CONFIG.PSU__USE__S_AXI_GP1 {0} CONFIG.PSU__USE__S_AXI_GP2 {1} \
    CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} \
    CONFIG.PSU__USE__S_AXI_GP3 {0} CONFIG.PSU__USE__S_AXI_GP4 {0} CONFIG.PSU__USE__S_AXI_GP5 {0} CONFIG.PSU__USE__S_AXI_GP6 {0} \
    CONFIG.PSU__USE__IRQ0 {0} CONFIG.PSU__USE__IRQ1 {0} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100}] $ps
create_bd_cell -type ip -vlnv xilinx.com:hls:gemm_top:1.0 gemm
set status [create_bd_cell -type module -reference f8_status status]
set_property -dict [list CONFIG.BUILD_TAG0 [expr {wide($::env(F8_TAG0))}] CONFIG.BUILD_TAG1 [expr {wide($::env(F8_TAG1))}]] $status
set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect ctrl]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $ctrl
set data [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect data]
set_property -dict [list CONFIG.NUM_SI {3} CONFIG.NUM_MI {1}] $data
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset reset]
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl/M00_AXI] [get_bd_intf_pins gemm/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ctrl/M01_AXI] [get_bd_intf_pins status/S_AXI]
foreach {index prefix} {0 x 1 w 2 y} {
    connect_bd_intf_net [get_bd_intf_pins gemm/m_axi_gmem$index] [get_bd_intf_pins data/S0${index}_AXI]
    set signals [expr {$index < 2 ? {arvalid arready rvalid rready rlast rresp} : {awvalid awready wvalid wready bvalid bready bresp}}]
    foreach signal $signals {
        # A scalar net overrides its interface member in IPI. Include BOTH
        # original endpoints when adding the passive third observer.
        set master_pin [get_bd_pins gemm/m_axi_gmem${index}_[string toupper $signal]]
        set fabric_pin [get_bd_pins data/S0${index}_AXI_${signal}]
        set monitor_pin [get_bd_pins status/${prefix}_${signal}]
        if {[llength $fabric_pin] != 1} {error "Missing SmartConnect pin for $index/$signal"}
        connect_bd_net $master_pin $fabric_pin $monitor_pin
        set attached [get_bd_pins -of_objects [get_bd_nets -of_objects $monitor_pin]]
        if {[llength $attached] != 3 || [llength [filter $attached {DIR == O}]] != 1} {
            error "Invalid three-endpoint AXI observation net: $attached"
        }
    }
}
connect_bd_intf_net [get_bd_intf_pins data/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins {ps/maxihpm0_fpd_aclk ps/saxihp0_fpd_aclk ctrl/aclk data/aclk gemm/ap_clk status/aclk reset/slowest_sync_clk}]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins reset/ext_reset_in]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins reset/dcm_locked]
connect_bd_net [get_bd_pins reset/peripheral_aresetn] [get_bd_pins {ctrl/aresetn data/aresetn gemm/ap_rst_n status/aresetn}]
assign_bd_address -offset 0xA0000000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs gemm/s_axi_control/Reg]
set status_segs [get_bd_addr_segs -of_objects [get_bd_intf_pins status/S_AXI]]
if {[llength $status_segs] != 1} {error "Expected one status register segment: $status_segs"}
assign_bd_address -offset 0xA0010000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps/Data] $status_segs
foreach index {0 1 2} {
    set space [get_bd_addr_spaces gemm/Data_m_axi_gmem$index]
    assign_bd_address -offset 0 -range 0x80000000 -target_address_space $space [get_bd_addr_segs ps/SAXIGP2/HP0_DDR_LOW]
    assign_bd_address -target_address_space $space [get_bd_addr_segs ps/SAXIGP2/HP0_DDR_HIGH]
}
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH $rst] ne "0"} {error "Unexpected reset polarity"}
save_bd_design
write_bd_tcl [file join $run_dir generated_bd.tcl]
report_property -all $ps -file [file join $run_dir ps_properties.txt]
set f [open [file join $run_dir address_map.txt] w]
foreach space [get_bd_addr_spaces] {
    puts $f "SPACE $space"
    foreach seg [get_bd_addr_segs -of_objects $space] {puts $f "$seg OFFSET=[get_property OFFSET $seg] RANGE=[get_property RANGE $seg]"}
}
close $f
set bd [get_files kv260_f8.bd]
generate_target all $bd
add_files -norecurse [make_wrapper -files $bd -top]
set_property top kv260_f8_wrapper [current_fileset]
update_compile_order -fileset sources_1
mark bd_pass.txt "F8_BD_PASS"
if {$::env(F8_BUILD) eq "1"} {
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis incomplete"}
    set f [open [file join $run_dir project f8_kv260.runs synth_1 runme.log] r]
    set synthesis_log [read $f]; close $f
    if {[regexp {CRITICAL WARNING:.*unconnected pin} $synthesis_log]} {error "Unconnected synthesis pin"}
    set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation incomplete"}
    open_run impl_1
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $run_dir timing_summary.rpt]
    report_drc -file [file join $run_dir drc.rpt]
    report_utilization -file [file join $run_dir utilization.rpt]
    report_route_status -file [file join $run_dir route_status.rpt]
    set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
    set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
    if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {error "Timing failed: $setup / $hold"}
    if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]} {error "DRC errors remain"}
    file mkdir [file join $run_dir artifacts]
    foreach ext {bit bin} {
        file copy [file join $run_dir project f8_kv260.runs impl_1 kv260_f8_wrapper.$ext] [file join $run_dir artifacts kv260_f8.$ext]
    }
    set hwh [file join $run_dir project f8_kv260.gen sources_1 bd kv260_f8 hw_handoff kv260_f8.hwh]
    if {![file exists $hwh]} {set hwh [file join $run_dir project f8_kv260.srcs sources_1 bd kv260_f8 hw_handoff kv260_f8.hwh]}
    file copy $hwh [file join $run_dir artifacts kv260_f8.hwh]
    write_hw_platform -fixed -include_bit [file join $run_dir artifacts kv260_f8.xsa]
    mark build_pass.txt "F8_BUILD_PASS setup_slack_ns=$setup hold_slack_ns=$hold"
}
close_project
exit 0
