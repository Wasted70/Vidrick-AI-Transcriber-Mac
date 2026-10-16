@echo off
setlocal
rem This launcher runs ONLY the independent Mac prototype beside this file.
rem It never invokes the production Windows Vidrick app or installs packages.
pushd "%~dp0" || exit /b 1
set "PYTHONDONTWRITEBYTECODE=1"
set "VIDRICK_PREVIEW_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%VIDRICK_PREVIEW_PYTHON%" goto bundled_python
where py.exe >nul 2>nul
if not errorlevel 1 goto system_python
echo Python was not found. This preview needs Python 3.11 or newer with Tk.
echo Nothing was installed or changed. See README.txt for manual commands.
set "VIDRICK_PREVIEW_EXIT=2"
goto finish

:bundled_python
"%VIDRICK_PREVIEW_PYTHON%" -E -B -m vidrick_mac --windows-preview
set "VIDRICK_PREVIEW_EXIT=%ERRORLEVEL%"
goto finish

:system_python
py.exe -3 -E -B -m vidrick_mac --windows-preview
set "VIDRICK_PREVIEW_EXIT=%ERRORLEVEL%"

:finish
popd
if not "%VIDRICK_PREVIEW_EXIT%"=="0" (
    echo.
    echo The development preview did not open successfully. Review the message above.
    pause
)
exit /b %VIDRICK_PREVIEW_EXIT%
