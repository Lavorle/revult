# phase1_gates_windows.ps1 -- Phase 1 gate runner on Windows.
# Equivalent of phase1_gates.sh: check + test + build + no-SDL gate are
# delegated to build_windows.ps1; the smoke/nested/input/periodic gates
# run the built exe directly with RENPY_HOST_GATE set.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File host\scripts\phase1_gates_windows.ps1 [-Fast]
# -Fast skips the 60s periodic soak.

[CmdletBinding()]
param(
    [switch]$Fast,
    [string]$Toolkits = "E:\lyx\toolkits"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Exe = Join-Path $Root "host\target\x86_64-pc-windows-gnu\debug\renpy-host.exe"

& (Join-Path $PSScriptRoot "build_windows.ps1") -Test -Toolkits $Toolkits
if (-not (Test-Path $Exe)) { throw "host binary missing after build: $Exe" }

$Activate = Join-Path $Toolkits "activate.ps1"
if (Test-Path $Activate) { . $Activate }
$PythonBase = (& (Join-Path $Root ".venv\Scripts\python.exe") -c "import sys; print(sys.base_prefix)").Trim()
$GnuBin = Join-Path $env:RUSTUP_HOME "toolchains\stable-x86_64-pc-windows-gnu\bin"
$env:PATH = "$PythonBase;$GnuBin;$env:PATH"
$env:PYTHONPATH = "$Root\.venv\Lib\site-packages;$Root\host\python\gates"
$env:RENPY_HOST_BASE = $Root
$env:RENPY_HOST_HEADLESS = "1"
$env:RUST_LOG = "info,wgpu_hal=off,wgpu_core=off,naga=off"

$Gates = @(
    @{ Name = "smoke"; Secs = 2 },
    @{ Name = "nested"; Secs = 30 },
    @{ Name = "input"; Secs = 10 },
    @{ Name = "periodic"; Secs = 60 }
)
foreach ($Gate in $Gates) {
    if ($Fast -and $Gate.Name -eq "periodic") { Write-Host "SKIP: periodic (-Fast)"; continue }
    Write-Host "== gate: $($Gate.Name) ($($Gate.Secs)s max) =="
    $env:RENPY_HOST_GATE = $Gate.Name
    $env:RENPY_HOST_SMOKE_SECS = "$($Gate.Secs)"
    $LogFile = Join-Path $env:TEMP "renpy-host-phase1-$($Gate.Name).log"
    $PrevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $Exe 2> $LogFile | Out-Null
    $Rc = $LASTEXITCODE
    $ErrorActionPreference = $PrevEAP
    Get-Content $LogFile | Select-Object -Last 2
    if ($Rc -ne 0) { throw "gate $($Gate.Name) failed (rc=$Rc); log: $LogFile" }
    $Log = Get-Content $LogFile -Raw
    if ($Log -notmatch "backend=Vulkan") { throw "gate $($Gate.Name): no backend=Vulkan in log" }
    Write-Host "OK: $($Gate.Name)"
}
Write-Host "OK: all Phase 1 Windows gates passed (backend=Vulkan, no SDL)"
