# build_cython_windows.ps1 -- Build the SDL-free Cython extensions on Windows.
#
# The host product boot (renpy.import_all) needs the compiled modules
# (renpy.astsupport, renpy.cslots, renpy.text.bidi, ...); they have no
# pure-Python fallbacks. This script builds them with MSVC from the
# toolkits VS2022 install, matching run.sh's `setup.py build_ext --inplace`.
#
# Prerequisites (all under E:\lyx\toolkits):
#   - VS2022 Build Tools (VCTools workload) at $Toolkits\VS2022
#   - fribidi static lib at $Toolkits\fribidi (meson build; see host/README.md)
# RENPY_HOST_BUILD=1 skips renpy.ecsign: the host installs the pure-Python
# renpy_ecsign_host stand-in before import_all, so OpenSSL is not needed.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File host\scripts\build_cython_windows.ps1

[CmdletBinding()]
param(
    [string]$Toolkits = "E:\lyx\toolkits",
    [string]$Vcvars = "",
    [string]$FribidiDir = "",
    [string]$PythonExe = "",
    [int]$Jobs = 8
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if (-not $Vcvars) {
    $Vcvars = Join-Path $Toolkits "VS2022\VC\Auxiliary\Build\vcvars64.bat"
}
if (-not (Test-Path $Vcvars)) {
    # CI / stock machines: discover VS via vswhere (VCTools workload).
    $VsWhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $VsWhere) {
        $VsRoot = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($VsRoot) { $Vcvars = Join-Path $VsRoot "VC\Auxiliary\Build\vcvars64.bat" }
    }
}
if (-not (Test-Path $Vcvars)) { throw "vcvars64.bat not found (install VS Build Tools VCTools, or pass -Vcvars)" }
$Fribidi = if ($FribidiDir) { $FribidiDir } else { Join-Path $Toolkits "fribidi" }
if (-not (Test-Path (Join-Path $Fribidi "lib\fribidi.lib"))) {
    throw "fribidi.lib not found under $Fribidi\lib (build fribidi with meson first; see host/README.md)"
}
$Activate = Join-Path $Toolkits "activate.ps1"
if (Test-Path $Activate) { . $Activate }

$env:RENPY_HOST_BUILD = "1"
$env:RENPY_CYTHON = "cython"
$env:PYTHONUTF8 = "1"
$env:FRIBIDI_INCLUDE_DIRS = Join-Path $Fribidi "include"
$env:FRIBIDI_LIBRARY_DIRS = Join-Path $Fribidi "lib"
$env:FRIBIDI_LIBRARIES = "fribidi"
$env:TFDWIN_LIBRARIES = "user32;comdlg32;ole32;shell32"

$Python = if ($PythonExe) { $PythonExe } else { Join-Path $Root ".venv\Scripts\python.exe" }
if (-not (Test-Path $Python)) { throw "python not found: $Python (pass -PythonExe; CI uses setup-python)" }
$Log = Join-Path $Root "tmp\cython_build.log"
cmd /c "`"$Vcvars`" >nul 2>&1 && cd /d $Root && `"$Python`" setup.py build_ext -b tmp/build/lib.renpy-run -t tmp/build/tmp.renpy-run --inplace -j $Jobs > tmp\cython_build.log 2>&1"
if ($LASTEXITCODE -ne 0) {
    Get-Content $Log -Tail 30
    throw "Cython build failed ($LASTEXITCODE); full log: $Log"
}
$Pyds = Get-ChildItem (Join-Path $Root "tmp\build\lib.renpy-run") -Recurse -Filter *.pyd
Write-Host "OK: $($Pyds.Count) Cython extensions built (log: $Log)"
& $Python -c "import renpy.astsupport, renpy.cslots, renpy.lexersupport, renpy.pydict, renpy.encryption, renpy.text.bidi, renpy.text.textsupport, renpy.tfd; print('OK: extension imports')"
if ($LASTEXITCODE -ne 0) { throw "extension import check failed" }
