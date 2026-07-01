@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  if exist "C:\Program Files\Git\cmd\git.exe" (
    set "PATH=C:\Program Files\Git\cmd;%PATH%"
  )
)

echo.
echo [Assessment Checker] GitHub upload
echo Repository: %CD%
echo.
echo This file uploads local changes to GitHub.
echo If WORK_HANDOFF.md was edited, it will be committed and pushed too.
echo.

echo Current status:
git status --short --branch
if errorlevel 1 goto fail

echo.
echo ============================================================
echo WARNING: Do not upload sensitive school files or outputs.
echo Check that these are NOT included:
echo   *.hwpx, *.hwp, *.pdf, *.xlsx, *.xls, *.csv
echo   .env, secrets.toml
echo   assessment_checker_output/, dist/, build/
echo   uploads/, outputs/, temp/, tmp/
echo.
echo Before uploading, update WORK_HANDOFF.md if today's work
echo should be continued on another PC.
echo ============================================================
echo.

set /p CONFIRM=Type YES to continue upload: 
if /I not "%CONFIRM%"=="YES" (
  echo Upload cancelled.
  pause
  exit /b 0
)

echo.
set /p COMMIT_MSG=Commit message: 
if "%COMMIT_MSG%"=="" (
  echo Commit message is required.
  pause
  exit /b 1
)

echo.
echo Running: git add .
git add .
if errorlevel 1 goto fail

echo.
echo Status after git add:
git status --short --branch
if errorlevel 1 goto fail

echo.
echo Running: git commit
git commit -m "%COMMIT_MSG%"
if errorlevel 1 goto fail

echo.
echo Running: git push
git push
if errorlevel 1 goto fail

echo.
echo Upload complete.
pause
exit /b 0

:fail
echo.
echo Upload failed. Please check the message above.
pause
exit /b 1
