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
  --add-binary "PresentMon-2.5.1-x64.exe;." ^
  main.py
echo.
echo Done - see dist\FpsOverlay.exe
