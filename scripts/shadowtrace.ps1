Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$PythonPath = if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    $VenvPython
} else {
    'python'
}
Push-Location $ProjectRoot
try {
    & $PythonPath -m shadowtrace_arena @args
} finally {
    Pop-Location
}
