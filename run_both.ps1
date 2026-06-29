###############################################################################
# run_both.ps1 — Chay 2 giai phap roi so sanh
#
# Buoc 1: Cai dat team 1142 venv (chi lan dau)
# Buoc 2: Chay giai phap cua ban
# Buoc 3: Chay giai phap team 1142
# Buoc 4: So sanh ket qua
#
# Su dung:
#   .\run_both.ps1                   # Chay ca hai + so sanh
#   .\run_both.ps1 -SkipMine         # Chi chay team 1142
#   .\run_both.ps1 -SkipTeam1142     # Chi chay giai phap cua ban
#   .\run_both.ps1 -CompareOnly      # Chi so sanh (da chay truoc do)
###############################################################################

param(
    [switch]$SkipMine,
    [switch]$SkipTeam1142,
    [switch]$CompareOnly,
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
$TEAM1142_DIR = Join-Path $ROOT "team1142 solution\kddcup2026-data-agents-starter-kit-agent1"
$TEAM1142_VENV = Join-Path $TEAM1142_DIR ".venv"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DABench Solution Comparison Runner" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ----- Step 0: Setup team 1142 venv if needed -----
if (-not (Test-Path $TEAM1142_VENV)) {
    Write-Host "[SETUP] Creating venv for team 1142..." -ForegroundColor Yellow
    Push-Location $TEAM1142_DIR
    uv venv
    uv pip install -e .
    Pop-Location
    Write-Host "[SETUP] Team 1142 venv created." -ForegroundColor Green
} else {
    Write-Host "[SETUP] Team 1142 venv already exists." -ForegroundColor DarkGray
}

# Symlink data directory for team 1142 if needed
$TEAM1142_DATA = Join-Path $TEAM1142_DIR "data"
if (-not (Test-Path $TEAM1142_DATA)) {
    Write-Host "[SETUP] Creating data symlink for team 1142..." -ForegroundColor Yellow
    # Create a junction (doesn't need admin rights)
    $TARGET_DATA = Join-Path $ROOT "data"
    cmd /c mklink /J "$TEAM1142_DATA" "$TARGET_DATA"
    Write-Host "[SETUP] Data symlink created." -ForegroundColor Green
}

if ($SetupOnly) {
    Write-Host "`nSetup complete!" -ForegroundColor Green
    exit 0
}

if ($CompareOnly) {
    Write-Host "[COMPARE] Running comparison..." -ForegroundColor Cyan
    Push-Location $ROOT
    & "$ROOT\.venv\Scripts\python.exe" compare_solutions.py --official-only
    Pop-Location
    exit 0
}

# ----- Step 1: Run YOUR solution -----
if (-not $SkipMine) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  Running YOUR solution (consensus)" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
    Write-Host ""
    Push-Location $ROOT
    $env:VIRTUAL_ENV = "$ROOT\.venv"
    uv run dabench run-consensus --config configs/hierarchical_baseline.yaml --official-only --max-rounds 3
    Pop-Location
} else {
    Write-Host "[SKIP] Skipping your solution." -ForegroundColor DarkGray
}

# ----- Step 2: Run Team 1142 solution -----
if (-not $SkipTeam1142) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Magenta
    Write-Host "  Running Team 1142 solution (consensus)" -ForegroundColor Magenta
    Write-Host "============================================" -ForegroundColor Magenta
    Write-Host ""

    # Create output dir if needed
    $T1142_OUT = Join-Path $ROOT "artifacts\runs_team1142"
    if (-not (Test-Path $T1142_OUT)) {
        New-Item -ItemType Directory -Path $T1142_OUT -Force | Out-Null
    }

    Push-Location $TEAM1142_DIR
    & "$TEAM1142_VENV\Scripts\dabench.exe" run-benchmark --config configs/run_with_consensus.yaml --official-only
    Pop-Location
} else {
    Write-Host "[SKIP] Skipping team 1142 solution." -ForegroundColor DarkGray
}

# ----- Step 3: Compare -----
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Comparing Results" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Push-Location $ROOT
& "$ROOT\.venv\Scripts\python.exe" compare_solutions.py --official-only
Pop-Location

Write-Host ""
Write-Host "Done!" -ForegroundColor Green
