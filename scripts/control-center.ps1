Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$PythonPath = if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    $VenvPython
} else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) { $PythonCommand.Source } else { $null }
}
if (-not $PythonPath) {
    throw 'Python 3.11 or newer is required.'
}
& $PythonPath -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.11 or newer is required.'
}
& $PythonPath -c "import tkinter"
if ($LASTEXITCODE -ne 0) {
    throw 'The selected Python installation does not include Tkinter.'
}
Push-Location $ProjectRoot
try {
    & $PythonPath control_center.py
} finally {
    Pop-Location
}
