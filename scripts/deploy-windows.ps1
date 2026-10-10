[CmdletBinding()]
param(
    [switch]$SkipCodex,
    [switch]$SkipCodeSentinel,
    [switch]$NoLaunch,
    [switch]$KeepHooksActive,
    [ValidateSet('none', 'core', 'full')]
    [string]$Toolchain = 'full',
    [switch]$NoPackageManagerBootstrap,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvRoot = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvRoot 'Scripts\python.exe'

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-PythonCandidate {
    param(
        [string]$Executable,
        [string[]]$PrefixArgs = @()
    )
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf) -and
        -not (Get-Command $Executable -ErrorAction SilentlyContinue)) {
        return $false
    }
    try {
        & $Executable @PrefixArgs -c `
            'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' `
            *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-SystemPython {
    $Candidates = New-Object System.Collections.Generic.List[object]
    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Launcher) {
        $Candidates.Add([pscustomobject]@{
            Executable = $Launcher.Source
            PrefixArgs = @('-3')
        })
    }

    foreach ($Name in @('python.exe', 'python3.exe')) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) {
            $Candidates.Add([pscustomobject]@{
                Executable = $Command.Source
                PrefixArgs = @()
            })
        }
    }

    $SearchRoots = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:ProgramFiles 'Python')
    )
    foreach ($Root in $SearchRoots) {
        if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $Candidates.Add([pscustomobject]@{
                    Executable = Join-Path $_.FullName 'python.exe'
                    PrefixArgs = @()
                })
            }
    }

    foreach ($Candidate in $Candidates) {
        $PrefixArgs = [string[]]$Candidate.PrefixArgs
        if (Test-PythonCandidate $Candidate.Executable $PrefixArgs) {
            return $Candidate
        }
    }
    return $null
}

