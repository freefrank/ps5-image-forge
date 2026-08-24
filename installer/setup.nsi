; Packs the branded setup app (installer/app/) into the single setup.exe users
; download. NSIS itself shows no UI at all here — SilentInstall silent means
; the only window anyone ever sees is our own pywebview installer.
;
; This replaces installer.nsi (MUI2), which is kept for reference. The wizard
; it drew could not be made to look like the app, and its "close the running
; instance" step was a `taskkill /F` that discarded whatever build or transfer
; was in flight. The Python installer asks instead.
;
; Build:
;   makensis /DSRCDIR=<PyInstaller onedir of the installer app>
;            /DOUTFILE=<out.exe> /DAPPVER=<x.y.z> [/DICONFILE=<installer.ico>]
;            setup.nsi
; or, end to end:  powershell -File installer\build_setup.ps1
;
; $PLUGINSDIR is NSIS's own scratch directory: created by InitPluginsDir and
; deleted automatically when this process exits. ExecWait keeps the outer
; process alive for exactly as long as the installer runs, so the app never has
; its own DLLs pulled out from under it.
;
; --self=$EXEPATH is load-bearing. The installer reproduces itself by copying
; its outer image to <install dir>\uninstall.exe, and under this wrapper that
; image is no longer the running process — engine.py cannot find it on its own.
; $EXEPATH is also how the app tells the three cases apart: setup.exe in
; Downloads (install), uninstall.exe in the install dir (uninstall).

Unicode true
ManifestDPIAware true

!ifndef SRCDIR
  !error "SRCDIR is required: /DSRCDIR=<path to the installer app folder>"
!endif
!ifndef OUTFILE
  !error "OUTFILE is required: /DOUTFILE=<path to the exe to write>"
!endif
!ifndef APPVER
  !define APPVER "0.0.0"
!endif
!ifndef INNER_EXE
  !define INNER_EXE "ps5if-setup.exe"
!endif

Name "PS5 Image Forge Setup"
OutFile "${OUTFILE}"
; Installs under %LOCALAPPDATA%\Programs and registers uninstall in HKCU, so it
; needs no elevation — and asking for admin would be a scarier prompt than the
; SmartScreen one users already get from an unsigned binary.
RequestExecutionLevel user
SilentInstall silent
SetCompressor /SOLID lzma

!ifdef ICONFILE
  Icon "${ICONFILE}"
!endif

VIProductVersion "${APPVER}.0"
VIAddVersionKey "ProductName"     "PS5 Image Forge"
VIAddVersionKey "FileDescription" "PS5 Image Forge Setup"
VIAddVersionKey "FileVersion"     "${APPVER}"
VIAddVersionKey "ProductVersion"  "${APPVER}"
VIAddVersionKey "CompanyName"     "freefrank"
VIAddVersionKey "LegalCopyright"  "GPL-3.0"

!include "FileFunc.nsh"

Section
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\setup"
  File /r "${SRCDIR}\*.*"

  ; Forward the user's own arguments (--auto, --uninstall) unchanged and
  ; prepend the outer path.
  ${GetParameters} $R0

  ExecWait '"$PLUGINSDIR\setup\${INNER_EXE}" "--self=$EXEPATH" $R0' $R1
  SetErrorLevel $R1
SectionEnd
