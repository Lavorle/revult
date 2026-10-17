# build_windows.ps1 -- Windows build for renpy-host (Vulkan-only, Phase W0/W1).
#
# Uses the GNU Rust toolchain + cargo-zigbuild from E:\lyx\toolkits, because
# this machine has no MSVC link.exe. The MSVC-built CPython 3.12 from the
# toolkits still links fine: PyO3 generates a GNU import library for
# python312.dll, and zig provides llvm-dlltool (GNU dlltool has no `as`).
#
# Usage (from repo root, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File host\scripts\build_windows.ps1
#   powershell -ExecutionPolicy Bypass -File host\scripts\build_windows.ps1 -Release -Smoke
#   powershell -ExecutionPolicy Bypass -File host\scripts\build_windows.ps1 -CheckOnly
#   powershell -ExecutionPolicy Bypass -File host\scripts\build_windows.ps1 -Test
#
# Backend policy: Vulkan only (gpu.rs hard-requires Backends::VULKAN).
# DX12 is out of scope; do not relax that check to make a build pass.

[CmdletBinding()]
param(
    [switch]$Release,
    [switch]$CheckOnly,
    [switch]$Smoke,
    [switch]$Test,
    [switch]$Package,
    [switch]$FullGame,
    [string]$Toolkits = "E:\lyx\toolkits",
    [int]$SmokeSecs = 3
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$HostDir = Join-Path $Root "host"
$Target = "x86_64-pc-windows-gnu"
$Toolchain = "stable-x86_64-pc-windows-gnu"
$Profile = if ($Release) { "release" } else { "debug" }
$Exe = Join-Path $HostDir "target\$Target\$Profile\renpy-host.exe"

# --- Toolchain -------------------------------------------------------------
$Activate = Join-Path $Toolkits "activate.ps1"
if (Test-Path $Activate) {
    . $Activate
} elseif (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo not found and no toolkits activate.ps1 at $Activate"
}

if (-not (Get-Command cargo-zigbuild -ErrorAction SilentlyContinue)) {
    throw "cargo-zigbuild not found on PATH (expected in $Toolkits\bin)"
}
if (-not (Get-Command zig -ErrorAction SilentlyContinue)) {
    throw "zig not found on PATH (expected in $Toolkits\zig)"
}

$Toolchains = rustup toolchain list
if (-not ($Toolchains -match [regex]::Escape($Toolchain))) {
    Write-Host "Installing $Toolchain (one-time)..."
    rustup toolchain install $Toolchain
    if ($LASTEXITCODE -ne 0) { throw "rustup toolchain install failed" }
}

# Directory holding python312.dll + the GNU runtime DLLs the exe needs.
$PythonBase = (& python -c "import sys; print(sys.base_prefix)").Trim()
$GnuBin = Join-Path $env:RUSTUP_HOME "toolchains\$Toolchain\bin"
foreach ($d in @($PythonBase, $GnuBin)) {
    if ((Test-Path $d) -and ($env:PATH -notlike "*$d*")) { $env:PATH = "$d;$env:PATH" }
}

# --- Build -------------------------------------------------------------------
Push-Location $HostDir
try {
    # Invoke cargo through rustup: $Toolkits\cargo\bin\cargo.exe is the real
    # binary, not the rustup proxy, so `cargo +<toolchain>` does not work.
    if ($CheckOnly) {
        rustup run $Toolchain cargo check -p renpy-host --target $Target
        if ($LASTEXITCODE -ne 0) { throw "cargo check failed ($LASTEXITCODE)" }
        Write-Host "OK: cargo check -p renpy-host ($Target)"
        return
    }

    $BuildArgs = @("zigbuild", "-p", "renpy-host", "--target", $Target)
    if ($Release) { $BuildArgs += "--release" }
    rustup run $Toolchain cargo @BuildArgs
    if ($LASTEXITCODE -ne 0) { throw "cargo zigbuild failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

if (-not (Test-Path $Exe)) { throw "expected binary missing: $Exe" }
Write-Host "OK: built $Exe ($([math]::Round((Get-Item $Exe).Length / 1MB, 1)) MB)"

# --- No-SDL import check (Windows equivalent of `ldd | grep libSDL`) --------
$PeCheck = @"
import pefile, sys
pe = pefile.PE(sys.argv[1])
dlls = [e.dll.decode(errors="replace") for e in pe.DIRECTORY_ENTRY_IMPORT]
bad = [d for d in dlls if "SDL" in d.upper()]
print("imports:", len(dlls), "dlls")
print("python312.dll:", any(d.lower() == "python312.dll" for d in dlls))
if bad:
    print("FAIL: SDL linked:", bad); sys.exit(1)
print("OK: no SDL* in import table")
"@
# pefile is a project dependency: use the project venv, not the bare toolkits python.
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { $VenvPython = "python" }
$PeCheck | & $VenvPython - $Exe
if ($LASTEXITCODE -ne 0) { throw "no-SDL import check failed ($LASTEXITCODE)" }

# --- Tests -------------------------------------------------------------------
if ($Test) {
    Push-Location $HostDir
    try {
        rustup run $Toolchain cargo zigbuild -p renpy-host --target $Target --tests
        if ($LASTEXITCODE -ne 0) { throw "test build failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    $DepsDir = Join-Path $HostDir "target\$Target\$Profile\deps"
    $Harnesses = Get-ChildItem "$DepsDir\*.exe" | Where-Object {
        $Out = Join-Path $env:TEMP "renpy-host-test-list.txt"
        $P = Start-Process -FilePath $_.FullName -ArgumentList "--list" -NoNewWindow -PassThru `
            -RedirectStandardOutput $Out -RedirectStandardError "$env:TEMP\renpy-host-test-list.err"
        if ($P.WaitForExit(5000)) { (Get-Content $Out -ErrorAction SilentlyContinue).Count -gt 0 }
        else { $P.Kill(); $false }
    }
    # The product binary itself also lands in deps/; --list filters it out.
    $env:RENPY_HOST_BASE = $Root
    $Total = 0
    foreach ($H in $Harnesses) {
        & $H.FullName | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0) { throw "tests failed in $($H.Name) ($LASTEXITCODE)" }
        $Total++
    }
    Write-Host "OK: $Total test harness(es) passed"
}

# --- Package -----------------------------------------------------------------
if ($Package -or $FullGame) {
    $Rev = (git -C $Root rev-parse --short HEAD 2>$null)
    if (-not $Rev) { $Rev = "unknown" }
    $BundleName = if ($FullGame) { "renpy-host-$Rev-windows-x86_64-fullgame" } else { "renpy-host-$Rev-windows-x86_64" }
    $Stage = Join-Path $HostDir "target\$BundleName"
    $Zip = Join-Path $HostDir "target\$BundleName.zip"
    Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $Zip -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $Stage | Out-Null

    Copy-Item $Exe $Stage
    # DLLs the exe needs next to it (Windows searches the exe dir first):
    # python312.dll + C runtime from the CPython base, GNU runtimes from the toolchain.
    foreach ($Dll in @("python312.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll")) {
        $Src = Join-Path $PythonBase $Dll
        if (Test-Path $Src) { Copy-Item $Src $Stage }
    }
    foreach ($Dll in @("libwinpthread-1.dll", "libgcc_s_seh-1.dll")) {
        Copy-Item (Join-Path $GnuBin $Dll) $Stage
    }
    # Embedded CPython home: stdlib + extension DLLs + Pillow from the project venv.
    $PyHome = Join-Path $Stage "python"
    New-Item -ItemType Directory -Force $PyHome | Out-Null
    Copy-Item (Join-Path $PythonBase "Lib") $PyHome -Recurse
    Copy-Item (Join-Path $PythonBase "DLLs") $PyHome -Recurse
    $Site = Join-Path $PyHome "Lib\site-packages"
    New-Item -ItemType Directory -Force $Site | Out-Null
    Copy-Item (Join-Path $Root ".venv\Lib\site-packages\PIL") $Site -Recurse
    # cryptography (+cffi) backs the host renpy.ecsign stand-in (savetoken).
    foreach ($Pkg in @("cryptography", "cffi", "pycparser")) {
        $Src = Join-Path $Root ".venv\Lib\site-packages\$Pkg"
        if (Test-Path $Src) { Copy-Item $Src $Site -Recurse }
    }
    Copy-Item (Join-Path $Root ".venv\Lib\site-packages\_cffi_backend*.pyd") $Site
    Get-ChildItem (Join-Path $Root ".venv\Lib\site-packages") -Directory |
        Where-Object { $_.Name -match "^(pillow|cryptography|cffi)-.*\.dist-info$" } |
        ForEach-Object { Copy-Item $_.FullName $Site -Recurse }
    if ($FullGame) {
        # Runtime third-party deps of the Ren'Py tree (pyproject dependencies;
        # dev tools like pytest/ruff/sphinx/cython stay out of the bundle).
        $SiteRoot = Join-Path $Root ".venv\Lib\site-packages"
        foreach ($Pkg in @("future", "past", "libfuturize", "requests", "urllib3", "certifi",
                "charset_normalizer", "idna", "chardet", "rsa", "pyasn1", "websockets",
                "jinja2", "markupsafe", "ordlookup")) {
            $Src = Join-Path $SiteRoot $Pkg
            if (Test-Path $Src) { Copy-Item $Src $Site -Recurse }
        }
        foreach ($Mod in @("six.py", "pefile.py", "socks.py")) {
            $Src = Join-Path $SiteRoot $Mod
            if (Test-Path $Src) { Copy-Item $Src $Site }
        }
    }
    Get-ChildItem $PyHome -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    # Product trees the host discovers from RENPY_HOST_BASE (= bundle root).
    if ($FullGame) {
        # Whole Ren'Py tree (incl. the .pyd extensions) + demo game, so the
        # bundle boots a game with no repository around.
        robocopy (Join-Path $Root "renpy") (Join-Path $Stage "renpy") /E /XJ /XD __pycache__ /XF *.pyc /NFL /NDL /NJH | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy renpy failed ($LASTEXITCODE)" }
        robocopy (Join-Path $Root "the_question") (Join-Path $Stage "the_question") /E /XJ /XD __pycache__ cache saves /XF *.pyc /NFL /NDL /NJH | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy the_question failed ($LASTEXITCODE)" }
    } else {
        Copy-Item (Join-Path $Root "renpy\wgpu") (Join-Path $Stage "renpy\wgpu") -Recurse
        Copy-Item (Join-Path $Root "renpy\__init__.py") (Join-Path $Stage "renpy\")
    }
    # robocopy /XJ: host/python/gates/hmc_dyn_gate.py is a Linux symlink (\tmp\...)
    # that dangles on Windows and makes Copy-Item fail the whole tree copy.
    robocopy (Join-Path $Root "host\python") (Join-Path $Stage "host\python") /E /XJ /NFL /NDL /NJH | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy host\python failed ($LASTEXITCODE)" }
    Copy-Item (Join-Path $Root "host\README.md") (Join-Path $Stage "host\")
    # Launcher: pin PYTHONHOME/RENPY_HOST_BASE to the bundle, no PATH surgery.
    $Launcher = @(
        "@echo off",
        "set PYTHONHOME=%~dp0python",
        "set RENPY_HOST_BASE=%~dp0."
    )
    if ($FullGame) { $Launcher += "set RENPY_HOST_GAME=%~dp0the_question" }
    $Launcher += "`"%~dp0renpy-host.exe`" %*"
    $Launcher | Set-Content (Join-Path $Stage "renpy-host.cmd") -Encoding Ascii

    Compress-Archive -Path "$Stage\*" -DestinationPath $Zip
    $Hash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
    "$Hash  $BundleName.zip" | Set-Content "$Zip.sha256" -Encoding Ascii
    Write-Host "OK: package $Zip ($([math]::Round((Get-Item $Zip).Length / 1MB, 1)) MB) sha256=$Hash"

    # Verify the staged exe runs with a bare PATH (only its own dir as DLL source).
    $env:PYTHONHOME = $PyHome
    $env:RENPY_HOST_BASE = $Stage
    $env:RENPY_HOST_HEADLESS = "1"
    $env:RENPY_HOST_SMOKE_SECS = if ($FullGame) { "15" } else { "$SmokeSecs" }
    $env:RUST_LOG = "info,wgpu_hal=off,wgpu_core=off,naga=off"
    if ($FullGame) {
        # Boot the bundled game with no repo PYTHONPATH; cwd is TEMP below.
        Remove-Item Env:RENPY_HOST_PHASE0_SMOKE -ErrorAction SilentlyContinue
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        $env:RENPY_HOST_GAME = Join-Path $Stage "the_question"
        $env:RENPY_PERFORMANCE_TEST = "0"
        $env:PYTHONUTF8 = "1"
    } else {
        $env:RENPY_HOST_PHASE0_SMOKE = "1"
    }
    $SavedPath = $env:PATH
    $env:PATH = "C:\Windows\System32;C:\Windows"
    $StageLog = Join-Path $env:TEMP "renpy-host-package-smoke.log"
    $PrevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    Push-Location $env:TEMP
    & (Join-Path $Stage "renpy-host.exe") 2> $StageLog | Out-Null
    $StageRc = $LASTEXITCODE
    Pop-Location
    $ErrorActionPreference = $PrevEAP
    $env:PATH = $SavedPath
    Remove-Item Env:RENPY_HOST_PHASE0_SMOKE -ErrorAction SilentlyContinue
    $StageLogText = Get-Content $StageLog -Raw
    if ($FullGame) {
        # versions.py logs a benign git traceback when detached from a repo;
        # the hard failures are a nonzero exit, a gate failure, or savetoken
        # being unable to generate its signing key.
        if ($StageRc -ne 0 -or $StageLogText -notmatch "backend=Vulkan" -or $StageLogText -match "Python gate failed" -or $StageLogText -match "Failed to generate signing key") {
            throw "full-game package boot failed (rc=$StageRc); log: $StageLog"
        }
        Write-Host "OK: full-game package boots standalone (bare PATH, bundle PYTHONHOME, cwd=TEMP)"
    } else {
        if ($StageRc -ne 0 -or $StageLogText -notmatch "backend=Vulkan" -or $StageLogText -notmatch "pump smoke done") {
            throw "package smoke failed (rc=$StageRc); log: $StageLog"
        }
        Write-Host "OK: packaged exe runs standalone (bare PATH, bundle PYTHONHOME)"
    }
}

# --- Smoke -------------------------------------------------------------------
if ($Smoke) {
    $env:RENPY_HOST_BASE = $Root
    $env:RENPY_HOST_HEADLESS = "1"
    $env:RENPY_HOST_PHASE0_SMOKE = "1"
    $env:RENPY_HOST_SMOKE_SECS = "$SmokeSecs"
    $env:RUST_LOG = "info,wgpu_hal=off,wgpu_core=off,naga=off"
    # The host logs to stderr; PowerShell 5.1 turns that into an error record
    # under $ErrorActionPreference=Stop, so capture via a file instead.
    $LogFile = Join-Path $env:TEMP "renpy-host-win-smoke.log"
    $PrevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Exe 2> $LogFile | Out-Null
    $ErrorActionPreference = $PrevEAP
    $Log = Get-Content $LogFile -Raw
    $Log -split "`n" | Select-String -Pattern "backend=Vulkan|CPython|pump smoke done" | ForEach-Object { $_.Line.Trim() }
    if ($Log -notmatch "backend=Vulkan") { throw "smoke failed: no backend=Vulkan in host log" }
    if ($Log -notmatch "pump smoke done") { throw "smoke failed: nested pump did not finish" }
    Write-Host "OK: Windows smoke (Vulkan adapter + embedded CPython + nested pump)"
}
