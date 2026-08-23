; PS5 Image Forge — Windows installer (NSIS 3.x, Modern UI 2)
;
; Styled to match the app's cyberpunk UI (see webui/app.css):
;   deep-navy ground #070b14, neon-cyan #00e5ff, magenta accent #ff2d95.
; The Welcome/Finish pages and the install log render fully dark with neon
; text; interior pages carry a dark neon header band. Branding art is produced
; by installer/make_assets.py.
;
; Build (from the repo root):
;   powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
; or directly, once dist\PS5-Image-Forge.exe and assets\ exist:
;   makensis /DVERSION=0.7.4 installer\installer.nsi
;
; Overridable defines: VERSION, SRC_EXE, OUTFILE, APP_EXE.

Unicode true
SetCompressor /SOLID lzma

;--------------------------------- metadata --------------------------------
!ifndef VERSION
  !define VERSION "0.7.4"
!endif
!define PRODUCT_NAME    "PS5 Image Forge"
!define PRODUCT_SLUG    "PS5-Image-Forge"
!define PUBLISHER       "freefrank"
!define WEB_SITE        "https://github.com/freefrank/ps5-image-forge"

!ifndef APP_EXE
  !define APP_EXE "PS5-Image-Forge.exe"
!endif
; Path to the PyInstaller single-file exe to package (relative to this .nsi).
!ifndef SRC_EXE
  !define SRC_EXE "..\dist\${APP_EXE}"
!endif
; Where the finished setup is written (relative to this .nsi -> repo dist\).
!ifndef OUTFILE
  !define OUTFILE "..\dist\${PRODUCT_SLUG}-Setup-${VERSION}.exe"
!endif

!define REG_UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

Name "${PRODUCT_NAME}"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
InstallDirRegKey HKLM "Software\${PRODUCT_NAME}" "InstallDir"
RequestExecutionLevel admin
ShowInstDetails show
ShowUninstDetails show
BrandingText "PS5 IMAGE  FORGE   v${VERSION}"

; Dark cyberpunk install log (text / background), like the app console.
InstallColors 00E5FF 070B14
XPStyle on

;----------------------------- version resource ----------------------------
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName"     "${PRODUCT_NAME}"
VIAddVersionKey "FileDescription" "${PRODUCT_NAME} Setup"
VIAddVersionKey "FileVersion"     "${VERSION}.0"
VIAddVersionKey "ProductVersion"  "${VERSION}"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "LegalCopyright"  "GPL-3.0"

;--------------------------------- MUI 2 -----------------------------------
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!insertmacro GetSize

; Neon-on-navy chrome
!define MUI_BGCOLOR "070B14"
!define MUI_ICON   "assets\installer.ico"
!define MUI_UNICON "assets\uninstall.ico"

; Header strip: right-aligned wordmark, no stretch
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP         "assets\header.bmp"
!define MUI_HEADERIMAGE_UNBITMAP       "assets\header.bmp"

; Tall side banner on Welcome / Finish
!define MUI_WELCOMEFINISHPAGE_BITMAP   "assets\welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "assets\welcome.bmp"

!define MUI_WELCOMEPAGE_TITLE "PS5 Image Forge"
!define MUI_WELCOMEPAGE_TEXT  "Mount-free exFAT / PFS / UFS image builder for PS5 game dumps — with backport, a payload library and console tools.$\r$\n$\r$\nThis will install ${PRODUCT_NAME} ${VERSION} on your computer.$\r$\n$\r$\nClick Next to continue."

!define MUI_FINISHPAGE_RUN            "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT       "Launch ${PRODUCT_NAME}"
!define MUI_FINISHPAGE_LINK           "Project page on GitHub"
!define MUI_FINISHPAGE_LINK_LOCATION  "${WEB_SITE}"

; Skin callbacks (recolor page text for the dark ground)
!define MUI_PAGE_CUSTOMFUNCTION_SHOW SkinWelcome
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_SHOW SkinHeader
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW SkinHeader
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_SHOW SkinHeader
!insertmacro MUI_PAGE_DIRECTORY
!define MUI_PAGE_CUSTOMFUNCTION_SHOW SkinHeader
!insertmacro MUI_PAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_SHOW SkinFinish
!insertmacro MUI_PAGE_FINISH

!define MUI_PAGE_CUSTOMFUNCTION_SHOW un.SkinHeader
!insertmacro MUI_UNPAGE_CONFIRM
!define MUI_PAGE_CUSTOMFUNCTION_SHOW un.SkinHeader
!insertmacro MUI_UNPAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_SHOW un.SkinFinish
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

