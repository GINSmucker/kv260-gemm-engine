[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RunDirectory,[string]$MonitorToolsPath,[switch]$PrepareFromSynthesis,[string]$PythonExecutable='python',[Parameter(Mandatory=$true)][string]$HlsTool)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$monitorTools=$PSScriptRoot
$python=(Get-Command $PythonExecutable -ErrorAction Stop).Source
if($MonitorToolsPath){
    $monitorTools=[IO.Path]::GetFullPath((Join-Path $repo $MonitorToolsPath))
    if(-not $monitorTools.StartsWith($repo+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Monitor tools outside repository'}
}
$run=[IO.Path]::GetFullPath((Join-Path $repo $RunDirectory))
if(-not $run.StartsWith((Join-Path $repo 'results/A/A1')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected replay target'}
if($PrepareFromSynthesis){
    if(-not $MonitorToolsPath){throw 'Combined simulation requires versioned candidate monitor tools'}
    foreach($stage in @('csim','csynth')){
        if((Get-Content -LiteralPath (Join-Path $run ($stage+'_pass.txt')) -Raw).Trim() -ne 'PASS'){throw "Required $stage gate missing"}
    }
    if(Test-Path -LiteralPath (Join-Path $run 'model_gemm_hls/solution1/sim')){throw 'Preserve existing simulation; combined setup requires a fresh sim directory'}
    $replayConfig=Get-Content -LiteralPath (Join-Path $run 'config.json') -Raw | ConvertFrom-Json
    if($replayConfig.cosim_profile -notmatch '^[a-z0-9_]+$'){throw 'Invalid replay profile'}
}elseif(-not (Test-Path -LiteralPath (Join-Path $run 'cosim_pass.txt'))){throw 'Official full C/RTL co-sim must pass first'}
if(Test-Path -LiteralPath (Join-Path $run 'axi_monitor_injection.json')){throw 'Replay already prepared; preserve existing evidence'}
$alias=$null;$saved=@{}
try{
    $used=@([Environment]::GetLogicalDrives() | ForEach-Object {$_.Substring(0,2)})
    $alias=@('Z:','Y:','X:','W:','V:','U:','T:','S:','R:') | Where-Object {$_ -notin $used} | Select-Object -First 1
    if(-not $alias){throw 'No ASCII drive available'}
    & subst $alias $repo
    if($LASTEXITCODE -ne 0){throw 'ASCII path mapping failed'}
    $aliasRun=Join-Path ($alias+'\') ([IO.Path]::GetRelativePath($repo,$run))
    $sim=Join-Path $aliasRun 'model_gemm_hls/solution1/sim/verilog'
    $profile=Join-Path $aliasRun 'userdata'
    $overrides=@{TEMP=(Join-Path $aliasRun 'temp');TMP=(Join-Path $aliasRun 'temp');USERPROFILE=$profile;APPDATA=(Join-Path $profile 'AppData/Roaming');LOCALAPPDATA=(Join-Path $profile 'AppData/Local');XILINX_LOCAL_USER_DATA='no';PROCESSOR_ARCHITECTURE='AMD64'}
    foreach($key in $overrides.Keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,$overrides[$key],'Process')}
    if($PrepareFromSynthesis){
        # HLS 2020.2.2 help confirms -setup emits all simulation files without running RTL.
        # The sole RTL run below is monitored, followed by the actual C output postcheck.
        $setup=@(
            'open_project model_gemm_hls',
            'open_solution solution1',
            ('cosim_design -setup -rtl verilog -tool xsim -argv '+$replayConfig.cosim_profile),
            'close_project',
            'exit 0'
        )
        $setupPath=Join-Path $aliasRun 'cosim_monitored_setup.tcl'
        $setup | Set-Content -LiteralPath $setupPath -Encoding ascii
        Push-Location $aliasRun
        try{
            & $HlsTool -f $setupPath *> (Join-Path $aliasRun 'cosim_monitored_setup.log')
            if($LASTEXITCODE -ne 0){throw 'HLS monitored setup failed'}
        }finally{Pop-Location}
    }
    # Preserve prior evidence; setup-only mode has no earlier RTL success.
    $priorFolder=if($PrepareFromSynthesis){'cosim_setup_before_monitor'}else{'official_cosim_first_run'}
    $original=Join-Path $run $priorFolder
    $null=New-Item -ItemType Directory -Path $original
    foreach($p in @('model_gemm_hls/solution1/sim/report/model_gemm_top_cosim.rpt','model_gemm_hls/solution1/sim/verilog/xsim.log','model_gemm_hls/solution1/sim/verilog/model_gemm_top.result.lat.rb','model_gemm_hls/solution1/sim/wrapc_pc/rtl_tb_summary.json','model_gemm_hls/solution1/sim/wrapc_pc/rtl_cases.csv')){
        $src=Join-Path $run $p
        if(Test-Path -LiteralPath $src){Copy-Item -LiteralPath $src -Destination (Join-Path $original ([IO.Path]::GetFileName($src)))}
    }
    & $python -X utf8 -B (Join-Path $monitorTools 'inject_axi_monitor.py') --run $aliasRun
    if($LASTEXITCODE -ne 0){throw 'Monitor preparation failed'}
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $run 'run_monitored_replay.ps1')
    Copy-Item -LiteralPath (Join-Path $monitorTools 'axi_replay_postcheck.tcl') -Destination (Join-Path $sim 'axi_replay_postcheck.tcl')
    Write-Output "MONITORED_REPLAY_RUN=$run"
    Push-Location $sim
    try{
        & (Join-Path $sim 'run_xsim.bat') *> (Join-Path $aliasRun 'axi_replay_console.log')
        $replayExit=$LASTEXITCODE
        if($replayExit -ne 0){throw "XSim replay exit $replayExit"}
        if($MonitorToolsPath -and (Get-Content -LiteralPath (Join-Path $aliasRun 'axi_replay_console.log') -Raw).Contains('AXI_MONITOR_FAIL')){throw 'AXI monitor rejected; actual RTL output may be incomplete'}
        # The generated C postcheck consumes the replay's actual RTL output files.
        & $HlsTool -f (Join-Path $sim 'axi_replay_postcheck.tcl').Replace('\','/') *> (Join-Path $aliasRun 'axi_replay_postcheck.log')
        if($LASTEXITCODE -ne 0){throw 'Actual RTL output C postcheck failed'}
    }finally{Pop-Location}
    & $python -X utf8 -B (Join-Path $monitorTools 'verify_axi_monitor.py') --run $aliasRun
    if($LASTEXITCODE -ne 0){throw 'AXI monitor result rejected'}
    'PASS' | Set-Content -LiteralPath (Join-Path $run 'axi_monitor_pass.txt') -Encoding utf8
    if($PrepareFromSynthesis){'PASS' | Set-Content -LiteralPath (Join-Path $run 'cosim_monitored_pass.txt') -Encoding utf8}
}finally{
    foreach($key in $saved.Keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
    if($alias){& subst $alias /D | Out-Null}
}
