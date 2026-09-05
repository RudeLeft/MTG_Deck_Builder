@echo off
REM ====================================================================
REM  Build a portable Windows folder for the MTG Deck Builder.
REM
REM  Prerequisite: Python 3.11+ installed and on PATH.
REM    Get it from https://www.python.org/downloads/  (tick "Add to PATH").
REM
REM  Put ALL the project files in one folder, then double-click this file.
REM  When it finishes, your program is at:  dist\MTGDeckBuilder\MTGDeckBuilder.exe
REM ====================================================================

setlocal

REM Always run from the folder this script lives in, no matter how it's launched.
cd /d "%~dp0"
echo Working folder: %CD%
echo.

REM --- Make sure the project files are actually here -------------------
set "MISSING="
if not exist "pyproject.toml"           set "MISSING=%MISSING% pyproject.toml"
if not exist "mtgdb\main.py"            set "MISSING=%MISSING% mtgdb\main.py"
if not exist "mtgdb\ui\app.py"          set "MISSING=%MISSING% mtgdb\ui\app.py"
if not exist "mtgdb\__main__.py"        set "MISSING=%MISSING% mtgdb\__main__.py"
if not exist "mtgdb\ui\components.py"   set "MISSING=%MISSING% mtgdb\ui\components.py"
if not exist "mtgdb\database\db.py"     set "MISSING=%MISSING% mtgdb\database\db.py"
if not exist "assets\magic_icon.ico"  set "MISSING=%MISSING% assets\magic_icon.ico"
if not exist "MTGDeckBuilder.spec"      set "MISSING=%MISSING% MTGDeckBuilder.spec"
if defined MISSING (
    echo *** Missing file^(s^):%MISSING%
    echo.
    echo The mtgdb package and build config must be together in THIS folder:
    echo     mtgdb\  ^(the application package^)
    echo     assets\magic_icon.ico
    echo     pyproject.toml  MTGDeckBuilder.spec  build_windows.bat
    echo.
    echo They probably got separated when downloaded. Put them all in one
    echo folder ^(or unzip the provided zip^), then run this again.
    echo.
    pause
    exit /b 1
)

set "BUILD_STAGE=[1/7] Cleaning up previous build"
echo === [1/7] Cleaning up any previous build ===
REM A previously-built app that's still running locks its own .exe and makes
REM the next build fail with "Access is denied". Close it first, then clear
REM the old output folders so nothing is stale or locked.
taskkill /f /im MTGDeckBuilder.exe >nul 2>&1
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"
REM Rebuild the dependency environment too. A reused build-venv silently keeps
REM whatever Pillow/ReportLab/PyInstaller versions it was first created with,
REM so two builds of the same source could ship different libraries.
if exist "build-venv" rmdir /s /q "build-venv"
REM rmdir reports "Access is denied" and carries on when something still holds a
REM file, which used to leave the old environment in place and build against it
REM without a word. Stop instead: a build that cannot guarantee its own
REM dependencies is worse than no build.
if exist "build-venv" (
    echo.
    echo *** Could not remove build-venv, so this build would reuse the old
    echo     dependency environment instead of rebuilding it.
    echo.
    echo     Something is still using that folder. Usually it is a python.exe
    echo     started from build-venv that is still running, or a terminal or
    echo     editor sitting inside the folder. These are using it now:
    echo.
    powershell -NoProfile -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue ^| Where-Object { $_.Path -like '*build-venv*' } ^| Select-Object Id,Path ^| Format-Table -AutoSize"
    echo.
    echo     Close them and run this again.
    echo.
    pause
    exit /b 1
)

set "BUILD_STAGE=[2/7] Creating isolated build environment"
echo === [2/7] Creating an isolated build environment ===
python -m venv build-venv || goto :err
call build-venv\Scripts\activate.bat || goto :err

echo.
set "BUILD_STAGE=[3/7] Installing dependencies"
echo === [3/7] Installing dependencies (Pillow + ReportLab + PyInstaller) ===
python -m pip install --upgrade pip || goto :err
python -m pip install ".[build]" || goto :err
if exist "mtg_deck_builder.egg-info" rmdir /s /q "mtg_deck_builder.egg-info"

echo.
echo.
set "BUILD_STAGE=[4/7] Compilation and cross-platform guardrails"
echo === [4/7] Running compilation and cross-platform guardrails ===
python -m compileall -q mtgdb tests windows_tests package_release.py || goto :err
for %%F in (tests\test_*.py) do (
    echo Running %%F
    python "%%F" || goto :err
)

echo.
set "BUILD_STAGE=[5/7] Windows pre-build guardrails"
echo === [5/7] Running Windows pre-build guardrails ===
python windows_tests\test_single_instance_windows.py || goto :err
python windows_tests\test_ui_geometry_windows.py || goto :err
python windows_tests\test_app_startup_windows.py || goto :err

echo.
set "BUILD_STAGE=[6/7] PyInstaller build"
echo === [6/7] Building portable MTGDeckBuilder folder ===
pyinstaller MTGDeckBuilder.spec --noconfirm || goto :err

echo.
set "BUILD_STAGE=[7/7] Packaged portability smoke test"
echo === [7/7] Running packaged portability smoke test ===
python windows_tests\smoke_packaged_windows.py || goto :err

echo.
echo =====================================================================
echo  Success!  Your self-contained program is here:
echo      %CD%\dist\MTGDeckBuilder\MTGDeckBuilder.exe
echo  Copy the ENTIRE dist\MTGDeckBuilder folder to another PC.
echo  Keep the folder together so the app remains fully portable.
echo =====================================================================
echo.
pause
exit /b 0

:err
echo.
echo *** Build failed during %BUILD_STAGE%. ***
echo Review the error immediately above this message for the exact cause.
echo.
echo If you saw "Access is denied" on MTGDeckBuilder.exe:
echo   - The app is probably still running. Close its window, and check
echo     Task Manager ^(Ctrl+Shift+Esc^) for MTGDeckBuilder.exe -^> End task.
echo   - Or your antivirus is scanning the new .exe. Wait a few seconds and
echo     run this again, or add this folder as an antivirus exclusion.
echo.
pause
exit /b 1
