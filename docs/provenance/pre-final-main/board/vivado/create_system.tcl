# New full-layer platform; the frozen A1 RTL is imported without edits.
foreach k {MG_RUN MG_BOARD MG_IP MG_OBSERVER MG_TAG0 MG_TAG1 MG_BUILD} {
    if {![info exists ::env($k)]} {error "$k missing"}
}
if {[version -short] ne "2020.2.2"} {error "Vivado 2020.2.2 required"}
set run [file normalize $::env(MG_RUN)]
set part xck26-sfvc784-2LV-c
proc mark {name value} {set f [open [file join $::run $name] w]; puts $f $value; close $f}
set_param board.repoPaths [list [file normalize $::env(MG_BOARD)]]
cd $run
create_project observer_sim [file join $run observer_sim] -part $part
add_files [file join $::env(MG_OBSERVER) f8_status.v]
add_files -fileset sim_1 [file join $::env(MG_OBSERVER) tb_f8_status.sv]
set_property top tb_f8_status [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
launch_simulation
close_sim
set f [open [file join $run observer_sim observer_sim.sim sim_1 behav xsim simulate.log] r]
set text [read $f]; close $f
if {[string first "F8_STATUS_RTL_PASS" $text]<0 || [regexp -nocase {fatal:|error:} $text]} {error "Observer RTL failed"}
mark observer_pass.txt PASS
close_project
create_project model_kv260 [file join $run project] -part $part
set_property board_part xilinx.com:kv260:part0:1.1 [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list [file normalize $::env(MG_IP)]] [current_project]
update_ip_catalog
add_files [file join $::env(MG_OBSERVER) f8_status.v]
update_compile_order -fileset sources_1
create_bd_design kv260_model
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps
set_property -dict [list CONFIG.PSU__USE__M_AXI_GP0 1 CONFIG.PSU__USE__M_AXI_GP1 0 CONFIG.PSU__USE__M_AXI_GP2 0 CONFIG.PSU__MAXIGP0__DATA_WIDTH 128 CONFIG.PSU__USE__S_AXI_GP0 0 CONFIG.PSU__USE__S_AXI_GP1 0 CONFIG.PSU__USE__S_AXI_GP2 1 CONFIG.PSU__SAXIGP2__DATA_WIDTH 128 CONFIG.PSU__USE__S_AXI_GP3 0 CONFIG.PSU__USE__S_AXI_GP4 0 CONFIG.PSU__USE__S_AXI_GP5 0 CONFIG.PSU__USE__S_AXI_GP6 0 CONFIG.PSU__USE__IRQ0 0 CONFIG.PSU__USE__IRQ1 0 CONFIG.PSU__FPGA_PL0_ENABLE 1 CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ 100] $ps
create_bd_cell -type ip -vlnv xilinx.com:hls:model_gemm_top:1.0 gemm
set obs [create_bd_cell -type module -reference f8_status status]
set_property -dict [list CONFIG.BUILD_TAG0 [expr {wide($::env(MG_TAG0))}] CONFIG.BUILD_TAG1 [expr {wide($::env(MG_TAG1))}]] $obs
set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect ctrl]
set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 2] $ctrl
set data [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect data]
set_property -dict [list CONFIG.NUM_SI 3 CONFIG.NUM_MI 1] $data
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset reset]
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant one]
set_property -dict [list CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 1] $one
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl/M00_AXI] [get_bd_intf_pins gemm/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ctrl/M01_AXI] [get_bd_intf_pins status/S_AXI]
foreach {i prefix} {0 x 1 w 2 y} {
    connect_bd_intf_net [get_bd_intf_pins gemm/m_axi_gmem$i] [get_bd_intf_pins data/S0${i}_AXI]
    set signals [expr {$i<2 ? {arvalid arready rvalid rready rlast rresp} : {awvalid awready wvalid wready bvalid bready bresp}}]
    foreach s $signals {
        set a [get_bd_pins gemm/m_axi_gmem${i}_[string toupper $s]]
        set b [get_bd_pins data/S0${i}_AXI_${s}]
        set c [get_bd_pins status/${prefix}_${s}]
        if {[llength $a]!=1 || [llength $b]!=1 || [llength $c]!=1} {error "Missing monitor endpoint $i/$s"}
        connect_bd_net $a $b $c
        set attached [get_bd_pins -of_objects [get_bd_nets -of_objects $c]]
        if {[llength $attached]!=3 || [llength [filter $attached {DIR == O}]]!=1} {error "Monitor net not three endpoints: $attached"}
    }
}
connect_bd_intf_net [get_bd_intf_pins data/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins {ps/maxihpm0_fpd_aclk ps/saxihp0_fpd_aclk ctrl/aclk data/aclk gemm/ap_clk status/aclk reset/slowest_sync_clk}]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins reset/ext_reset_in]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins reset/dcm_locked]
connect_bd_net [get_bd_pins reset/peripheral_aresetn] [get_bd_pins {ctrl/aresetn data/aresetn gemm/ap_rst_n status/aresetn}]
assign_bd_address -offset 0xA0000000 -range 0x10000 -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs gemm/s_axi_control/Reg]
set seg [get_bd_addr_segs -of_objects [get_bd_intf_pins status/S_AXI]]
if {[llength $seg]!=1} {error "Unexpected observer address segment"}
assign_bd_address -offset 0xA0010000 -range 0x10000 -target_address_space [get_bd_addr_spaces ps/Data] $seg
foreach i {0 1 2} {
    set space [get_bd_addr_spaces gemm/Data_m_axi_gmem$i]
    assign_bd_address -offset 0 -range 0x80000000 -target_address_space $space [get_bd_addr_segs ps/SAXIGP2/HP0_DDR_LOW]
    assign_bd_address -target_address_space $space [get_bd_addr_segs ps/SAXIGP2/HP0_DDR_HIGH]
}
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH $rst] ne "0"} {error "Wrong reset polarity"}
if {[get_property CONFIG.PSU__MAXIGP0__DATA_WIDTH $ps] ne "128" || [get_property CONFIG.PSU__SAXIGP2__DATA_WIDTH $ps] ne "128"} {error "PS fabric widths differ"}
save_bd_design
write_bd_tcl [file join $run generated_bd.tcl]
report_property -all $ps -file [file join $run ps_properties.txt]
set f [open [file join $run address_map.txt] w]
foreach space [get_bd_addr_spaces] {
    puts $f "SPACE $space"
    foreach s [get_bd_addr_segs -of_objects $space] {puts $f "$s OFFSET=[get_property OFFSET $s] RANGE=[get_property RANGE $s]"}
}
close $f
set bd [get_files kv260_model.bd]
generate_target all $bd
add_files -norecurse [make_wrapper -files $bd -top]
set_property top kv260_model_wrapper [current_fileset]
update_compile_order -fileset sources_1
mark bd_pass.txt PASS
if {$::env(MG_BUILD) eq "1"} {
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis incomplete"}
    set f [open [file join $run project model_kv260.runs synth_1 runme.log] r]
    set log [read $f]; close $f
    if {[regexp {CRITICAL WARNING:.*unconnected pin} $log]} {error "Unconnected synthesis pins"}
    set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation incomplete"}
    open_run impl_1
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $run timing_summary.rpt]
    report_drc -file [file join $run drc.rpt]
    report_utilization -hierarchical -file [file join $run utilization.rpt]
    report_route_status -file [file join $run route_status.rpt]
    set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
    set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
    if {$setup eq "" || $hold eq "" || $setup<0 || $hold<0} {error "Timing failed $setup/$hold"}
    if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]} {error "DRC errors"}
    file mkdir [file join $run artifacts]
    foreach ext {bit bin} {file copy [file join $run project model_kv260.runs impl_1 kv260_model_wrapper.$ext] [file join $run artifacts kv260_model.$ext]}
    set hwh [file join $run project model_kv260.gen sources_1 bd kv260_model hw_handoff kv260_model.hwh]
    if {![file exists $hwh]} {set hwh [file join $run project model_kv260.srcs sources_1 bd kv260_model hw_handoff kv260_model.hwh]}
    file copy $hwh [file join $run artifacts kv260_model.hwh]
    write_hw_platform -fixed -include_bit [file join $run artifacts kv260_model.xsa]
    mark build_pass.txt "PASS WNS=$setup WHS=$hold"
}
close_project
exit 0
