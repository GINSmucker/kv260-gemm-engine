[CmdletBinding()]
param(
    [ValidateSet('opt35a','opt38')][string]$Candidate='opt35a',
    [switch]$Build,
    [string]$VivadoTool
)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$asset=Join-Path $root "build/assets/ip-rebuild/ip-rebuild/$Candidate"
$archive=Join-Path $asset 'export.zip'
if(-not (Test-Path -LiteralPath $archive -PathType Leaf)){throw 'First acquire and extract the ip-rebuild asset using docs/tools/assets.py'}
$index=Get-Content -LiteralPath (Join-Path (Split-Path -Parent $asset) 'asset_manifest.json') -Raw | ConvertFrom-Json
$item=$index.files.PSObject.Properties["$Candidate/export.zip"].Value
if((Get-FileHash -LiteralPath $archive).Hash.ToLowerInvariant() -ne $item.sha256){throw 'IP archive changed'}
$run=Join-Path $root ('build/platform_'+$Candidate+'_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
if(Test-Path -LiteralPath $run){throw 'Use a new build directory'}
$null=New-Item -ItemType Directory -Path (Join-Path $run 'source/ip')
$zip=[IO.Compression.ZipFile]::OpenRead($archive)
try{
    foreach($entry in $zip.Entries){
        $destination=[IO.Path]::GetFullPath((Join-Path (Join-Path $run 'source/ip') $entry.FullName))
        if(-not $destination.StartsWith((Join-Path $run 'source/ip')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe IP ZIP member'}
    }
}finally{$zip.Dispose()}
[IO.Compression.ZipFile]::ExtractToDirectory($archive,(Join-Path $run 'source/ip'))
$component=Join-Path $run 'source/ip/component.xml'
if(-not (Test-Path -LiteralPath $component)){throw 'Unexpected IP export layout'}
$before=(Get-FileHash -LiteralPath $component).Hash.ToLowerInvariant()
$xml=[IO.File]::ReadAllText($component)
foreach($i in 0..2){
    $old='spirit:id="BUSIFPARAM_VALUE.M_AXI_GMEM'+$i+'.HAS_BURST">0</spirit:value>'
    $new='spirit:id="BUSIFPARAM_VALUE.M_AXI_GMEM'+$i+'.HAS_BURST">1</spirit:value>'
    if([regex]::Matches($xml,[regex]::Escape($old)).Count -ne 1){throw "Unreviewed HAS_BURST for gmem$i"}
    $xml=$xml.Replace($old,$new)
}
[IO.File]::WriteAllText($component,$xml,[Text.UTF8Encoding]::new($false))
$after=(Get-FileHash -LiteralPath $component).Hash.ToLowerInvariant()
$null=New-Item -ItemType Directory -Path (Join-Path $run 'source/observer')
foreach($name in @('f8_status.v','tb_f8_status.sv')){Copy-Item -LiteralPath (Join-Path $root "board/vivado/observer/$name") -Destination (Join-Path $run 'source/observer')}
Copy-Item -LiteralPath (Join-Path $asset 'contracts') -Destination (Join-Path $run 'source/contracts') -Recurse
Copy-Item -LiteralPath (Join-Path $root 'build/assets/ip-rebuild/ip-rebuild/board_files') -Destination (Join-Path $run 'source/board_files') -Recurse
Copy-Item -LiteralPath (Join-Path $root 'board/vivado/create_system.tcl') -Destination (Join-Path $run 'create_system.tcl')
$sha=[Security.Cryptography.SHA256]::Create()
try{$fingerprint=[Convert]::ToHexString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($item.sha256+$after+(Get-FileHash -LiteralPath (Join-Path $run 'create_system.tcl')).Hash))).ToLowerInvariant()}finally{$sha.Dispose()}
$tag0='0x'+$fingerprint.Substring(0,8);$tag1='0x'+$fingerprint.Substring(8,8)
[ordered]@{scope='NEW_PLATFORM_NOT_OLD_A2_OR_READY';candidate=$Candidate;ip_zip_sha256=$item.sha256;component_before=$before;component_after=$after;tags=@($tag0,$tag1);fingerprint=$fingerprint;clock_mhz=100;part='xck26-sfvc784-2LV-c';board_part='xilinx.com:kv260:part0:1.1';build_requested=[bool]$Build;PL_starts=0} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $run 'preparation.json') -Encoding utf8
Write-Output "PLATFORM_PREPARED=$run"
if(-not $Build){return}
if(-not $VivadoTool -or -not (Test-Path -LiteralPath $VivadoTool -PathType Leaf)){throw 'Provide the installed Vivado 2020.2.2 launcher using -VivadoTool'}
$alias=$null;$saved=@{}
try{
    $used=@([Environment]::GetLogicalDrives() | ForEach-Object {$_.Substring(0,2)})
    $alias=@('Z:','Y:','X:','W:','V:') | Where-Object {$_ -notin $used} | Select-Object -First 1
    if(-not $alias){throw 'No ASCII drive alias available'}
    & subst $alias $root
    if($LASTEXITCODE -ne 0){throw 'ASCII mapping failed'}
    $ascii=Join-Path ($alias+'\') ([IO.Path]::GetRelativePath($root,$run))
    $envs=@{MG_RUN=$ascii;MG_IP=(Join-Path $ascii 'source/ip');MG_BOARD=(Join-Path $ascii 'source/board_files');MG_OBSERVER=(Join-Path $ascii 'source/observer');MG_TAG0=$tag0;MG_TAG1=$tag1;MG_BUILD='1';XILINX_LOCAL_USER_DATA='no'}
    foreach($key in $envs.Keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,$envs[$key],'Process')}
    & $VivadoTool -mode batch -source (Join-Path $ascii 'create_system.tcl') *> (Join-Path $run 'vivado.log')
    if($LASTEXITCODE -ne 0){throw 'Vivado failed; preserve the build directory'}
    foreach($file in @('observer_pass.txt','bd_pass.txt','build_pass.txt')){if(-not (Test-Path -LiteralPath (Join-Path $run $file))){throw "Missing $file"}}
}finally{
    foreach($key in $saved.Keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
    if($alias){& subst $alias /D | Out-Null}
}
