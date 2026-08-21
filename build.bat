@echo off
rem Builds FpsOverlay.exe into dist\ - a single portable file that has
rem PresentMon bundled inside it (extracted to a temp folder at runtime).
rem Flags:
rem   --onefile     everything in one .exe
rem   --windowed    no console window (status goes to fps_overlay.log)
rem   --uac-admin   requests Administrator on launch (needed by PresentMon/ETW)
rem   --add-binary  embeds PresentMon next to the Python code inside the exe
python -m PyInstaller --noconfirm --clean ^
  --onefile --windowed --uac-admin ^
  --icon=icon.ico ^
  --version-file=version_info.txt ^
  --name FpsOverlay ^
  --add-data "PresentMon-2.5.1-x64.exe;." ^
  --add-data "icon.ico;." ^
  main.py
if errorlevel 1 (
  echo BUILD FAILED - is FpsOverlay.exe still running? Close it first.
  exit /b 1
)
echo.
echo Done - see dist\FpsOverlay.exe
