@echo off
rem Builds FpsOverlay.exe into dist\ - a single portable file that has
rem PresentMon bundled inside it (extracted to a temp folder at runtime).
rem All build options (onefile, admin manifest, icon, version resource,
rem PresentMon/icon bundling, unused-Qt stripping) live in FpsOverlay.spec.
python -m PyInstaller --noconfirm --clean FpsOverlay.spec
if errorlevel 1 (
  echo BUILD FAILED - is FpsOverlay.exe still running? Close it first.
  exit /b 1
)
echo.
echo Done - see dist\FpsOverlay.exe
