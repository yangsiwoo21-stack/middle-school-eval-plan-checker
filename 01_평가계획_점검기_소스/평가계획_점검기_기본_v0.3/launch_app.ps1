$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "assessment_checker_app\run_app.ps1"

if (-not $script) {
    throw "Could not find assessment_checker_app\run_app.ps1 under $root"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $script
