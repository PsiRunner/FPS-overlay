@echo off
rem Builds FpsOverlay.exe and wraps it in a zip ready to publish.
rem Usage: package.bat [version]   (default 1.0.0)
setlocal
set VER=%1
if "%VER%"=="" set VER=1.0.0

call build.bat
if errorlevel 1 exit /b 1

rem Zip contains everything needed for redistribution: the app plus
rem license/attribution files that must accompany any public upload.
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\FpsOverlay.exe','LICENSE','NOTICE','README.md' -DestinationPath 'dist\FpsOverlay-v%VER%.zip'"
if errorlevel 1 exit /b 1

echo.
echo Done:
dir /b dist\FpsOverlay.exe dist\FpsOverlay-v%VER%.zip
