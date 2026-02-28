<# 
.SYNOPSIS
    Build the 3MF API reference documentation using Sphinx.

.DESCRIPTION
    Generates HTML documentation from Python docstrings in io_mesh_3mf/api.py.
    Output goes to docs/site/ (committed to the repo so users don't need to
    build).  Intermediate doctrees go to docs/_build/ (gitignored).

    Only contributors who change the API or docstrings need to rebuild.

.EXAMPLE
    .\docs\build.ps1           # Build HTML docs
    .\docs\build.ps1 -Clean    # Clean then rebuild
#>

param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$docsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $docsDir
$cacheDir = Join-Path $docsDir "_build"
$siteDir = Join-Path $docsDir "site"

Push-Location $projectRoot
try {
    if ($Clean) {
        if (Test-Path $cacheDir) {
            Write-Host "Cleaning $cacheDir..." -ForegroundColor Yellow
            Remove-Item -Recurse -Force $cacheDir
        }
        if (Test-Path $siteDir) {
            Write-Host "Cleaning $siteDir..." -ForegroundColor Yellow
            Remove-Item -Recurse -Force $siteDir
        }
    }

    Write-Host "Building HTML docs..." -ForegroundColor Cyan
    sphinx-build -b html -d docs/_build/doctrees docs docs/site -W --keep-going 2>&1

    if ($LASTEXITCODE -eq 0) {
        $indexPath = Join-Path $siteDir "index.html"
        Write-Host "`nDocs built successfully!" -ForegroundColor Green
        Write-Host "Open: $indexPath" -ForegroundColor Gray
    } else {
        Write-Host "`nBuild completed with warnings." -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}