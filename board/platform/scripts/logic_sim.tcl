# Source-only procedure: actual production BD behavioral simulation in XSim.
# Never launches synthesis, implementation, hardware manager, or a board tool.
# Caller owns the fresh run and has already created/validated its production BD.
proc board_logic_test {bd} {
    require {[info exists ::run] && [info exists ::project_root] &&
             [info exists ::tag0] && [info exists ::tag1]} "Logic simulation requires the current run identity"
    # A file returned through a Tcl procedure can lose Vivado's object handle
    # representation. Validate its path, then re-resolve get_files at each API.
    set bd_path [file normalize [singleton $bd "production block design for behavioral simulation"]]
    require {[file isfile $bd_path] && [file tail $bd_path] eq "kv260_opt50.bd"} "Unexpected production BD"
    singleton [get_files -quiet $bd_path] "registered production BD"
    set testbench [file join $::project_root tests tb_opt50_board_logic.sv]
    require {[file isfile $testbench]} "Missing integrated RTL test bench"
    set patch_file [file join $::project_root input sim_model vendor_arb_wr_6_fixed.sv]
    set real_include [file join $::project_root input sim_cases opt50_real_cases.svh]
    require {[file isfile $patch_file] && [file isfile $real_include]} "Prepared local model and full48 real-case include required"
    foreach name {board_logic_pass.txt board_logic_simulate.log} {
        require {![file exists [file join $::run $name]]} "Refusing existing logic result: $name"
    }

    # UG835: generate the BD's simulation target; get_ips must not be used to
    # generate an individual IP target from inside a BD. The PS simulation model
    # supplies the vendor VIP APIs used by this test, without altering the BD.
    generate_target simulation [get_files -quiet $bd_path]
    set wrappers [make_wrapper -files [get_files -quiet $bd_path] -top]
    set wrapper [singleton $wrappers "generated production HDL wrapper"]
    require {[file isfile $wrapper]} "Production wrapper was not generated"
    if {[llength [get_files -quiet $wrapper]] == 0} {add_files -norecurse $wrapper}
    set_property target_simulator XSim [current_project]
    # SIMULATION fileset ONLY. This library contains one arbiter module and
    # cannot be used for synthesis/implementation or modify the installed VIP.
    add_files -norecurse -fileset sim_1 $patch_file
    set_property file_type SystemVerilog [get_files $patch_file]
    set_property library opt50_ps_vip_fixed [get_files $patch_file]
    set_property used_in_synthesis false [get_files $patch_file]
    set_property used_in_implementation false [get_files $patch_file]
    add_files -norecurse -fileset sim_1 $testbench
    set_property file_type SystemVerilog [get_files $testbench]
    set simset [get_filesets sim_1]
    set_property top tb_opt50_board_logic $simset
    set_property generic [list TAG0=[expr {wide($::tag0)}] TAG1=[expr {wide($::tag1)}]] $simset
    set_property xsim.simulate.runtime all $simset
    set_property include_dirs [list [file join $::project_root input sim_model] [file join $::project_root input sim_cases]] $simset
    update_compile_order -fileset sources_1
    update_compile_order -fileset sim_1
    set project_name [get_property NAME [current_project]]
    set project_directory [get_property DIRECTORY [current_project]]
    set logfile [file join $project_directory ${project_name}.sim sim_1 behav xsim simulate.log]

    # Generate the REAL production BD scripts, then actually execute all three
    # steps. scripts_only is preparation, never evidence of simulation PASS.
    # Vivado's automatic source pruning omits our duplicate module, and its
    # more_options appends -L after the original library. Explicit compilation
    # and first-library ordering are therefore required (UG900).
    launch_simulation -simset sim_1 -mode behavioral -scripts_only
    set simdir [file dirname $logfile]
    foreach script {compile.bat elaborate.bat simulate.bat} {
        require {[file isfile [file join $simdir $script]]} "Generated actual simulation step missing: $script"
    }
    set elaborate_file [file join $simdir elaborate.bat]
    set original [read_text $elaborate_file]
    require {[regexp -line -all {^call xelab[ \t]+} $original] == 1} "Unexpected generated elaboration command"
    set ordered [regsub -line {^call xelab[ \t]+} $original {call xelab -L opt50_ps_vip_fixed }]
    new_text [file join $::run board_logic_elaborate_original.bat] $original
    # Only a newly generated run-local command is rewritten, not an old run,
    # tool installation, compiled vendor library, source RTL, or Golden.
    set handle [open $elaborate_file w]
    puts -nonewline $handle $ordered
    close $handle
    require {[info exists ::env(XILINX_VIVADO)]} "Vivado tool environment missing"
    set xvlog [file join $::env(XILINX_VIVADO) bin xvlog.bat]
    require {[file isfile $xvlog]} "Current tool's xvlog executable missing"
    set previous [pwd]
    cd $simdir
    foreach {label command} [list \
        compile [list cmd.exe /d /c compile.bat] \
        local_model [list cmd.exe /d /c $xvlog --sv --work opt50_ps_vip_fixed --include [file dirname $patch_file] $patch_file -log local_model_compile.log] \
        elaborate [list cmd.exe /d /c elaborate.bat] \
        simulate [list cmd.exe /d /c simulate.bat]] {
        puts "OPT50_EXECUTING_ACTUAL_XSIM_STEP $label"
        set failed [catch {exec {*}$command 2>@1} output options]
        new_text [file join $::run board_logic_step_${label}.log] $output
        if {$failed} {
            cd $previous
            error "Actual XSim $label failed: $output"
        }
        require {![regexp -line -nocase {^[ \t]*(ERROR:|FATAL:)} $output]} "Actual XSim $label reported an error despite its exit code"
    }
    cd $previous
    require {[file isfile $logfile] && [file size $logfile] > 0} "Actual XSim simulation log is missing"
    set published [file join $::run board_logic_simulate.log]
    file copy $logfile $published
    set text [read_text $published]
    require {![regexp -nocase {fatal:|error:|\$fatal|\$error|OPT50_BOARD_LOGIC_RTL_FAIL} $text]} "Integrated RTL simulation failed; preserve log"
    # XSim prints parameter specialization in %m. Check its exact 32-bit
    # generics against this run, rather than stripping an arbitrary prefix.
    set instance_prefix "tb_opt50_board_logic(TAG0=32'b"
    for {set bit 31} {$bit >= 0} {incr bit -1} {append instance_prefix [expr {($::tag0 >> $bit) & 1}]}
    append instance_prefix ",TAG1=32'b"
    for {set bit 31} {$bit >= 0} {incr bit -1} {append instance_prefix [expr {($::tag1 >> $bit) & 1}]}
    append instance_prefix ").dut.kv260_opt50_i.ps.inst."
    set expected_instances [list \
        ${instance_prefix}ddrc.ddr_write_ports \
        ${instance_prefix}ocm_interconnect.write_ports \
        ${instance_prefix}reg_interconnect.write_ports]
    set bound {}
    foreach line [split $text \n] {
        if {[string match OPT50_PSVIP_ARB_FIXED_V1* $line]} {
            require {[regexp {^OPT50_PSVIP_ARB_FIXED_V1 changes=36 instance=([^ \t\r]+)[\r]*$} $line -> instance]} "Unexpected local-model identity line"
            lappend bound $instance
        }
    }
    require {[lsort $bound] eq [lsort $expected_instances]} "Local model must bind exactly DDR/OCM/REG arbiter instances"
    require {[regexp -line -all {^OPT50_REAL_CASES_COMPLETED profile=full48 jobs=48 PL_STARTS=48 INT32=1870848 PACKAGE_SHA256=6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf BOARD_NOT_RUN[ \t\r]*$} $text] == 1} "Frozen full48 regression did not finish"
    set pattern {^OPT50_BOARD_LOGIC_RTL_PASS checks=([1-9][0-9]*) TAG0=([0-9]+) TAG1=([0-9]+) PL_STARTS=50 REAL_JOBS=48 INT32=1870848 MODEL=LOCAL_ARB_FIXED_V1[ \t\r]*$}
    require {[regexp -line -all $pattern $text] == 1} "Missing/duplicate actual integrated RTL PASS"
    require {[regexp -line $pattern $text -> checks tag0 tag1] &&
             $checks >= 1870848 && $tag0 == $::tag0 && $tag1 == $::tag1} "Integrated RTL result belongs to another run or lacks output checks"
    require {[file isfile $elaborate_file]} "Actual elaboration command evidence missing"
    file copy $elaborate_file [file join $::run board_logic_elaborate.bat]
    new_text [file join $::run simulation_model_scope.txt] "LOCAL_ARB_FIXED_V1\n36_REQUEST_PUBLICATIONS_MOVED_AFTER_PAYLOAD\nTHREE_INSTANCES_DDR_OCM_REG\nOCM_REG_BUSINESS_TRAFFIC_NOT_QUALIFIED\nPROJECT_LOCAL_SIMULATION_LIBRARY_ONLY\nORIGINAL_VENDOR_MODEL_FAIL_PRESERVED\nNO_HARDWARE_RTL_CHANGE\nFULL48_PLUS_TWO_TINY_CASES\nNOT_BOARD_ACCEPTANCE"
    new_text [file join $::run board_logic_pass.txt] "PASS"
}
