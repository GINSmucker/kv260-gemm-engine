[CmdletBinding()]
param(
    [ValidateSet('opt35a','opt38')][string]$Candidate='opt35a',
    [ValidateSet('csim','csynth','cosim','export','all')][string]$Stage='csim',
    [string]$RunDirectory,
    [Parameter(Mandatory=$true)][string]$HlsTool
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$config=if($Candidate -eq 'opt35a'){'hls/config/opt35a_clear_v1_full.json'}else{'hls/config/opt38_fullk_v1_full.json'}
& (Join-Path $root 'hls/scripts/run_hls.ps1') -Stage $Stage -Config $config -RunDirectory $RunDirectory -HlsTool $HlsTool
