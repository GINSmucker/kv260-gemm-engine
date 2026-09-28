# Read-only discovery of local Vivado support for the KV260 K26 device.
# Project board files are pinned from XilinxBoardStore 2020.2.2.
set board_files [file join [file dirname [file normalize [info script]]] board_files]
set_param board.repoPaths [list $board_files]
puts "VIVADO_VERSION=[version -short]"
set zynqmp_parts [get_parts -quiet xczu*]
puts "ZYNQMP_PARTS=$zynqmp_parts"
set parts [get_parts -quiet xck26*]
puts "K26_PARTS=$parts"
set boards [get_board_parts -quiet *:kv260:*]
puts "KV260_BOARD_PARTS=$boards"
if {[llength $parts] == 0} {
    puts "KV260_PART_READY=NO"
} else {
    puts "KV260_PART_READY=YES"
}
exit 0
