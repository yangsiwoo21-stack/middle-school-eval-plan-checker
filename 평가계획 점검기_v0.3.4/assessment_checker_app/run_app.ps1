$ErrorActionPreference = "Stop"

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $appDir

$candidatePythons = @(
  "python",
  "py",
  (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe"),
  (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
)

$python = $null
foreach ($candidate in $candidatePythons) {
  if ($candidate -in @("python", "py")) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
      $python = $candidate
      break
    }
  } elseif (Test-Path -LiteralPath $candidate) {
    $python = $candidate
    break
  }
}

if (-not $python) {
  throw "Python 실행 파일을 찾지 못했습니다. Python을 설치한 뒤 다시 실행해 주세요."
}

& $python (Join-Path $appDir "app.py")
