[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^f8_system_\d{8}_\d{6}_\d{3}$')][string]$SystemRunName)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$root=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$build=Join-Path $root "results\c_integration\$SystemRunName"
if (-not (Test-Path -LiteralPath (Join-Path $build 'build_pass.txt'))) {throw 'Passing F8 build required'}
$identity=Get-Content -LiteralPath (Join-Path $build 'run_manifest.json') -Raw | ConvertFrom-Json
foreach($entry in $identity.source_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $root $entry.Name)).Hash.ToLowerInvariant() -ne $entry.Value) {throw "Hardware build source changed: $($entry.Name)"}
}
$hashes=Get-Content -LiteralPath (Join-Path $build 'artifact_sha256.json') -Raw | ConvertFrom-Json
foreach($entry in $hashes.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $build "artifacts\$($entry.Name)")).Hash.ToLowerInvariant() -ne $entry.Value.sha256) {throw 'Build artifact changed'}
}
$name='f8_bundle_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff')
$out=Join-Path $root "results\c_integration\$name"
$null=New-Item -ItemType Directory -Path $out
foreach($ext in @('bit','hwh','xsa')) {
    Copy-Item -LiteralPath (Join-Path $build "artifacts\kv260_f8.$ext") -Destination $out
}
Copy-Item -LiteralPath (Join-Path $build 'run_manifest.json') -Destination (Join-Path $out 'hardware_build.json')
Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $out 'package_source.ps1')
@'
all:
{
    [destination_device=pl] kv260_f8.bit
}
'@ | Set-Content -LiteralPath (Join-Path $out 'bitstream.bif') -Encoding ascii
$drive=$null
$saved=[Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE','Process')
try {
    $used=@([Environment]::GetLogicalDrives() | ForEach-Object {$_.Substring(0,2)})
    $drive=@('Z:','Y:','X:','W:','V:') | Where-Object {$_ -notin $used} | Select-Object -First 1
    if(-not $drive){throw 'No free ASCII drive'}
    & subst $drive $out
    if($LASTEXITCODE -ne 0){throw 'Drive alias failed'}
    [Environment]::SetEnvironmentVariable('PROCESSOR_ARCHITECTURE','AMD64','Process')
    Push-Location ($drive+'\')
    try {& 'C:\Xilinx\Vivado\2020.2\bin\bootgen.bat' -arch zynqmp -image bitstream.bif -process_bitstream bin -w off *> (Join-Path $out 'bootgen.log'); $code=$LASTEXITCODE} finally {Pop-Location}
    if($code -ne 0){throw 'Bootgen failed'}
} finally {
    [Environment]::SetEnvironmentVariable('PROCESSOR_ARCHITECTURE',$saved,'Process')
    if($drive){& subst $drive /D | Out-Null}
}
$raw=[IO.File]::ReadAllBytes((Join-Path $build 'artifacts\kv260_f8.bin'))
$bin=[IO.File]::ReadAllBytes((Join-Path $out 'kv260_f8.bit.bin'))
Add-Type -TypeDefinition @'
public static class F8WordOrder {
 public static bool Matches(byte[] a, byte[] b) {
  if(a.Length!=b.Length || a.Length%4!=0) return false;
  for(int i=0;i<a.Length;i+=4) for(int j=0;j<4;j++) if(a[i+j]!=b[i+3-j]) return false;
  return true;
 }
}
'@
if(-not [F8WordOrder]::Matches($raw,$bin)){throw 'Bootgen word-order validation failed'}
Move-Item -LiteralPath (Join-Path $out 'kv260_f8.bit.bin') -Destination (Join-Path $out 'kv260-f8.bit.bin')
$files=@('board/__init__.py','board/backend.py','board/cases.py','board/cli.py','board/f8_host.py','board/f8_xrt.py','board/f8_backend.py','board/f8_smoke.py','board/native/f8_xrt_service.cpp','board/tools/run_f8_board_validation.py','board/tools/f8_read_ps_widths.py','hardware/kv260_f8/capability.json','hardware/kv260_f8/kv260_f8.dts','hardware/kv260_f8/make_metadata.py','hardware/kv260_f8/f8_status.v','hardware/kv260_f8/create_system.tcl','hardware/kv260_f8/tb_f8_status.sv')
foreach($rel in $files) {
    $dest=Join-Path $out $rel
    $null=New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest)
    Copy-Item -LiteralPath (Join-Path $root $rel) -Destination $dest
}
# Native binary and XRT metadata were compiled/parsed with the installed ARM SDK.
$scp='C:\Windows\System32\OpenSSH\scp.exe'
foreach($file in @('f8_xrt_service','kv260_f8_metadata.xclbin','xrt.ini','kv260_f8.dtbo')) {
    & $scp "kv260:/home/ubuntu/fpga_project/deploy/f8_runtime_20260927/$file" (Join-Path $out $file)
    if($LASTEXITCODE -ne 0){throw "Could not retrieve $file"}
}
Move-Item -LiteralPath (Join-Path $out 'kv260_f8.dtbo') -Destination (Join-Path $out 'kv260-f8.dtbo')
$inspection=& 'C:\Windows\System32\OpenSSH\ssh.exe' -o BatchMode=yes kv260 'cd /home/ubuntu/fpga_project/deploy/f8_runtime_20260927 && ./f8_xrt_service --inspect kv260_f8_metadata.xclbin'
if($LASTEXITCODE -ne 0 -or ($inspection -join "`n") -notmatch 'F8_METADATA_PARSE_PASS uuid=([0-9a-f-]{36})'){throw 'Metadata UUID parse failed'}
$metadataUuid=$Matches[1]
$inspection | Set-Content -LiteralPath (Join-Path $out 'metadata_inspect.txt') -Encoding ascii
'{"shell_type":"PL_FLAT","num_slots":"1"}' | Set-Content -LiteralPath (Join-Path $out 'shell.json') -Encoding ascii
$mapped=[ordered]@{}
foreach($item in Get-ChildItem -LiteralPath $out -File -Recurse) {
    $rel=[IO.Path]::GetRelativePath($out,$item.FullName).Replace('\','/')
    $mapped[$rel]=(Get-FileHash -LiteralPath $item.FullName).Hash.ToLowerInvariant()
}
[ordered]@{
    schema='f8_runtime_bundle_v1'; bundle=$name; host_time=(Get-Date -Format o)
    source_build=$SystemRunName; build_fingerprint=$identity.build_fingerprint
    hardware_build_tags=@([Convert]::ToUInt32($identity.build_tag0.Substring(2),16),[Convert]::ToUInt32($identity.build_tag1.Substring(2),16))
    xclbin_uuid=$metadataUuid
    capability_sha256=$mapped['hardware/kv260_f8/capability.json']
    bootgen_word_swap_verified=$true; board_execution='NOT_RUN'; files=$mapped
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $out 'bundle_manifest.json') -Encoding utf8
Write-Output "F8_BUNDLE=$out"
