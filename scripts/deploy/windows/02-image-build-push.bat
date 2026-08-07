@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
set "INFRA_DIR=%PROJECT_ROOT%\infra"
if not defined IMAGE_TAG set "IMAGE_TAG=k8s-auto"

call :terraform_output AWS_REGION aws_region || exit /b 1
call :terraform_output WEB_REPO web_ecr_repository_url || exit /b 1
call :terraform_output WAS_REPO was_ecr_repository_url || exit /b 1

for /f "tokens=1 delims=/" %%A in ("%WEB_REPO%") do set "ECR_REGISTRY=%%A"

echo [1/3] ECR 로그인
aws ecr get-login-password --region "%AWS_REGION%" | docker login --username AWS --password-stdin "%ECR_REGISTRY%"
if errorlevel 1 exit /b 1

echo [2/3] WEB 이미지 빌드 및 Push
docker build --platform linux/amd64 -t "%WEB_REPO%:%IMAGE_TAG%" "%PROJECT_ROOT%\apps\web" || exit /b 1
docker push "%WEB_REPO%:%IMAGE_TAG%" || exit /b 1

echo [3/3] WAS 이미지 빌드 및 Push
docker build --platform linux/amd64 -t "%WAS_REPO%:%IMAGE_TAG%" "%PROJECT_ROOT%\apps\was" || exit /b 1
docker push "%WAS_REPO%:%IMAGE_TAG%" || exit /b 1

echo.
echo 이미지 Push 완료: %IMAGE_TAG%
exit /b 0

:terraform_output
set "%~1="
pushd "%INFRA_DIR%" >nul
for /f "usebackq delims=" %%A in (`terraform output -raw %~2`) do set "%~1=%%A"
popd >nul
if not defined %~1 exit /b 1
exit /b 0
