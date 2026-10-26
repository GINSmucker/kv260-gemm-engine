[CmdletBinding()]
param(
    [string]$VitisRoot = 'D:\Vivado\2026.1\Vitis'
)

# Native C++ only: creates/updates .build, not an HLS run or an IP package.
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRoot = Join-Path $projectRoot '.build'
$compiler = Join-Path $VitisRoot 'win64\tools\clang-16\bin\clang++.exe'
$mingw = Join-Path $VitisRoot 'tps\mingw\10.0.0\win64.o\nt'
$headers = Join-Path $VitisRoot 'include'
$caseRoot = Join-Path $projectRoot 'tb\axi_cases'
foreach ($required in @($compiler, $mingw, $headers, (Join-Path $caseRoot 'manifest.json'))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required tool or staged golden package missing: $required"
    }
}

$savedTaskPath = $env:PATH
try {
    $env:PATH = (Join-Path $VitisRoot 'lib\win64.o') + ';' +
        (Join-Path $mingw 'bin') + ';' + (Join-Path $VitisRoot 'win64\lib\csim') + ';' + $savedTaskPath
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    $executable = Join-Path $buildRoot 'testbench.exe'
    $compileArguments = @('--target=x86_64-w64-windows-gnu', "--sysroot=$mingw",
        '-std=c++11', '-O2', '-Wno-unknown-pragmas', '-ferror-limit=8', '-I', $headers,
        (Join-Path $projectRoot 'src\model_gemm_top.cpp'),
        (Join-Path $projectRoot 'tb\testbench.cpp'), '-pthread', '-Wl,--stack,16777216',
        '-o', $executable)
    Write-Host 'Compiling native C++ testbench (not Vitis HLS CSim)...'
    & $compiler @compileArguments
    if ($LASTEXITCODE -ne 0) { throw "Native compilation failed: $LASTEXITCODE" }

    foreach ($profile in @('full', 'rtl', 'golden')) {
        $runRoot = Join-Path $buildRoot ('run_' + $profile)
        New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
        Push-Location -LiteralPath $runRoot
        try {
            Write-Host "Running NATIVE profile=$profile (rtl is only a case-set name)..."
            if ($profile -eq 'golden') {
                # ASCII relative path also works if the project parent contains Chinese.
                & $executable golden '../../tb/axi_cases'
            } else {
                & $executable $profile
            }
            if ($LASTEXITCODE -ne 0) { throw "Native profile $profile failed: $LASTEXITCODE" }
        } finally {
            Pop-Location
        }
    }
    Write-Host 'PASS: native integer tests only; HLS/Burst/RTL/timing/board remain unverified.'
} finally {
    $env:PATH = $savedTaskPath
}
