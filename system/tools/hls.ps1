[CmdletBinding()]
param(
    [ValidateSet('opt35a','opt38')][string]$Candidate='opt35a',
    [ValidateSet('csim','csynth','cosim','export','all')][string]$Stage='csim',
    [string]$RunDirectory,
    [Parameter(Mandatory=$true)][string]$HlsTool
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$config=if($Candidate -eq 'opt35a'){'hardware/model_gemm/config/opt35a_clear_v1_full.json'}else{'hardware/model_gemm/config/opt38_fullk_v1_full.json'}
& (Join-Path $root 'hardware/model_gemm/scripts/run_hls.ps1') -Stage $Stage -Config $config -RunDirectory $RunDirectory -HlsTool $HlsTool
