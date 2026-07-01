@echo off
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  if exist "C:\Program Files\Git\cmd\git.exe" (
    set "PATH=C:\Program Files\Git\cmd;%PATH%"
  )
)

echo.
echo [Assessment Checker] GitHub update
echo Repository: %CD%
echo.
echo This file downloads the latest GitHub version.
echo It does NOT upload WORK_HANDOFF.md or other local changes.
echo Use "깃허브 업로드.bat" when you finish work.
echo.

set "BACKUP_ROOT=%~dp0_github_update_backups"
set "STAMP=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "STAMP=%STAMP: =0%"
set "BACKUP_DIR=%BACKUP_ROOT%\before_pull_%STAMP%"
mkdir "%BACKUP_DIR%" >nul 2>nul

echo Creating backup before pull:
echo %BACKUP_DIR%
robocopy "%~dp0" "%BACKUP_DIR%" /E /XD ".git" "_github_update_backups" "assessment_checker_output" "__pycache__" ".venv" "venv" "dist" "build" "uploads" "outputs" "temp" "tmp" /XF "*.hwpx" "*.hwp" "*.pdf" "*.xlsx" "*.xls" "*.csv" "*.show" >nul
if %ERRORLEVEL% GEQ 8 (
  echo Backup failed.
  goto fail
)
echo Backup complete.
echo.

git status --short --branch
echo.
echo Running: git fetch
git fetch
if errorlevel 1 goto fail

echo.
echo Running: git pull
git pull
if errorlevel 1 goto fail

echo.
echo Current status:
git status --short --branch
echo.
echo Update complete.
pause
exit /b 0

:fail
echo.
echo Update failed. Please check the message above.
pause
exit /b 1
