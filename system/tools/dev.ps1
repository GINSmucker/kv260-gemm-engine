#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$Python,
    [string]$Cxx,
    [switch]$InstallDependencies,
    [switch]$RefreshSourceLock,
    [switch]$Cpp,
    [switch]$Node,
    [switch]$Model,
    [switch]$RuntimeV3,
    [switch]$BV3
)
$ErrorActionPreference = 'Stop'
$systemRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $systemRoot '.venv\Scripts\python.exe'
$portableCxx = Join-Path $systemRoot 'assets\toolchain\llvm-mingw-20261006-ucrt-x86_64\bin\clang++.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not $Python) { throw 'Select an existing Python 3.12 executable with -Python; no system installation is performed.' }
    & $Python -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
    if ($LASTEXITCODE) { throw 'Python 3.12 required' }
    & $Python -m venv (Join-Path $systemRoot '.venv')
    if ($LASTEXITCODE) { throw 'Project environment creation failed' }
}
& $venvPython -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version; print(sys.executable); print(sys.version)'
if ($LASTEXITCODE) { throw 'Invalid project Python environment' }
if ($InstallDependencies) {
    & $venvPython -m pip install --timeout 120 -r (Join-Path $systemRoot 'model\b\requirements-arm.txt')
    if ($LASTEXITCODE) { throw 'Pinned dependency install failed; preserve the error and inspect its cause' }
}
$previousDirectory = Get-Location
try {
    Set-Location -LiteralPath $systemRoot
    if ($RefreshSourceLock) {
        & $venvPython -B tools/source_manifest.py --output DEVELOPMENT_SOURCE.lock.json
        if ($LASTEXITCODE) { throw 'Source lock generation failed' }
    }
    if ($Cpp -or $RuntimeV3 -or $BV3) {
        if (-not $Cxx -and (Test-Path -LiteralPath $portableCxx)) { $Cxx = $portableCxx }
        if (-not $Cxx) { throw 'Select a C++17 compiler with -Cxx; no automatic toolchain installation is performed.' }
    }
    $checkArguments = @('-B', 'tools/check.py')
    if ($Cpp) { $checkArguments += @('--cpp', '--cxx', $Cxx, '--cxx-flag=-static') }
    if ($Node) { $checkArguments += '--node' }
    if ($Model) { $checkArguments += @('--model-bundle', 'assets/model/B_MODEL_BUNDLE_V1') }
    & $venvPython @checkArguments
    if ($LASTEXITCODE) { throw 'Local handoff check failed; G1 checks can still be run separately without -Model.' }
    if ($RuntimeV3) {
        & $venvPython -B tools/check_runtime_v3.py --cxx $Cxx --cxx-flag=-static
        if ($LASTEXITCODE) { throw 'G1 public core check failed' }
    }
    if ($BV3) {
        & $venvPython -B tools/check_b_v3.py --bundle assets/model/B_MODEL_BUNDLE_V1 --cxx $Cxx
        if ($LASTEXITCODE) { throw 'B same-machine G1 diagnostic check failed' }
    }
} finally {
    Set-Location -LiteralPath $previousDirectory
}
