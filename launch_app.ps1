$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Get-ChildItem -LiteralPath $root -Directory |
  ForEach-Object { Join-Path $_.FullName "assessment_checker_app\run_app.ps1" } |
  Where-Object { Test-Path -LiteralPath $_ } |
  Select-Object -First 1

if (-not $script) {
  throw "Could not find assessment_checker_app\run_app.ps1 under $root"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $script
