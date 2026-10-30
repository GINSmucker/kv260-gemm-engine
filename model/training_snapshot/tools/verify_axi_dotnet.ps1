param([string]$Root=(Split-Path -Parent $PSScriptRoot), [string]$OutputPath='')
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'VerifyAxi.cs')
$timer=[System.Diagnostics.Stopwatch]::StartNew()
$calls=[BRelease.VerifyAxi]::Run($Root)
$result=[ordered]@{status='PASS_INDEPENDENT_CSHARP_BYTE_DECODER'; calls=$calls; seconds=$timer.Elapsed.TotalSeconds; hardware_accessed=$false; HLS_RTL_board='NOT_RUN'}
$json=$result | ConvertTo-Json
if ($OutputPath) { [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($OutputPath),$json,[System.Text.UTF8Encoding]::new($false)) }
$json
