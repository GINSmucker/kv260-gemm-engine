# Standalone OPT50 full-system recipe. Invoked only by run_vivado.ps1.
# No device programming, driver/module operation, or board connection is performed.
# A successful local build is not numerical, board, or independent timing acceptance.

proc require {condition message} {
    if {![uplevel 1 [list expr $condition]]} {error $message}
}

proc singleton {objects description} {
    require {[llength $objects] == 1} "Expected one $description; found: $objects"
    return [lindex $objects 0]
}

proc pin {path} {return [singleton [get_bd_pins -quiet $path] "pin $path"]}
proc intf {path} {return [singleton [get_bd_intf_pins -quiet $path] "interface $path"]}
proc addrspace {path} {return [singleton [get_bd_addr_spaces -quiet $path] "address space $path"]}
proc addrseg {path} {return [singleton [get_bd_addr_segs -quiet $path] "address segment $path"]}

proc new_text {path value} {
    # EXCL is intentional: an interrupted/failed run must never be overwritten.
    set handle [open $path {WRONLY CREAT EXCL}]
    puts $handle $value
    close $handle
}

proc read_text {path} {
    set handle [open $path r]
    set result [read $handle]
    close $handle
    return $result
}

proc connect_scalar_paths {paths} {
    # Resolve each path separately; a braced whitespace string is not a list of
    # get_bd_pins patterns on every supported Vivado command implementation.
    set pins {}
    foreach path $paths {lappend pins [pin $path]}
    connect_bd_net {*}$pins
}

proc check_scalar_endpoints {paths} {
    set expected {}
    foreach path $paths {lappend expected [pin $path]}
    set net [singleton [get_bd_nets -quiet -of_objects [lindex $expected 0]] "scalar net $paths"]
    set actual [get_bd_pins -quiet -of_objects $net]
    require {[lsort $actual] eq [lsort $expected]} "Unexpected scalar endpoints on $net: $actual"
    set outputs {}
    foreach endpoint $actual {
        if {[get_property DIR $endpoint] eq "O"} {lappend outputs $endpoint}
    }
    require {[llength $outputs] == 1} "Net $net must have exactly one output driver: $outputs"
    require {[llength [get_bd_ports -quiet -of_objects $net]] == 0} "Unexpected external scalar port on $net"
}

proc check_interface_endpoints {paths} {
    set expected {}
    foreach path $paths {lappend expected [intf $path]}
    set net [singleton [get_bd_intf_nets -quiet -of_objects [lindex $expected 0]] "interface net $paths"]
    set actual [get_bd_intf_pins -quiet -of_objects $net]
    require {[lsort $actual] eq [lsort $expected]} "Unexpected interface endpoints on $net: $actual"
    require {[llength [get_bd_intf_ports -quiet -of_objects $net]] == 0} "Unexpected external AXI interface on $net"
}

proc check_address_ranges {space_path expected} {
    set actual {}
    foreach segment [get_bd_addr_segs -quiet -of_objects [addrspace $space_path]] {
        set offset [get_property OFFSET $segment]
        set range [get_property RANGE $segment]
        require {[string is entier -strict $offset] && [string is entier -strict $range]} "Non-numeric mapped address at $segment"
        lappend actual [list [expr {wide($offset)}] [expr {wide($range)}]]
    }
    set normalized {}
    foreach pair $expected {
        lassign $pair offset range
        lappend normalized [list [expr {wide($offset)}] [expr {wide($range)}]]
    }
    require {[lsort $actual] eq [lsort $normalized]} "Address range mismatch for $space_path: $actual"
}

