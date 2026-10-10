[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('csim','csynth','cosim','export','all')][string]$Stage,
    [string]$Config='hls/config/opt35a_clear_v1_full.json',
    [string]$RunDirectory,
    [Parameter(Mandatory=$true)][string]$HlsTool
)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$configPath=[IO.Path]::GetFullPath((Join-Path $repo $Config))
$cfg=Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if($cfg.schema -ne 'model_gemm_hls_config_v1' -or $cfg.part -ne 'xck26-sfvc784-2LV-c' -or $cfg.flow -ne 'vivado' -or $cfg.clock_period_ns -ne 10 -or $cfg.clock_uncertainty_ns -ne 1 -or $cfg.address_bits -ne 64){throw 'Unreviewed tool configuration'}
$tool=[IO.Path]::GetFullPath($HlsTool)
if(-not (Test-Path -LiteralPath $tool -PathType Leaf)){throw 'Documented HLS launcher missing'}
$inputs=@($cfg.design_files)+@($cfg.tb_file)+@($cfg.tb_extra)
$inputHashes=[ordered]@{}
foreach($f in $inputs){$inputHashes[$f]=(Get-FileHash -LiteralPath (Join-Path (Join-Path $repo $cfg.source_root) $f)).Hash.ToLowerInvariant()}
$cfgHash=(Get-FileHash -LiteralPath $configPath).Hash.ToLowerInvariant()
$runRoot=Join-Path $repo 'results/A'
if($RunDirectory){
    $run=[IO.Path]::GetFullPath((Join-Path $repo $RunDirectory))
    if(-not $run.StartsWith($runRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Run outside results/A'}
    $prior=Get-Content -LiteralPath (Join-Path $run 'run_manifest.json') -Raw | ConvertFrom-Json
    if($prior.config_sha256 -ne $cfgHash -or $prior.top -ne $cfg.top){throw 'Existing run config changed'}
    foreach($f in $inputs){
        if($prior.source_sha256.$f -ne $inputHashes[$f] -or (Get-FileHash -LiteralPath (Join-Path $run ('source/'+$f))).Hash.ToLowerInvariant() -ne $inputHashes[$f]){throw "Existing source changed: $f"}
    }
    if((Get-FileHash -LiteralPath (Join-Path $run 'run_hls.tcl')).Hash -ne (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'run_hls.tcl')).Hash){throw 'Existing Tcl changed'}
    if(Test-Path -LiteralPath (Join-Path $run ($Stage+'_pass.txt'))){throw 'Stage already completed; do not overwrite evidence'}
}else{
    if($Stage -in @('cosim','export')){throw 'Use -RunDirectory pointing to a successful synthesis'}
    $family=if($cfg.top -eq 'gemm_top'){'A0'}else{'A1'}
    $run=Join-Path $runRoot ($family+'/'+$cfg.candidate_id+'_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
    $null=New-Item -ItemType Directory -Path $run
    foreach($f in $inputs){
        $target=Join-Path $run ('source/'+$f)
        $null=New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target)
        Copy-Item -LiteralPath (Join-Path (Join-Path $repo $cfg.source_root) $f) -Destination $target
    }
    Copy-Item -LiteralPath $configPath -Destination (Join-Path $run 'config.json')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run_hls.tcl') -Destination (Join-Path $run 'run_hls.tcl')
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $run 'run_hls.ps1')
    [ordered]@{schema='model_gemm_hls_run_v1';candidate_id=$cfg.candidate_id;top=$cfg.top;config_sha256=$cfgHash;source_sha256=$inputHashes;part=$cfg.part;clock_ns=10;uncertainty_ns=1;address_bits=64;control=$cfg.control;host_start=(Get-Date -Format o);board_status='NOT_RUN';tool='Vitis HLS 2020.2.2'} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $run 'run_manifest.json') -Encoding utf8
}
$alias=$null
$saved=@{}
Write-Output "MODEL_GEMM_RUN=$run"
try{
    $used=@([Environment]::GetLogicalDrives() | ForEach-Object {$_.Substring(0,2)})
    $alias=@('Z:','Y:','X:','W:','V:','U:','T:','S:','R:') | Where-Object {$_ -notin $used} | Select-Object -First 1
    if(-not $alias){throw 'No ASCII drive available'}
    & subst $alias $repo
    if($LASTEXITCODE -ne 0){throw 'ASCII path mapping failed'}
    $aliasRun=Join-Path ($alias+'\') ([IO.Path]::GetRelativePath($repo,$run))
    $temp=Join-Path $aliasRun 'temp'
    $profile=Join-Path $aliasRun 'userdata'
    $null=New-Item -ItemType Directory -Force -Path $temp,(Join-Path $profile 'AppData/Roaming'),(Join-Path $profile 'AppData/Local')
    $overrides=@{MODEL_GEMM_RUN=$aliasRun;MODEL_GEMM_TOP=$cfg.top;MODEL_GEMM_STAGE=$Stage;MODEL_GEMM_DESIGN_FILES=($cfg.design_files -join '|');MODEL_GEMM_TB=$cfg.tb_file;MODEL_GEMM_CSIM_PROFILE=$cfg.csim_profile;MODEL_GEMM_COSIM_PROFILE=$cfg.cosim_profile;MODEL_GEMM_AUTO_PIPELINE=[string]$cfg.automatic_pipeline_loops;MODEL_GEMM_CONTROL=$cfg.control;TEMP=$temp;TMP=$temp;USERPROFILE=$profile;APPDATA=(Join-Path $profile 'AppData/Roaming');LOCALAPPDATA=(Join-Path $profile 'AppData/Local');XILINX_LOCAL_USER_DATA='no';PROCESSOR_ARCHITECTURE='AMD64'}
    foreach($key in $overrides.Keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,$overrides[$key],'Process')}
    Push-Location $aliasRun
    try{& $tool -f (Join-Path $aliasRun 'run_hls.tcl').Replace('\','/') *> (Join-Path $aliasRun ($Stage+'_console.log'));$toolExit=$LASTEXITCODE}finally{Pop-Location}
    $required=if($Stage -eq 'all'){@('csim','csynth','cosim','export')}else{@($Stage)}
    foreach($s in $required){if(-not (Test-Path -LiteralPath (Join-Path $run ($s+'_pass.txt')))){throw "HLS stage $s incomplete (exit $toolExit): $run"}}
    if($toolExit -ne 0){throw "HLS exit $toolExit"}
    Write-Output "MODEL_GEMM_${Stage}_PASS=$run"
}finally{
    foreach($key in $saved.Keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
    if($alias){& subst $alias /D | Out-Null}
}
