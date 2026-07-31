@echo off
rem Setzer development launcher for Windows.
rem Runs the meson-built setzer_dev.py with the correct environment.
rem Usage: scripts\setzer.dev.bat

setlocal
set SRC_DIR=%~dp0..
set BLD_DIR=%SRC_DIR%\builddir

if not exist "%BLD_DIR%\setzer_dev.py" (
    echo Make sure to run "meson setup builddir" first.
    exit /b 1
)

cd /d "%SRC_DIR%"
rem The setzer package is not installed into site-packages, so add the
rem source root to PYTHONPATH. Also prepend mingw64\bin to PATH so the
rem GTK/libadwaita DLLs and the correct python are found.
set "PYTHONPATH=%SRC_DIR%;%PYTHONPATH%"
if exist "%MSYSTEM_PREFIX%\bin" set "PATH=%MSYSTEM_PREFIX%\bin;%PATH%"
if exist "C:\msys64\mingw64\bin" set "PATH=C:\msys64\mingw64\bin;%PATH%"
python "%BLD_DIR%\setzer_dev.py" %*