proc observer_test {} {
    set simdir [file join $::run observer_sim]
    require {![file exists $simdir]} "Observer simulation output already exists"
    create_project opt50_observer $simdir -part $::part
    set_property target_simulator XSim [current_project]
    add_files -norecurse [file join $::project_root input observer f8_status.v]
    add_files -norecurse -fileset sim_1 [file join $::project_root input observer tb_f8_status.sv]
    set_property top tb_f8_status [get_filesets sim_1]
    set_property xsim.simulate.runtime all [get_filesets sim_1]
    update_compile_order -fileset sim_1
    launch_simulation -simset sim_1 -mode behavioral
    close_sim
    set logfile [file join $simdir opt50_observer.sim sim_1 behav xsim simulate.log]
    require {[file isfile $logfile]} "Observer simulation log missing: $logfile"
    set text [read_text $logfile]
    require {[regexp {F8_STATUS_RTL_PASS checks=[1-9][0-9]*} $text]} "Observer test did not reach its PASS assertion"
    require {![regexp -nocase {fatal:|error:|\$fatal} $text]} "Observer simulation reported a fatal/error"
    close_project
    new_text [file join $::run observer_pass.txt] "PASS"
}

proc build_bd {} {
    set projectdir [file join $::run project]
    require {![file exists $projectdir]} "Project output already exists"
    set_param board.repoPaths [list [file join $::project_root input board_files]]
    create_project opt50_kv260 $projectdir -part $::part
    set boards [get_board_parts -quiet xilinx.com:kv260:part0:1.1]
    singleton $boards "KV260 board definition xilinx.com:kv260:part0:1.1"
    set_property board_part xilinx.com:kv260:part0:1.1 [current_project]
    set_property target_language Verilog [current_project]
    set_property ip_repo_paths [list [file join $::project_root input ip]] [current_project]
    update_ip_catalog
    singleton [get_ipdefs -quiet xilinx.com:hls:model_gemm_top:1.0] "OPT50 HLS IP definition"
    add_files -norecurse [file join $::project_root input observer f8_status.v]
    update_compile_order -fileset sources_1
    create_bd_design kv260_opt50

    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
        -config {apply_board_preset "1"} $ps
    # Preserve the board DDR/MIO preset. Only the reviewed fabric ports and PL0
    # clock are changed; retain a complete pre-override property snapshot.
    report_property -all $ps -file [file join $::run ps_board_preset_properties.txt]
    set_property -dict [list \
        CONFIG.PSU__USE__M_AXI_GP0 1 CONFIG.PSU__USE__M_AXI_GP1 0 CONFIG.PSU__USE__M_AXI_GP2 0 \
        CONFIG.PSU__MAXIGP0__DATA_WIDTH 128 \
        CONFIG.PSU__USE__S_AXI_GP0 0 CONFIG.PSU__USE__S_AXI_GP1 0 CONFIG.PSU__USE__S_AXI_GP2 1 \
        CONFIG.PSU__SAXIGP2__DATA_WIDTH 128 CONFIG.PSU__USE__S_AXI_GP3 0 \
        CONFIG.PSU__USE__S_AXI_GP4 0 CONFIG.PSU__USE__S_AXI_GP5 0 CONFIG.PSU__USE__S_AXI_GP6 0 \
        CONFIG.PSU__USE__IRQ0 0 CONFIG.PSU__USE__IRQ1 0 CONFIG.PSU__FPGA_PL0_ENABLE 1 \
        CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ 125] $ps

    create_bd_cell -type ip -vlnv xilinx.com:hls:model_gemm_top:1.0 gemm
    set observer [create_bd_cell -type module -reference f8_status status]
    set_property -dict [list CONFIG.BUILD_TAG0 $::tag0 CONFIG.BUILD_TAG1 $::tag1] $observer
    set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect ctrl]
    set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 2] $ctrl
    set data [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect data]
    set_property -dict [list CONFIG.NUM_SI 3 CONFIG.NUM_MI 1] $data
    set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset reset]
    # Pin BOTH input polarities. With auxiliary reset tied to zero below,
    # C_AUX_RESET_HIGH=0 would hold the entire fabric in reset indefinitely.
    # Do not rely on IP defaults or board-flow polarity propagation.
    set_property -dict [list CONFIG.C_EXT_RESET_HIGH 0 CONFIG.C_AUX_RESET_HIGH 1] $reset
    set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant one]
    set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant zero]
    set_property -dict [list CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 1] $one
    set_property -dict [list CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0] $zero

    connect_bd_intf_net [intf ps/M_AXI_HPM0_FPD] [intf ctrl/S00_AXI]
    connect_bd_intf_net [intf ctrl/M00_AXI] [intf gemm/s_axi_control]
    connect_bd_intf_net [intf ctrl/M01_AXI] [intf status/S_AXI]
    foreach {index prefix} {0 x 1 w 2 y} {
        connect_bd_intf_net [intf gemm/m_axi_gmem$index] [intf data/S0${index}_AXI]
        # W_data and W_meta intentionally use the SAME gmem1 master. There is
        # no fourth master or independently fabricated metadata connection.
        if {$index < 2} {
            set signals {arvalid arready rvalid rready rlast rresp}
        } else {
            set signals {awvalid awready wvalid wready bvalid bready bresp}
        }
        foreach signal $signals {
            set endpoints [list \
                gemm/m_axi_gmem${index}_[string toupper $signal] \
                data/S0${index}_AXI_${signal} status/${prefix}_${signal}]
            connect_scalar_paths $endpoints
            check_scalar_endpoints $endpoints
        }
    }
    connect_bd_intf_net [intf data/M00_AXI] [intf ps/S_AXI_HP0_FPD]
    set clock_endpoints {ps/pl_clk0 ps/maxihpm0_fpd_aclk ps/saxihp0_fpd_aclk ctrl/aclk data/aclk gemm/ap_clk status/aclk reset/slowest_sync_clk}
    set reset_endpoints {reset/peripheral_aresetn ctrl/aresetn data/aresetn gemm/ap_rst_n status/aresetn}
    connect_scalar_paths $clock_endpoints
    connect_scalar_paths {ps/pl_resetn0 reset/ext_reset_in}
    connect_scalar_paths {one/dout reset/dcm_locked}
    connect_scalar_paths {zero/dout reset/aux_reset_in reset/mb_debug_sys_rst}
    connect_scalar_paths $reset_endpoints

    assign_bd_address -offset 0xA0000000 -range 0x10000 \
        -target_address_space [addrspace ps/Data] [addrseg gemm/s_axi_control/Reg]
    set status_segment [singleton [get_bd_addr_segs -quiet -of_objects [intf status/S_AXI]] "observer register segment"]
    assign_bd_address -offset 0xA0010000 -range 0x10000 \
        -target_address_space [addrspace ps/Data] $status_segment
    foreach index {0 1 2} {
        set space [addrspace gemm/Data_m_axi_gmem$index]
        assign_bd_address -offset 0 -range 0x80000000 -target_address_space $space [addrseg ps/SAXIGP2/HP0_DDR_LOW]
        assign_bd_address -offset 0x800000000 -range 0x80000000 -target_address_space $space [addrseg ps/SAXIGP2/HP0_DDR_HIGH]
    }
    validate_bd_design

    check_address_ranges ps/Data {{0xA0000000 0x10000} {0xA0010000 0x10000}}
    foreach index {0 1 2} {
        check_address_ranges gemm/Data_m_axi_gmem$index {{0 0x80000000} {0x800000000 0x80000000}}
    }
    require {[get_property CONFIG.C_EXT_RESET_HIGH $reset] eq "0"} "External reset must remain active-low"
    require {[get_property CONFIG.C_AUX_RESET_HIGH $reset] eq "1"} "Auxiliary reset tied to zero must be active-high"
    require {[get_property CONFIG.CONST_VAL $one] == 1 && [get_property CONFIG.CONST_WIDTH $one] == 1} "DCM locked must be tied to scalar one"
    require {[get_property CONFIG.CONST_VAL $zero] == 0 && [get_property CONFIG.CONST_WIDTH $zero] == 1} "Unused active-high reset inputs must be tied to scalar zero"
    require {[get_property CONFIG.PSU__MAXIGP0__DATA_WIDTH $ps] eq "128"} "HPM0 must be 128-bit"
    require {[get_property CONFIG.PSU__SAXIGP2__DATA_WIDTH $ps] eq "128"} "HP0 must be 128-bit"
    require {[get_property CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ $ps] == 125} "PL0 requested frequency must be 125 MHz"
    foreach path {gemm/m_axi_gmem0 gemm/m_axi_gmem1 gemm/m_axi_gmem2 ps/M_AXI_HPM0_FPD ps/S_AXI_HP0_FPD} {
        require {[get_property CONFIG.DATA_WIDTH [intf $path]] == 128} "AXI data width mismatch at $path"
    }
    foreach index {0 1 2} {
        require {[get_property CONFIG.HAS_BURST [intf gemm/m_axi_gmem$index]] == 1} "Copied IP Burst declaration is not corrected"
    }
    foreach paths [list \
        {ps/M_AXI_HPM0_FPD ctrl/S00_AXI} {ctrl/M00_AXI gemm/s_axi_control} \
        {ctrl/M01_AXI status/S_AXI} {data/M00_AXI ps/S_AXI_HP0_FPD} \
        {gemm/m_axi_gmem0 data/S00_AXI} {gemm/m_axi_gmem1 data/S01_AXI} {gemm/m_axi_gmem2 data/S02_AXI}] {
        check_interface_endpoints $paths
    }
    check_scalar_endpoints $clock_endpoints
    check_scalar_endpoints $reset_endpoints
    check_scalar_endpoints {ps/pl_resetn0 reset/ext_reset_in}
    check_scalar_endpoints {one/dout reset/dcm_locked}
    check_scalar_endpoints {zero/dout reset/aux_reset_in reset/mb_debug_sys_rst}
    # Validate final propagated pin metadata as well as the requested generic.
    require {[get_property CONFIG.POLARITY [pin reset/ext_reset_in]] eq "ACTIVE_LOW"} "External reset pin polarity changed"
    require {[get_property CONFIG.POLARITY [pin reset/aux_reset_in]] eq "ACTIVE_HIGH"} "Auxiliary reset pin polarity changed"
    require {[get_property CONFIG.POLARITY [pin reset/mb_debug_sys_rst]] eq "ACTIVE_HIGH"} "Debug reset must be active-high"
    foreach {index prefix} {0 x 1 w 2 y} {
        if {$index < 2} {set signals {arvalid arready rvalid rready rlast rresp}} \
        else {set signals {awvalid awready wvalid wready bvalid bready bresp}}
        foreach signal $signals {
            check_scalar_endpoints [list gemm/m_axi_gmem${index}_[string toupper $signal] \
                data/S0${index}_AXI_${signal} status/${prefix}_${signal}]
        }
    }
    save_bd_design
    write_bd_tcl [file join $::run generated_bd.tcl]
    report_property -all $ps -file [file join $::run ps_properties.txt]
    report_property -all [get_bd_cells gemm] -file [file join $::run gemm_properties.txt]
    report_property -all $observer -file [file join $::run observer_properties.txt]
    report_property -all $reset -file [file join $::run reset_properties.txt]
    new_text [file join $::run reset_contract.txt] "EXT_ACTIVE_LOW_FROM_PS\nAUX_ACTIVE_HIGH_TIED_ZERO\nDEBUG_ACTIVE_HIGH_TIED_ZERO\nDCM_LOCKED_TIED_ONE\nSINGLE_PL0_CLOCK_AND_SHARED_PERIPHERAL_RESET\nSTATIC_BD_ONLY_NOT_RTL_OR_BOARD_PROOF"
    set addresses ""
    foreach space [lsort [get_bd_addr_spaces]] {
        append addresses "SPACE $space\n"
        foreach segment [lsort [get_bd_addr_segs -of_objects $space]] {
            append addresses "$segment OFFSET=[get_property OFFSET $segment] RANGE=[get_property RANGE $segment]\n"
        }
    }
    new_text [file join $::run address_map.txt] $addresses
    new_text [file join $::run bd_pass.txt] "PASS"
    return [singleton [get_files -quiet */kv260_opt50.bd] "saved OPT50 block design"]
}

