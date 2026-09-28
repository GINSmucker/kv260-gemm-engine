[CmdletBinding()]
param([switch]$Build)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ipRoot = Join-Path $root 'results\external_ip_review_20260927\release'
$ipSha = (Get-FileHash -LiteralPath (Join-Path $ipRoot 'gemm_top.zip') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ipSha -ne 'f40f4178d66ae47f4362544ded71557a3f783b615ec4ba771804d816d781b521') {throw 'Original IP hash differs'}
$runName = 'f8_system_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff')
$runDir = Join-Path $root "results\c_integration\$runName"
$null = New-Item -ItemType Directory -Path (Join-Path $runDir 'source\hardware\kv260_f8')
$sourceHashes = [ordered]@{}
foreach ($relative in @('hardware/kv260_f8/f8_status.v', 'hardware/kv260_f8/tb_f8_status.sv', 'hardware/kv260_f8/create_system.tcl', 'scripts/run_f8_system.ps1')) {
    $source = Join-Path $root $relative
    $dest = Join-Path (Join-Path $runDir 'source') $relative
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest)
    Copy-Item -LiteralPath $source -Destination $dest
    $sourceHashes[$relative] = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
}
Copy-Item -LiteralPath (Join-Path $root 'board\hw\board_files') -Destination (Join-Path $runDir 'source\board_files') -Recurse
Copy-Item -LiteralPath (Join-Path $ipRoot 'ip') -Destination (Join-Path $runDir 'source\ip') -Recurse
# The author package declares no bursts, although its unchanged RTL emits INCR
# bursts. Correct only these three metadata values in the isolated build copy.
$component = Join-Path $runDir 'source\ip\component.xml'
$xml = [IO.File]::ReadAllText($component)
$before = (Get-FileHash -LiteralPath $component -Algorithm SHA256).Hash.ToLowerInvariant()
foreach ($index in 0..2) {
    $old = 'spirit:id="BUSIFPARAM_VALUE.M_AXI_GMEM' + $index + '.HAS_BURST">0</spirit:value>'
    $new = 'spirit:id="BUSIFPARAM_VALUE.M_AXI_GMEM' + $index + '.HAS_BURST">1</spirit:value>'
    if (($xml.Split($old).Count - 1) -ne 1) {throw "Expected one HAS_BURST for port $index"}
    $xml = $xml.Replace($old, $new)
}
[IO.File]::WriteAllText($component, $xml, [Text.UTF8Encoding]::new($false))
$after = (Get-FileHash -LiteralPath $component -Algorithm SHA256).Hash.ToLowerInvariant()
$identity = $ipSha + $after + ($sourceHashes.Values -join '')
$sha = [Security.Cryptography.SHA256]::Create()
try {$buildHash = [Convert]::ToHexString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($identity))).ToLowerInvariant()} finally {$sha.Dispose()}
$tag0 = '0x' + $buildHash.Substring(0,8)
$tag1 = '0x' + $buildHash.Substring(8,8)
[ordered]@{
    schema = 'f8_system_build_v1'; run_name = $runName; host_time = (Get-Date -Format o)
    git_head = ((& git -C $root rev-parse HEAD) -join '')
    target_part = 'xck26-sfvc784-2LV-c'; board_part = 'xilinx.com:kv260:part0:1.1'
    tool = 'Vivado 2020.2.2'; clock_constraint_mhz = 100
    original_ip_sha256 = $ipSha; source_sha256 = $sourceHashes
    metadata_change = @{file='component.xml'; before_sha256=$before; after_sha256=$after; change='m_axi_gmem0/1/2 HAS_BURST 0 -> 1; RTL unchanged'}
    build_fingerprint = $buildHash; build_tag0 = $tag0; build_tag1 = $tag1
    control_base = '0xA0000000'; status_base = '0xA0010000'; build_requested = [bool]$Build
    board_status = 'NOT_RUN'
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runDir 'run_manifest.json') -Encoding utf8
$drive = $null
$aliasCreated = $false
$saved = @{}
Write-Output "F8_RUN_DIR=$runDir"
try {
    $used = @([Environment]::GetLogicalDrives() | ForEach-Object {$_.Substring(0,2)})
    $drive = @('Z:','Y:','X:','W:','V:') | Where-Object {$_ -notin $used} | Select-Object -First 1
    if (-not $drive) {throw 'No free ASCII alias'}
    & subst $drive $root
    if ($LASTEXITCODE -ne 0) {throw 'subst failed'}
    $aliasCreated = $true
    $aliasRun = Join-Path ($drive + '\') "results\c_integration\$runName"
    $null = New-Item -ItemType Directory -Path (Join-Path $aliasRun 'temp')
    $overrides = @{
        F8_RUN_DIR=$aliasRun; F8_BOARD_REPO=(Join-Path $aliasRun 'source\board_files')
        F8_IP_DIR=(Join-Path $aliasRun 'source\ip'); F8_SOURCE_DIR=(Join-Path $aliasRun 'source\hardware\kv260_f8')
        F8_TAG0=$tag0; F8_TAG1=$tag1; F8_BUILD=$(if($Build){'1'}else{'0'})
        TEMP=(Join-Path $aliasRun 'temp'); TMP=(Join-Path $aliasRun 'temp')
        XILINX_LOCAL_USER_DATA='no'; PROCESSOR_ARCHITECTURE='AMD64'
    }
    foreach ($key in $overrides.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key,'Process')
        [Environment]::SetEnvironmentVariable($key,$overrides[$key],'Process')
    }
    Push-Location $aliasRun
    try {
        & 'C:\Xilinx\Vivado\2020.2\bin\vivado.bat' -mode batch -nolog -nojournal -source ((Join-Path $aliasRun 'source\hardware\kv260_f8\create_system.tcl').Replace('\','/')) *> (Join-Path $runDir 'vivado_console.log')
        $toolExit = $LASTEXITCODE
    } finally {Pop-Location}
    Get-Content -LiteralPath (Join-Path $runDir 'vivado_console.log') -Tail 28
    if ($toolExit -ne 0) {throw "Vivado exit=$toolExit; $runDir"}
    foreach ($name in @('status_rtl_pass.txt','bd_pass.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $runDir $name))) {throw "Missing $name"}
    }
    if ($Build) {
        if (-not (Test-Path -LiteralPath (Join-Path $runDir 'build_pass.txt'))) {throw 'Missing build PASS marker'}
        $hashes = [ordered]@{}
        foreach ($ext in @('bit','bin','hwh','xsa')) {
            $item = Join-Path $runDir "artifacts\kv260_f8.$ext"
            $hashes["kv260_f8.$ext"] = @{bytes=(Get-Item -LiteralPath $item).Length; sha256=(Get-FileHash -LiteralPath $item -Algorithm SHA256).Hash.ToLowerInvariant()}
        }
        $hashes | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runDir 'artifact_sha256.json') -Encoding utf8
    }
    Write-Output "F8_LOCAL_PASS=$runDir"
} finally {
    foreach ($key in $saved.Keys) {[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
    if ($aliasCreated) {& subst $drive /D | Out-Null}
}
