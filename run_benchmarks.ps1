param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,

    [string]$Baseline = "stable",

    [switch]$PromoteBaseline,

    [ValidateSet("smoke", "medium", "real", "full")]
    [string]$Size = "smoke",

    [int]$Seed = 42,

    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$DatasetsRoot = "D:\Stage_udgroup\sources\datasets"
$ReportRoot = Join-Path $ProjectRoot "dataset\reports\multi_dataset_benchmark"
$BaselineRoot = Join-Path $ProjectRoot "dataset\reports\baselines"
$RunArchiveRoot = Join-Path $ProjectRoot "dataset\reports\benchmark_runs"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtualenv not found at $Python"
}

if (-not (Test-Path -LiteralPath $DatasetsRoot)) {
    throw "Datasets root not found at $DatasetsRoot"
}

switch ($Size) {
    "smoke" { $LimitPerDataset = 5 }
    "medium" { $LimitPerDataset = 100 }
    "real" { $LimitPerDataset = 500 }
    "full" { $LimitPerDataset = $null }
}

New-Item -ItemType Directory -Force -Path $BaselineRoot, $RunArchiveRoot | Out-Null

if (Test-Path -LiteralPath $ReportRoot) {
    $PreviousName = "_previous_multi_dataset_benchmark_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
    $PreviousOutput = Join-Path $RunArchiveRoot $PreviousName
    Move-Item -LiteralPath $ReportRoot -Destination $PreviousOutput -Force
    Write-Host "Archived previous shared benchmark output: $PreviousOutput"
}
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null

Write-Host "Project: $ProjectRoot"
Write-Host "Datasets: $DatasetsRoot"
Write-Host "RunId: $RunId"
Write-Host "Baseline: $Baseline"
Write-Host "Size: $Size"

Push-Location $ProjectRoot
try {
    & $Python scripts\benchmark_multi_datasets.py --check-env

    $ArgsList = @(
        "scripts\benchmark_multi_datasets.py",
        "--datasets-root", $DatasetsRoot,
        "--seed", "$Seed"
    )

    if ($LimitPerDataset -ne $null) {
        $ArgsList += @("--limit-per-dataset", "$LimitPerDataset")
    }

    if ($Force) {
        $ArgsList += "--force"
    }

    & $Python @ArgsList

    & $Python scripts\generate_multi_dataset_report.py --output $ReportRoot

    $RunOutput = Join-Path $RunArchiveRoot $RunId
    if (Test-Path -LiteralPath $RunOutput) {
        Remove-Item -LiteralPath $RunOutput -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $RunOutput | Out-Null
    Copy-Item -LiteralPath $ReportRoot -Destination $RunOutput -Recurse -Force

    if ($PromoteBaseline) {
        $BaselineOutput = Join-Path $BaselineRoot $Baseline
        if (Test-Path -LiteralPath $BaselineOutput) {
            Remove-Item -LiteralPath $BaselineOutput -Recurse -Force
        }
        Copy-Item -LiteralPath $ReportRoot -Destination $BaselineOutput -Recurse -Force
        Write-Host "Promoted run '$RunId' to baseline '$Baseline'."
    }
    else {
        $BaselineOutput = Join-Path $BaselineRoot $Baseline
        if (Test-Path -LiteralPath $BaselineOutput) {
            Write-Host "Baseline available for manual comparison: $BaselineOutput"
        }
        else {
            Write-Host "Baseline '$Baseline' not found yet. Run with -PromoteBaseline once to create it."
        }
    }

    Write-Host "Run archived at: $RunOutput"
    Write-Host "Latest report: $(Join-Path $ReportRoot 'global_report.html')"
}
finally {
    Pop-Location
}

