[CmdletBinding()]
param(
    [ValidateSet('inspect','bd','observer','logic','build')][string]$Stage='inspect',
    [ValidateRange(1,8)][int]$Jobs=2,
    [string]$Vivado='D:\Vivado\2026.1\Vivado\bin\vivado.bat'
)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$opt50SystemRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$opt50ManifestPath=Join-Path $opt50SystemRoot 'project_manifest.json'
if(-not (Test-Path -LiteralPath $opt50ManifestPath)){throw 'Prepare a standalone project first; this is only the script source tree.'}
$opt50Manifest=Get-Content -LiteralPath $opt50ManifestPath -Raw | ConvertFrom-Json
if($opt50Manifest.schema -ne 'opt50_system_recipe_v1' -or $opt50Manifest.status -ne 'PREPARED_NOT_RUN' -or $opt50Manifest.tool_version -ne '2026.1'){throw 'Unreviewed project recipe'}
foreach($opt50Entry in $opt50Manifest.files.PSObject.Properties){
    $opt50File=[IO.Path]::GetFullPath((Join-Path $opt50SystemRoot $opt50Entry.Name))
    if(-not $opt50File.StartsWith($opt50SystemRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Recipe path escapes project'}
    if(-not (Test-Path -LiteralPath $opt50File -PathType Leaf) -or (Get-Item -LiteralPath $opt50File).Length -ne $opt50Entry.Value.bytes -or
       (Get-FileHash -LiteralPath $opt50File -Algorithm SHA256).Hash.ToLowerInvariant() -ne $opt50Entry.Value.sha256){throw "Project snapshot changed: $($opt50Entry.Name)"}
}
if(-not (Test-Path -LiteralPath $Vivado -PathType Leaf)){throw 'Specified existing Vivado executable is missing; no installation attempted'}
if($Stage -eq 'inspect'){
    [ordered]@{status='PROJECT_SNAPSHOT_VALID_NO_VIVADO_STARTED';root=$opt50SystemRoot;stage=$Stage;files=@($opt50Manifest.files.PSObject.Properties).Count;clock_hz=125000000;vivado='NOT_RUN';board='NOT_RUN'} | ConvertTo-Json
    return
}
if($opt50SystemRoot -match '[^\x20-\x7e]'){throw 'Vivado project path must be ASCII; use the prepared D:\GEMM\opt50_system copy'}
$opt50BuildRoot=Join-Path $opt50SystemRoot 'build'
$null=New-Item -ItemType Directory -Path $opt50BuildRoot -Force
$opt50RunId=$Stage+'_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff')+'_'+([guid]::NewGuid().ToString('N').Substring(0,8))
$opt50Run=Join-Path $opt50BuildRoot $opt50RunId
if(Test-Path -LiteralPath $opt50Run){throw 'Fresh run directory required'}
$null=New-Item -ItemType Directory -Path $opt50Run
$opt50RecipeSha=(Get-FileHash -LiteralPath $opt50ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$opt50Hasher=[Security.Cryptography.SHA256]::Create()
try{$opt50Fingerprint=[Convert]::ToHexString($opt50Hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($opt50RecipeSha+'|'+$opt50RunId+'|2026.1'))).ToLowerInvariant()}finally{$opt50Hasher.Dispose()}
$opt50Tag0=[Convert]::ToUInt32($opt50Fingerprint.Substring(0,8),16)
$opt50Tag1=[Convert]::ToUInt32($opt50Fingerprint.Substring(8,8),16)
$opt50UuidText=$opt50Fingerprint.Substring(0,8)+'-'+$opt50Fingerprint.Substring(8,4)+'-'+$opt50Fingerprint.Substring(12,4)+'-'+$opt50Fingerprint.Substring(16,4)+'-'+$opt50Fingerprint.Substring(20,12)
[ordered]@{schema='opt50_system_run_v1';status='VIVADO_REQUESTED_NOT_VERIFIED';run_id=$opt50RunId;stage=$Stage;recipe_sha256=$opt50RecipeSha;tool_version='2026.1';clock_hz=125000000;build_fingerprint=$opt50Fingerprint;observer_tags=@($opt50Tag0,$opt50Tag1);metadata_uuid=$opt50UuidText;board='NOT_RUN'} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $opt50Run 'run_manifest.json') -Encoding utf8NoBOM
$opt50SavedTemp=$env:TEMP
$opt50SavedTmp=$env:TMP
try{
    $opt50Temp=Join-Path $opt50Run 'temp'
    $null=New-Item -ItemType Directory -Path $opt50Temp
    $env:TEMP=$opt50Temp
    $env:TMP=$opt50Temp
    Push-Location -LiteralPath $opt50Run
    try{
        & $Vivado -mode batch -nolog -nojournal -source (Join-Path $opt50SystemRoot 'scripts/create_system.tcl') -tclargs $opt50SystemRoot $opt50Run $Stage ('0x'+$opt50Fingerprint.Substring(0,8)) ('0x'+$opt50Fingerprint.Substring(8,8)) $Jobs *> (Join-Path $opt50Run 'vivado_console.log')
        $opt50Exit=$LASTEXITCODE
    }finally{Pop-Location}
    if($opt50Exit -ne 0){throw "Vivado failed ($opt50Exit). Preserve this run: $opt50Run"}
    $opt50Required=if($Stage -eq 'observer'){@('observer_pass.txt')}elseif($Stage -eq 'bd'){@('bd_pass.txt')}elseif($Stage -eq 'logic'){@('bd_pass.txt','board_logic_pass.txt','board_logic_simulate.log','board_logic_elaborate.bat','simulation_model_scope.txt')}else{@('observer_pass.txt','bd_pass.txt','board_logic_pass.txt','board_logic_simulate.log','board_logic_elaborate.bat','simulation_model_scope.txt','build_pass.txt')}
    foreach($opt50Marker in $opt50Required){if(-not (Test-Path -LiteralPath (Join-Path $opt50Run $opt50Marker))){throw "Missing real stage result: $opt50Marker"}}
    [ordered]@{status='VIVADO_STAGE_FINISHED_REQUIRES_REPORT_REVIEW';stage=$Stage;run=$opt50Run;board='NOT_RUN';next=if($Stage -eq 'build'){'Run scripts/verify_system.py against this fresh build before generating metadata'}else{'Review generated project/reports; no hardware loaded'}} | ConvertTo-Json
}finally{
    $env:TEMP=$opt50SavedTemp
    $env:TMP=$opt50SavedTmp
}
