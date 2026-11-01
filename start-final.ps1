#requires -Version 7.0
<#
.SYNOPSIS
Start the local OPT50 final-results page from saved board evidence.
#>
[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 4179)
$ErrorActionPreference = 'Stop'
$nodeCommand = Get-Command node -ErrorAction Stop
if ([int]((& $nodeCommand.Source --version).TrimStart('v').Split('.')[0]) -lt 24) {
    throw 'Node.js 24 or newer is required.'
}
$previousPort = $env:KV260_FINAL_PORT
try {
    $env:KV260_FINAL_PORT = [string]$Port
    & $nodeCommand.Source (Join-Path $PSScriptRoot 'board/ui/final-server.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Final-results server stopped with an error.' }
} finally {
    $env:KV260_FINAL_PORT = $previousPort
}
