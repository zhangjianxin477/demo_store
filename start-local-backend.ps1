$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"

$PythonCandidates = @(
    (Join-Path $Root ".venv-local\Scripts\python.exe"),
    (Join-Path $Root "venv\Scripts\python.exe"),
    (Join-Path (Split-Path -Parent (Split-Path -Parent $Root)) ".venv\Scripts\python.exe"),
    "D:\Python\python.exe"
)
$Python = $null
foreach ($candidate in $PythonCandidates) {
    if (-not (Test-Path $candidate)) { continue }
    try {
        $probe = (& $candidate -c "import uvicorn; print('KNOWLEDGE_HUB_PYTHON_OK')" 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $probe -match "KNOWLEDGE_HUB_PYTHON_OK") {
            $Python = $candidate
            break
        }
    } catch { }
}

$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"

if (-not $Python) {
    Write-Host "Missing .venv-local. Run this first:"
    Write-Host "  C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m venv .venv-local"
    Write-Host "  .\.venv-local\Scripts\python.exe -m pip install --no-cache-dir -r requirements-lite.txt"
    exit 1
}

Push-Location $Backend
try {
    # Bind all local interfaces so both localhost (including IPv6 resolution)
    # and 127.0.0.1 reach the same latest backend process.
    & $Python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
}
finally {
    Pop-Location
}
