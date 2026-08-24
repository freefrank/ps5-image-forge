<#
.SYNOPSIS
    Build the scene-style branded Windows setup for PS5 Image Forge.

.DESCRIPTION
    1. Reads the version from pyproject.toml.
    2. Regenerates the branding art (installer/make_assets.py, needs Pillow).
    3. Builds the app exe with PyInstaller if it is missing (or -Build).
    4. Stages the payload (app exe + LICENSE + VERSION stamp).
    5. Builds installer/app/ with PyInstaller, embedding that payload.
    6. Runs makensis on installer/setup.nsi to wrap it in one silent
       self-extractor.

    The finished setup lands in dist\PS5-Image-Forge-Setup-<version>.exe and is
    the same binary that becomes uninstall.exe in the install folder.

    Everything under build\ and dist\ is git-ignored.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\build_setup.ps1
    powershell -ExecutionPolicy Bypass -File installer\build_setup.ps1 -Build
#>
[CmdletBinding()]
param(
    # Force a fresh PyInstaller build of the app even if dist\...exe exists.
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
$PayloadDir   = Join-Path $RepoRoot 'build\setup-payload'
$SetupAppDir  = Join-Path $RepoRoot 'build\setup-app'
$WorkDir      = Join-Path $RepoRoot 'build\setup-work'
$InnerExe     = 'ps5if-setup'

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

# --- 1. branding art (before the exe, so the spec can embed the icon) ------
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

$Icon = Join-Path $InstallerDir 'assets\installer.ico'

# --- 2. build the app exe if needed ---------------------------------------
if ($Build -or -not (Test-Path $SrcExe)) {
    Write-Host '==> Building the app exe with PyInstaller' -ForegroundColor Yellow
    Push-Location $RepoRoot
    try {
        & $Python -m PyInstaller --noconfirm (Join-Path $RepoRoot 'PS5-Image-Forge.spec')
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
}
if (-not (Test-Path $SrcExe)) { throw "Missing app exe: $SrcExe" }

# --- 3. stage the payload --------------------------------------------------
# One directory holding exactly what lands in the install folder, plus a
# VERSION stamp the installer reads back. engine.payload_files() copies
# everything here except VERSION, so adding a file is a one-line change.
Write-Host '==> Staging payload' -ForegroundColor Yellow
Remove-Item -Recurse -Force $PayloadDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null
Copy-Item $SrcExe (Join-Path $PayloadDir $AppExe)
Copy-Item (Join-Path $RepoRoot 'LICENSE') $PayloadDir
Set-Content -Path (Join-Path $PayloadDir 'VERSION') -Value $Version -NoNewline -Encoding ascii

# A truncated or missing app exe here produces a setup that installs nothing
# and still exits 0 — catch it now rather than from a user's bug report.
$payloadBytes = (Get-ChildItem $PayloadDir -File | Measure-Object Length -Sum).Sum
if ($payloadBytes -lt 20MB) {
    throw "payload suspiciously small ($payloadBytes bytes) — refusing to build a placeholder setup"
}
"    payload {0:N1} MB" -f ($payloadBytes / 1MB) | Write-Host

# --- 4. build the installer app -------------------------------------------
Write-Host '==> Building the installer app with PyInstaller' -ForegroundColor Yellow
Remove-Item -Recurse -Force $SetupAppDir -ErrorAction SilentlyContinue
Push-Location $RepoRoot
try {
    & $Python -m PyInstaller --noconfirm --onedir --windowed --name $InnerExe `
        --distpath $SetupAppDir --workpath $WorkDir --specpath $WorkDir `
        --icon $Icon `
        --collect-all webview `
        --add-data "$(Join-Path $InstallerDir 'app\webui');webui" `
        --add-data "$PayloadDir;payload" `
        (Join-Path $InstallerDir 'app\main.py')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally { Pop-Location }

$SrcDir = Join-Path $SetupAppDir $InnerExe
if (-not (Test-Path (Join-Path $SrcDir "$InnerExe.exe"))) {
    throw "Installer app missing: $SrcDir\$InnerExe.exe"
}

# --- 5. locate makensis ----------------------------------------------------
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

# --- 6. pack ---------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
$OutFile = Join-Path $DistDir "PS5-Image-Forge-Setup-$Version.exe"

& $makensis /V2 "/DSRCDIR=$SrcDir" "/DOUTFILE=$OutFile" "/DAPPVER=$Version" `
    "/DICONFILE=$Icon" "/DINNER_EXE=$InnerExe.exe" `
    (Join-Path $InstallerDir 'setup.nsi')
if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }
if (-not (Test-Path $OutFile)) { throw "makensis produced no output" }

Write-Host ""
Write-Host ("Setup written to {0} ({1:N1} MB)" -f $OutFile, ((Get-Item $OutFile).Length / 1MB)) `
    -ForegroundColor Green
