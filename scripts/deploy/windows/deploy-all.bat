@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
if not defined APP_NAMESPACE set "APP_NAMESPACE=de-ai-07"
if not defined IMAGE_TAG set "IMAGE_TAG=k8s-auto"
if not defined PYTHON_CMD set "PYTHON_CMD=python"

call "%PROJECT_ROOT%\scripts\deploy\windows\01-infra-apply.bat" || exit /b 1
call "%PROJECT_ROOT%\scripts\deploy\windows\02-image-build-push.bat" || exit /b 1
call "%PROJECT_ROOT%\scripts\deploy\windows\03-secret-sync.bat" || exit /b 1
call "%PROJECT_ROOT%\scripts\deploy\windows\04-k8s-deploy.bat" || exit /b 1

echo.
echo 전체 수동 배포 완료
exit /b 0
