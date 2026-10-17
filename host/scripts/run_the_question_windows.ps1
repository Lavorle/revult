# run_the_question_windows.ps1 -- Launch the_question under renpy-host on Windows.
# Equivalent of run_the_question.sh. Requires the Cython extensions
# (build_cython_windows.ps1) and the host binary (build_windows.ps1).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File host\scripts\run_the_question_windows.ps1 [-Smoke 30] [-Release]
# Without -Smoke the game runs interactively until closed.

[CmdletBinding()]
param(
    [int]$Smoke = 0,
    [switch]$Release,
    [string]$Toolkits = "E:\lyx\toolkits"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Profile = if ($Release) { "release" } else { "debug" }
$Exe = Join-Path $Root "host\target\x86_64-pc-windows-gnu\$Profile\renpy-host.exe"
if (-not (Test-Path $Exe)) { throw "host binary missing: $Exe (run build_windows.ps1 first)" }
if (-not (Get-ChildItem (Join-Path $Root "renpy\*.cp312-win_amd64.pyd"))) {
    throw "Cython extensions missing in renpy\ (run build_cython_windows.ps1 first)"
}

$Activate = Join-Path $Toolkits "activate.ps1"
if (Test-Path $Activate) { . $Activate }
$PythonBase = (& (Join-Path $Root ".venv\Scripts\python.exe") -c "import sys; print(sys.base_prefix)").Trim()
$GnuBin = Join-Path $env:RUSTUP_HOME "toolchains\stable-x86_64-pc-windows-gnu\bin"
$env:PATH = "$PythonBase;$GnuBin;$env:PATH"
$env:PYTHONPATH = "$Root\.venv\Lib\site-packages;$Root\host\python\gates"
$env:PYTHONUTF8 = "1"
$env:RENPY_HOST_BASE = $Root
$env:RENPY_HOST_GAME = Join-Path $Root "the_question"
$env:RENPY_PERFORMANCE_TEST = "0"
$env:RUST_LOG = "info,wgpu_hal=off,wgpu_core=off,naga=off"
Remove-Item Env:RENPY_HOST_GATE -ErrorAction SilentlyContinue
if ($Smoke -gt 0) {
    $env:RENPY_HOST_HEADLESS = "1"
    $env:RENPY_HOST_SMOKE_SECS = "$Smoke"
    Write-Host "== smoke auto-quit after ${Smoke}s (headless) =="
} else {
    Remove-Item Env:RENPY_HOST_HEADLESS -ErrorAction SilentlyContinue
    Remove-Item Env:RENPY_HOST_SMOKE_SECS -ErrorAction SilentlyContinue
}

Write-Host "== run $Exe the_question =="
& $Exe (Join-Path $Root "the_question")
if ($LASTEXITCODE -ne 0) { throw "renpy-host exited with $LASTEXITCODE" }
Write-Host "OK: the_question ran under renpy-host (Windows/Vulkan)"
