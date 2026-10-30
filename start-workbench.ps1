#requires -Version 7.0
<#
.SYNOPSIS
Start the local workbench; a board endpoint must be explicitly supplied.
#>
[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$Port = 4178,
    [string]$BoardUrl
)
$ErrorActionPreference = 'Stop'
$nodeCommand = Get-Command node -ErrorAction Stop
$nodeMajor = [int]((& $nodeCommand.Source --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 24) { throw 'Node.js 24 or newer is required.' }
if ($BoardUrl) {
    $endpoint = [uri]$BoardUrl
    if ($endpoint.Scheme -ne 'http' -or $endpoint.Host -notin @('localhost','127.0.0.1')) {
        throw 'Use the HTTP loopback endpoint of the operator-provided board tunnel.'
    }
}
$oldPort = $env:KV260_UI_PORT
$oldBoard = $env:KV260_UI_BOARD_URL
try {
    $env:KV260_UI_PORT = [string]$Port
    $env:KV260_UI_BOARD_URL = $BoardUrl
    & $nodeCommand.Source (Join-Path $PSScriptRoot 'board/ui/server.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Workbench stopped with an error; inspect the terminal output.' }
} finally {
    $env:KV260_UI_PORT = $oldPort
    $env:KV260_UI_BOARD_URL = $oldBoard
}
