set sim [file dirname [file normalize [info script]]]
cd [file join $sim .. wrapc_pc]
set code [catch {exec ./cosim.pc.exe rtl} result]
puts $result
if {$code != 0} {error "RTL replay C postcheck failed: $result"}
set f [open rtl_tb_summary.json r]
set summary [read $f]
close $f
if {![regexp {"status":"PASS"} $summary] || ![regexp {"valid_cases_passed":12,} $summary]} {error "Replay coverage failed"}
exit 0
