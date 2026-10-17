# run_golden_tests_windows.ps1 -- Tier 2 Golden Visual Regression on Windows.
# Windows equivalent of run_golden_tests.sh: same parent_runner envelopes,
# same gate-*.txt verdicts, but invokes the built renpy-host.exe directly
# (no cargo run, no xvfb; RENPY_HOST_HEADLESS=1 skips present).
#
# Text gates (g02*) render with %WINDIR%\Fonts\arial.ttf on Windows vs
# NotoSans in the Linux baselines, so their MAE is a known font variance
# (same class as the llvmpipe G02 note in CI). They are reported but do
# not fail the suite unless -StrictText is given. Baselines are never
# rewritten by this script.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File host\scripts\build_windows.ps1
#   powershell -ExecutionPolicy Bypass -File host\scripts\run_golden_tests_windows.ps1 [-StrictText]

[CmdletBinding()]
param(
    [string]$Exe = "",
    [switch]$StrictText,
    [string]$Toolkits = "E:\lyx\toolkits"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Exe) { $Exe = Join-Path $Root "host\target\x86_64-pc-windows-gnu\debug\renpy-host.exe" }
if (-not (Test-Path $Exe)) { throw "host binary missing: $Exe (run build_windows.ps1 first)" }

$Activate = Join-Path $Toolkits "activate.ps1"
if (Test-Path $Activate) { . $Activate }
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { $VenvPython = "python" }

$PythonBase = (& $VenvPython -c "import sys; print(sys.base_prefix)").Trim()
$GnuBin = Join-Path $env:RUSTUP_HOME "toolchains\stable-x86_64-pc-windows-gnu\bin"
$env:PATH = "$PythonBase;$GnuBin;$env:PATH"
$env:PYTHONPATH = "$Root\.venv\Lib\site-packages;$Root\host\python\gates"
$env:RENPY_HOST_BASE = $Root
$env:RENPY_HOST_HEADLESS = "1"
$env:RENPY_HOST_SMOKE_SECS = "20"
$env:RUST_LOG = "info,wgpu_hal=off,wgpu_core=off,naga=off"

$Runner = Join-Path $Root "host\scripts\runner\parent_runner.py"
$EnvelopeDir = Join-Path $Root "host\target\envelopes"
New-Item -ItemType Directory -Force $EnvelopeDir | Out-Null

$CaseDir = @{
    g01 = "G01_solid_image"; g02 = "G02_text"; g03 = "G03_dissolve"; g04 = "G04_blur"
    g05 = "G05_movie"; g06 = "G06_live2d"; g07 = "G07_model"; g08 = "G08_mask"
    g02_cjk_vertical = "G02_cjk_vertical"; g02_arabic = "G02_arabic"
    g03_rot_clip = "G03_rot_clip"; g03_fog_mask = "G03_fog_mask"
}
$Mandatory = @("g01", "g03", "g04", "g05", "g06", "g07", "g08", "g03_rot_clip", "g03_fog_mask")
$TextGates = @("g02", "g02_cjk_vertical", "g02_arabic")

function Run-Gate([string]$Gate, [bool]$IsMandatory) {
    $Baseline = Join-Path $Root "testcases\wgpu_golden\$($CaseDir[$Gate])\baseline.rgba"
    $GatePy = Join-Path $Root "host\python\gates\$Gate.py"
    $Out = Join-Path $Root "host\target\gate-$Gate.txt"
    $Envelope = Join-Path $EnvelopeDir "$Gate.json"
    Remove-Item $Out -ErrorAction SilentlyContinue
    $env:RENPY_HOST_GATE = $Gate
    # Host logs go to stderr; PowerShell 5.1 would turn them into errors under Stop.
    $GateLog = Join-Path $env:TEMP "renpy-host-golden-$Gate.log"
    $PrevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $VenvPython $Runner --envelope-out $Envelope --input $Baseline --input $GatePy --cwd (Join-Path $Root "host") -- $Exe 2> $GateLog | Out-Null
    $Rc = $LASTEXITCODE
    $ErrorActionPreference = $PrevEAP
    $Txt = if (Test-Path $Out) { (Get-Content $Out -Raw).Trim() } else { "" }
    $Ok = ($Rc -eq 0) -and (Test-Path $Envelope) -and $Txt -and ($Txt -notmatch "ok=False")
    Write-Host "$(if ($Ok) { 'PASS' } else { 'FAIL' }): $Gate rc=$Rc $Txt"
    return [bool]$Ok
}

$Failed = 0; $Passed = 0
foreach ($g in $Mandatory) { if (Run-Gate $g $true) { $Passed++ } else { $Failed++ } }
$TextFailed = 0
foreach ($g in $TextGates) { if (-not (Run-Gate $g $false)) { $TextFailed++ } }

Write-Host "Mandatory passed: $Passed / $($Mandatory.Count); failed: $Failed"
Write-Host "Text gates failed (font variance): $TextFailed / $($TextGates.Count)$(if ($StrictText) { ' (strict)' } else { ' (non-blocking)' })"
if ($Failed -gt 0) { throw "Golden suite FAILED: $Failed mandatory gate(s)" }
if ($StrictText -and $TextFailed -gt 0) { throw "Golden suite FAILED (strict text): $TextFailed text gate(s)" }
Write-Host "OK: Windows golden suite (mandatory gates)"