proc report_count {text label} {
    # Avoid Tcl command substitution in a dynamically composed regexp.
    set expression [format {# of %s[. ]*:\s*([0-9]+)\s*:} $label]
    require {[regexp -nocase $expression $text -> count]} "Unknown Vivado route report format: $label"
    return $count
}

proc implement_system {bd} {
    # Same-run RTL gate: no synthesis or place/route starts until the actual
    # production BD has released reset and passed AXI + Dense/Sparse tests.
    require {[read_text [file join $::run board_logic_pass.txt]] eq "PASS\n"} "Same-run board logic RTL simulation is required before implementation"
    generate_target all $bd
    set wrapper [make_wrapper -files $bd -top]
    require {[llength $wrapper] == 1 && [file isfile $wrapper]} "Expected one generated full-system wrapper"
    if {[llength [get_files -quiet $wrapper]] == 0} {add_files -norecurse $wrapper}
    set_property top kv260_opt50_wrapper [current_fileset]
    update_compile_order -fileset sources_1
    launch_runs synth_1 -jobs $::jobs
    wait_on_run synth_1
    set synth [get_runs synth_1]
    require {[get_property PROGRESS $synth] eq "100%" && [get_property STATUS $synth] eq "synth_design Complete!"} "System synthesis did not complete successfully"
    set synth_log [file join [get_property DIRECTORY $synth] runme.log]
    require {[file isfile $synth_log]} "Synthesis log missing"
    require {![regexp -nocase {CRITICAL WARNING:[^\r\n]*(unconnected|undriven)|ERROR:} [read_text $synth_log]]} "Synthesis has errors or critical undriven/unconnected pins"
    set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]
    launch_runs impl_1 -to_step write_bitstream -jobs $::jobs
    wait_on_run impl_1
    set implementation [get_runs impl_1]
    require {[get_property PROGRESS $implementation] eq "100%" && [get_property STATUS $implementation] eq "write_bitstream Complete!"} "System implementation/bitstream did not complete successfully"
    open_run impl_1
    require {[llength [get_clocks -quiet]] > 0} "No timing clocks in implemented system"
    report_clocks -file [file join $::run clocks.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $::run timing_summary.rpt]
    redirect -file [file join $::run check_timing.rpt] {check_timing -verbose}
    report_drc -file [file join $::run drc.rpt]
    report_utilization -hierarchical -file [file join $::run utilization.rpt]
    report_route_status -file [file join $::run route_status.rpt]
    set setup_path [singleton [get_timing_paths -quiet -delay_type max -max_paths 1] "setup timing path"]
    set hold_path [singleton [get_timing_paths -quiet -delay_type min -max_paths 1] "hold timing path"]
    set setup [get_property SLACK $setup_path]
    set hold [get_property SLACK $hold_path]
    require {[string is double -strict $setup] && [string is double -strict $hold] && $setup >= 0 && $hold >= 0} "Routed timing failed: setup=$setup hold=$hold"
    foreach violation [get_drc_violations -quiet] {
        set severity [string tolower [get_property SEVERITY $violation]]
        require {$severity ni {error {critical warning}}} "Unresolved DRC $severity: $violation"
    }
    set route [read_text [file join $::run route_status.rpt]]
    set routable [report_count $route {routable nets}]
    set routed [report_count $route {fully routed nets}]
    set errors [report_count $route {nets with routing errors}]
    require {$routable > 0 && $routable == $routed && $errors == 0} "System routing incomplete: $routed/$routable errors=$errors"
    set artifacts [file join $::run artifacts]
    require {![file exists $artifacts]} "Artifact output already exists"
    file mkdir $artifacts
    set implementation_directory [get_property DIRECTORY $implementation]
    foreach {source_suffix destination_suffix} {bit bit bin bit.bin} {
        set source [file join $implementation_directory kv260_opt50_wrapper.$source_suffix]
        require {[file isfile $source] && [file size $source] > 0} "Missing system artifact: $source"
        file copy $source [file join $artifacts kv260_opt50.$destination_suffix]
    }
    set candidates [list \
        [file join $::run project opt50_kv260.gen sources_1 bd kv260_opt50 hw_handoff kv260_opt50.hwh] \
        [file join $::run project opt50_kv260.srcs sources_1 bd kv260_opt50 hw_handoff kv260_opt50.hwh]]
    set handoffs {}
    foreach candidate $candidates {if {[file isfile $candidate]} {lappend handoffs $candidate}}
    set handoff [singleton $handoffs "generated system HWH"]
    file copy $handoff [file join $artifacts kv260_opt50.hwh]
    write_hw_platform -fixed -include_bit -file [file join $artifacts kv260_opt50.xsa]
    new_text [file join $::run build_pass.txt] "PASS WNS=$setup WHS=$hold"
}

proc main {} {
    require {[llength $::argv] == 6} "Expected: project_root run_dir stage tag0 tag1 jobs"
    lassign $::argv project_root run stage tag0 tag1 jobs
    set ::project_root [file normalize $project_root]
    set ::run [file normalize $run]
    set ::stage $stage
    set ::part xck26-sfvc784-2LV-c
    require {[version -short] eq "2026.1"} "Only reviewed Vivado 2026.1 is supported"
    require {$stage in {bd observer logic build}} "Unsupported explicit stage: $stage"
    require {[string is integer -strict $jobs] && $jobs >= 1 && $jobs <= 8} "jobs must be 1..8"
    set ::jobs $jobs
    foreach {name value} [list tag0 $tag0 tag1 $tag1] {
        require {[regexp {^(0x[0-9a-fA-F]{1,8}|[0-9]{1,10})$} $value]} "Invalid uint32 $name"
        set ::$name [expr {wide($value)}]
        require {[set ::$name] >= 0 && [set ::$name] <= 4294967295} "$name is outside uint32"
    }
    require {$::tag0 != 0 || $::tag1 != 0} "Zero observer identity is forbidden"
    require {!($::tag0 == 3652287023 && $::tag1 == 1996644805)} "Do not reuse the old H576 observer identity"
    require {[file isdirectory $::project_root] && [file isdirectory $::run]} "Prepared project and run directories must already exist"
    require {[file dirname $::run] eq [file join $::project_root build]} "Run must be an immediate child of this project's build directory"
    require {![regexp {[^\x20-\x7e]} $::project_root]} "Vivado project path must be ASCII"
    foreach relative {project observer_sim artifacts generated_bd.tcl observer_pass.txt bd_pass.txt board_logic_pass.txt board_logic_simulate.log build_pass.txt failure.txt} {
        require {![file exists [file join $::run $relative]]} "Refusing existing stage output: $relative"
    }
    foreach relative {input/ip/component.xml input/observer/f8_status.v input/observer/tb_f8_status.sv scripts/logic_sim.tcl tests/tb_opt50_board_logic.sv project_manifest.json} {
        require {[file isfile [file join $::project_root $relative]]} "Prepared input missing: $relative"
    }
    require {[file isdirectory [file join $::project_root input board_files]]} "Prepared board repository missing"
    singleton [get_parts -quiet $::part] "K26 target part"
    source [file join $::project_root scripts logic_sim.tcl]
    cd $::run
    new_text [file join $::run stage_scope.txt] "STAGE=$stage\nVIVADO=[version -short]\nPART=$::part\nREQUESTED_PL_CLOCK_HZ=125000000\nOBSERVER_TAG0=$::tag0\nOBSERVER_TAG1=$::tag1\nBOARD_NOT_RUN\nNO_DRIVER_OR_OVERLAY_LOADING\nBD_ONLY_IS_NOT_IMPLEMENTATION\nSYSTEM_BUILD_IS_NOT_NUMERICAL_OR_BOARD_ACCEPTANCE"
    if {$stage in {observer build}} {observer_test}
    if {$stage in {bd logic build}} {
        set bd [build_bd]
        if {$stage in {logic build}} {board_logic_test $bd}
        if {$stage eq "build"} {implement_system $bd}
        close_project
    }
}

if {[catch {main} message options]} {
    puts stderr "OPT50_SYSTEM_STAGE_FAIL: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    if {[info exists ::run] && [file isdirectory $::run] && ![file exists [file join $::run failure.txt]]} {
        catch {new_text [file join $::run failure.txt] "FAIL\n$message\nPRESERVE_THIS_RUN\nBOARD_NOT_RUN"}
    }
    exit 1
}
exit 0