function Install-PythonWithWinget {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw ('Python 3.11+ was not found and winget is unavailable. ' +
            'Install Python from https://www.python.org/downloads/windows/ and retry.')
    }
    Write-Step 'Installing Python 3.12 with winget'
    & $Winget.Source install --id Python.Python.3.12 --exact --scope user `
        --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install Python (exit code $LASTEXITCODE)."
    }
}

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Label
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed (exit code $LASTEXITCODE)."
    }
}

function Test-LoopbackProxyIsDead {
    foreach ($Name in @('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY')) {
        $Value = [Environment]::GetEnvironmentVariable($Name, 'Process')
        if (-not $Value) {
            continue
        }
        $Uri = $null
        if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$Uri)) {
            continue
        }
        if ($Uri.Host -notin @('127.0.0.1', 'localhost', '::1')) {
            continue
        }
        $Port = if ($Uri.IsDefaultPort) {
            if ($Uri.Scheme -eq 'https') { 443 } else { 80 }
        } else {
            $Uri.Port
        }
        $Client = New-Object System.Net.Sockets.TcpClient
        try {
            $Pending = $Client.BeginConnect($Uri.Host, $Port, $null, $null)
            if (-not $Pending.AsyncWaitHandle.WaitOne(750)) {
                return $true
            }
            $Client.EndConnect($Pending)
        } catch {
            return $true
        } finally {
            $Client.Close()
        }
    }
    return $false
}

function Install-ProjectPackage {
    $Arguments = @(
        '-m', 'pip', 'install', '--disable-pip-version-check',
        '--editable', $ProjectRoot
    )
    if (-not (Test-LoopbackProxyIsDead)) {
        Invoke-Checked $VenvPython $Arguments 'Package installation'
        return
    }

    Write-Warning 'The configured loopback proxy is not running; pip will use a direct connection.'
    $ProxyNames = @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')
    $ProxyBackup = @{}
    foreach ($Name in $ProxyNames) {
        $ProxyBackup[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
        [Environment]::SetEnvironmentVariable($Name, $null, 'Process')
    }
    try {
        Invoke-Checked $VenvPython $Arguments 'Package installation'
    } finally {
        foreach ($Name in $ProxyNames) {
            [Environment]::SetEnvironmentVariable(
                $Name,
                $ProxyBackup[$Name],
                'Process'
            )
        }
    }
}

try {
    Write-Step 'Checking Python 3.11 or newer'
    $VenvReady = Test-PythonCandidate $VenvPython
    $BasePython = $null
    if (-not $VenvReady) {
        $BasePython = Find-SystemPython
        if (-not $BasePython -and -not $CheckOnly) {
            Install-PythonWithWinget
            $BasePython = Find-SystemPython
        }
        if (-not $BasePython) {
            throw 'No usable Python 3.11+ installation was found.'
        }
    }

    if ($CheckOnly) {
        $Selected = if ($VenvReady) { $VenvPython } else { $BasePython.Executable }
        Write-Host "Python ready: $Selected"
        Write-Host "Project root: $ProjectRoot"
        Write-Host "Code Sentinel toolchain profile: $Toolchain"
        Write-Host 'Check completed; no files were changed.'
        exit 0
    }

    if (-not $VenvReady) {
        if (Test-Path -LiteralPath $VenvRoot) {
            $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $Backup = "$VenvRoot.broken-$Stamp"
            Write-Host "Moving unusable environment to: $Backup" -ForegroundColor Yellow
            Move-Item -LiteralPath $VenvRoot -Destination $Backup
        }
        Write-Step 'Creating the project virtual environment'
        $PrefixArgs = [string[]]$BasePython.PrefixArgs
        & $BasePython.Executable @PrefixArgs -m venv $VenvRoot
        if ($LASTEXITCODE -ne 0 -or
            -not (Test-PythonCandidate $VenvPython)) {
            throw 'Virtual environment creation failed.'
        }
    } else {
        Write-Host "Reusing virtual environment: $VenvRoot"
    }

    Write-Step 'Installing ShadowTrace Arena and runtime dependencies'
    Install-ProjectPackage

    Write-Step 'Running pre-deployment health checks'
    Invoke-Checked $VenvPython @(
        '-m', 'shadowtrace_arena', 'doctor'
    ) 'Health check'

    if (-not $SkipCodex) {
        $DiscoveryText = & $VenvPython -m shadowtrace_arena discover --json
        if ($LASTEXITCODE -ne 0) {
            throw 'Codex discovery failed.'
        }
        $Discovery = ($DiscoveryText -join "`n") | ConvertFrom-Json
        if ([int]$Discovery.count -gt 0) {
            Write-Step "Deploying integrations to $($Discovery.count) Codex configuration(s)"
            $InstallArgs = @('-m', 'shadowtrace_arena', 'install', '--all')
            if ($KeepHooksActive) {
                $InstallArgs += '--skip-hooks-isolation'
            }
            Invoke-Checked $VenvPython $InstallArgs 'Codex integration deployment'
        } else {
            Write-Warning 'Codex config.toml was not found; integration deployment was skipped.'
        }
    } else {
        Write-Host 'Codex integration deployment skipped by option.'
    }

    if (-not $SkipCodeSentinel) {
        Write-Step "Deploying Code Sentinel and the $Toolchain security toolchain"
        $ToolchainArgs = @(
            '-m', 'shadowtrace_arena', 'toolchain', '--profile', $Toolchain
        )
        if ($SkipCodex) {
            $ToolchainArgs += '--skip-codex'
        }
        if ($NoPackageManagerBootstrap) {
            $ToolchainArgs += '--no-package-manager-bootstrap'
        }
        Invoke-Checked $VenvPython $ToolchainArgs 'Code Sentinel deployment'
    } else {
        Write-Host 'Code Sentinel deployment skipped by option.'
    }

    Write-Step 'Running final health checks'
    Invoke-Checked $VenvPython @(
        '-m', 'shadowtrace_arena', 'doctor'
    ) 'Final health check'

    $TkAvailable = $true
    & $VenvPython -c 'import tkinter' *> $null
    if ($LASTEXITCODE -ne 0) {
        $TkAvailable = $false
        Write-Warning 'Tkinter is unavailable. CLI deployment is complete, but the GUI cannot start.'
    }

    Write-Host "`nShadowTrace Arena deployment completed." -ForegroundColor Green
    Write-Host "CLI: $VenvPython -m shadowtrace_arena --help"

    if (-not $NoLaunch -and $TkAvailable) {
        Write-Step 'Opening the control center'
        Push-Location $ProjectRoot
        try {
            & $VenvPython (Join-Path $ProjectRoot 'control_center.py')
        } finally {
            Pop-Location
        }
    }
} catch {
    Write-Host "`n[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