;------------------------------ skin helpers -------------------------------
; Colors: cyan text 00E5FF, dim text 5D7A8C, body text C8E6EF, ground 070B14.
; Interior pages: recolor header title (1037) + subtitle (1038).
; Welcome/Finish: recolor the inner-dialog title (1201) + text (1202).

!macro _SkinHeaderBody
  GetDlgItem $0 $HWNDPARENT 1037
  SetCtlColors $0 0x00E5FF 0x070B14
  GetDlgItem $0 $HWNDPARENT 1038
  SetCtlColors $0 0x5D7A8C 0x070B14
!macroend

Function SkinHeader
  !insertmacro _SkinHeaderBody
FunctionEnd
Function un.SkinHeader
  !insertmacro _SkinHeaderBody
FunctionEnd

Function SkinWelcome
  GetDlgItem $0 $mui.WelcomePage 1201
  SetCtlColors $0 0x00E5FF 0x070B14
  GetDlgItem $0 $mui.WelcomePage 1202
  SetCtlColors $0 0xC8E6EF 0x070B14
FunctionEnd
Function SkinFinish
  GetDlgItem $0 $mui.FinishPage 1201
  SetCtlColors $0 0x00E5FF 0x070B14
  GetDlgItem $0 $mui.FinishPage 1202
  SetCtlColors $0 0xC8E6EF 0x070B14
FunctionEnd
Function un.SkinFinish
  GetDlgItem $0 $mui.FinishPage 1201
  SetCtlColors $0 0x00E5FF 0x070B14
  GetDlgItem $0 $mui.FinishPage 1202
  SetCtlColors $0 0xC8E6EF 0x070B14
FunctionEnd

;--------------------------------- sections --------------------------------
Section "!${PRODUCT_NAME} (required)" SEC_CORE
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; close a running instance so the exe can be overwritten
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F /T'

  File "/oname=${APP_EXE}" "${SRC_EXE}"
  File "/nonfatal" "..\LICENSE"
  File "/nonfatal" "..\README.md"
  File "/nonfatal" "..\CHANGELOG.md"

  ; Start Menu
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut  "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" \
                  "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortCut  "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk" \
                  "$INSTDIR\uninstall.exe"

  ; registry: install dir + Add/Remove Programs
  WriteRegStr HKLM "Software\${PRODUCT_NAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\${PRODUCT_NAME}" "Version" "${VERSION}"

  WriteRegStr HKLM "${REG_UNINST}" "DisplayName"     "${PRODUCT_NAME}"
  WriteRegStr HKLM "${REG_UNINST}" "DisplayVersion"  "${VERSION}"
  WriteRegStr HKLM "${REG_UNINST}" "Publisher"       "${PUBLISHER}"
  WriteRegStr HKLM "${REG_UNINST}" "DisplayIcon"     "$INSTDIR\${APP_EXE}"
  WriteRegStr HKLM "${REG_UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${REG_UNINST}" "URLInfoAbout"    "${WEB_SITE}"
  WriteRegStr HKLM "${REG_UNINST}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegStr HKLM "${REG_UNINST}" "QuietUninstallString" "$\"$INSTDIR\uninstall.exe$\" /S"
  WriteRegDWORD HKLM "${REG_UNINST}" "NoModify" 1
  WriteRegDWORD HKLM "${REG_UNINST}" "NoRepair" 1

  ; EstimatedSize (KB)
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${REG_UNINST}" "EstimatedSize" "$0"

  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Desktop shortcut" SEC_DESKTOP
  CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" \
                 "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
SectionEnd

;------------------------- component descriptions --------------------------
LangString DESC_CORE    ${LANG_ENGLISH} "The ${PRODUCT_NAME} application and its shortcuts."
LangString DESC_CORE    ${LANG_SIMPCHINESE} "${PRODUCT_NAME} 主程序及快捷方式。"
LangString DESC_DESKTOP ${LANG_ENGLISH} "Place a shortcut on the desktop."
LangString DESC_DESKTOP ${LANG_SIMPCHINESE} "在桌面创建快捷方式。"

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_CORE}    $(DESC_CORE)
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_DESKTOP} $(DESC_DESKTOP)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

;-------------------------------- uninstall --------------------------------
Section "Uninstall"
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F /T'

  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\CHANGELOG.md"
  Delete "$INSTDIR\uninstall.exe"
  RMDir  "$INSTDIR"

  Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${PRODUCT_NAME}"
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"

  DeleteRegKey HKLM "${REG_UNINST}"
  DeleteRegKey HKLM "Software\${PRODUCT_NAME}"
SectionEnd
