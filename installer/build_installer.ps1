<#
.SYNOPSIS
    Build the cyberpunk-styled Windows installer for PS5 Image Forge.

.DESCRIPTION
    1. Reads the version from pyproject.toml.
    2. Builds the single-file exe with PyInstaller if it is missing (or -Build).
    3. Regenerates the branding art (installer/make_assets.py, needs Pillow).
    4. Locates makensis and compiles installer/installer.nsi.

    The finished setup lands in dist\PS5-Image-Forge-Setup-<version>.exe.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
    powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1 -Build
#>
[CmdletBinding()]
param(
    # Force a fresh PyInstaller build even if dist\PS5-Image-Forge.exe exists.
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
# We check $LASTEXITCODE explicitly; don't let native stderr/exit auto-throw.
$PSNativeCommandUseErrorActionPreference = $false

$InstallerDir = $PSScriptRoot
$RepoRoot     = Split-Path $InstallerDir -Parent
$DistDir      = Join-Path $RepoRoot 'dist'
$AppExe       = 'PS5-Image-Forge.exe'
$SrcExe       = Join-Path $DistDir $AppExe

# --- version from pyproject.toml ------------------------------------------
$pyproject = Join-Path $RepoRoot 'pyproject.toml'
$verMatch  = Select-String -Path $pyproject -Pattern '^\s*version\s*=\s*"([^"]+)"' |
             Select-Object -First 1
if (-not $verMatch) { throw "Could not read version from $pyproject" }
$Version = $verMatch.Matches[0].Groups[1].Value
Write-Host "PS5 Image Forge $Version" -ForegroundColor Cyan

# --- Python (prefer the repo venv) ----------------------------------------
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

# --- 1. build the exe if needed -------------------------------------------
if ($Build -or -not (Test-Path $SrcExe)) {
    Write-Host '==> Building single-file exe with PyInstaller' -ForegroundColor Yellow
    $spec = Join-Path $RepoRoot 'PS5-Image-Forge.spec'
    Push-Location $RepoRoot
    try {
        if (Test-Path $spec) {
            & $Python -m PyInstaller --noconfirm $spec
        } else {
            & $Python -m PyInstaller --noconfirm --onefile --windowed --name PS5-Image-Forge `
                --collect-submodules mkpfs --collect-all webview `
                --add-data 'src/ps5_image_forge/webui;ps5_image_forge/webui' `
                --add-data 'src/ps5_image_forge/payload_catalog.json;ps5_image_forge' `
                --add-data 'vendor/payloads;ps5_image_forge/bundled_payloads' `
                --add-data 'vendor/ufs2tool;ufs2tool' entry.py
        }
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
}
if (-not (Test-Path $SrcExe)) { throw "Missing app exe: $SrcExe" }

# --- 2. branding art -------------------------------------------------------
# Assets need Pillow; use whichever interpreter has it (venv or system).
$AssetPython = $null
foreach ($cand in @($Python, 'python')) {
    $ok = $false
    try {
        $old = $ErrorActionPreference
        $ErrorActionPreference = 'SilentlyContinue'  # native stderr must not throw (WinPS 5.1)
        & $cand -c "import PIL" 2>&1 | Out-Null
        $ok = ($LASTEXITCODE -eq 0)
    } catch { $ok = $false } finally { $ErrorActionPreference = $old }
    if ($ok) { $AssetPython = $cand; break }
}
if (-not $AssetPython) {
    throw "Pillow not found. Install it: `"$Python`" -m pip install pillow"
}
Write-Host "==> Generating branding assets ($AssetPython)" -ForegroundColor Yellow
& $AssetPython (Join-Path $InstallerDir 'make_assets.py') --version $Version
if ($LASTEXITCODE -ne 0) { throw "Asset generation failed ($LASTEXITCODE)" }

# --- 3. locate makensis ----------------------------------------------------
$makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
if (-not $makensis) {
    foreach ($p in @(
        "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
        "$env:ProgramFiles\NSIS\makensis.exe")) {
        if (Test-Path $p) { $makensis = $p; break }
    }
}
if (-not $makensis) {
    throw "makensis not found. Install NSIS (https://nsis.sourceforge.io) or add makensis to PATH."
}
Write-Host "==> Using $makensis" -ForegroundColor Yellow

# --- 4. compile ------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
$OutFile = Join-Path $DistDir "PS5-Image-Forge-Setup-$Version.exe"
$nsi     = Join-Path $InstallerDir 'installer.nsi'

& $makensis `
    "/DVERSION=$Version" `
    "/DSRC_EXE=$SrcExe" `
    "/DOUTFILE=$OutFile" `
    "/DAPP_EXE=$AppExe" `
    $nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }

Write-Host ""
Write-Host "Installer written to $OutFile" -ForegroundColor Green
