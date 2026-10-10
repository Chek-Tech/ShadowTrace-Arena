[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [string]$Version = '3.9.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$Stage = Join-Path $TempRoot (
    'shadowtrace-code-sentinel-vendor-' + [guid]::NewGuid().ToString('N')
)
$Archive = Join-Path `
    $ProjectRoot "shadowtrace_arena\data\vendor\code-sentinel-$Version.zip"

function Remove-VerifiedStage {
    param([string]$Path)
    $Resolved = [IO.Path]::GetFullPath($Path)
    $Leaf = Split-Path $Resolved -Leaf
    if (-not $Resolved.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not $Leaf.StartsWith('shadowtrace-code-sentinel-vendor-')) {
        throw "Refusing to clean unexpected staging path: $Resolved"
    }
    if (Test-Path -LiteralPath $Resolved) {
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $Stage | Out-Null
try {
    $ExcludedDirectories = @(
        '.pytest_cache',
        '__pycache__',
        (Join-Path $SourceRoot 'ops-lab\tmp'),
        (Join-Path $SourceRoot 'ops-lab\targets'),
        (Join-Path $SourceRoot 'ops-lab\logs'),
        (Join-Path $SourceRoot 'ops-lab\reports'),
        (Join-Path $SourceRoot 'ops-lab\journal'),
        (Join-Path $SourceRoot 'ops-lab\samples'),
        (Join-Path $SourceRoot 'ops-lab\shared')
    )
    & robocopy.exe $SourceRoot $Stage /E /NFL /NDL /NJH /NJS /NP `
        /XD @ExcludedDirectories /XF '*.pyc' '.sentinel.toml' 'tool-index.json'
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }

    $ArchiveParent = Split-Path $Archive -Parent
    if (-not (Test-Path -LiteralPath $ArchiveParent -PathType Container)) {
        New-Item -ItemType Directory -Path $ArchiveParent | Out-Null
    }
    if (Test-Path -LiteralPath $Archive) {
        Remove-Item -LiteralPath $Archive -Force
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $Stage,
        $Archive,
        [IO.Compression.CompressionLevel]::Optimal,
        $false
    )
} finally {
    Remove-VerifiedStage $Stage
}

$Item = Get-Item -LiteralPath $Archive
Write-Host "Created $($Item.FullName) ($($Item.Length) bytes)"
