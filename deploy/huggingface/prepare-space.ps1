param(
    [string]$OutputDir = "hf-space"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$out = Join-Path $root $OutputDir

if (Test-Path $out) {
    Remove-Item -LiteralPath $out -Recurse -Force
}

New-Item -ItemType Directory -Path $out | Out-Null

Copy-Item -LiteralPath (Join-Path $root "backend") -Destination (Join-Path $out "backend") -Recurse
Copy-Item -LiteralPath (Join-Path $root "frontend") -Destination (Join-Path $out "frontend") -Recurse
Copy-Item -LiteralPath (Join-Path $root "requirements-lite.txt") -Destination (Join-Path $out "requirements-lite.txt")
Copy-Item -LiteralPath (Join-Path $root "Dockerfile.free") -Destination (Join-Path $out "Dockerfile")
Copy-Item -LiteralPath (Join-Path $root "Dockerfile.free.dockerignore") -Destination (Join-Path $out ".dockerignore")
Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination (Join-Path $out ".env.example")
Copy-Item -LiteralPath (Join-Path $root "deploy\huggingface\README.template.md") -Destination (Join-Path $out "README.md")

Write-Host "Prepared Hugging Face Space files at: $out"
Write-Host "Push this folder to your Hugging Face Space repository."
