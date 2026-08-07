@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
set "INFRA_DIR=%PROJECT_ROOT%\infra"
if not defined APP_NAMESPACE set "APP_NAMESPACE=de-ai-07"

call :terraform_output AWS_REGION aws_region
call :terraform_output CLUSTER_NAME cluster_name

if defined AWS_REGION if defined CLUSTER_NAME (
  aws eks update-kubeconfig --region "%AWS_REGION%" --name "%CLUSTER_NAME%"
  kubectl delete ingress public-alb -n "%APP_NAMESPACE%" --ignore-not-found=true --wait=true
  kubectl delete namespace "%APP_NAMESPACE%" --ignore-not-found=true --wait=true --timeout=20m
  kubectl delete ingressclass alb --ignore-not-found=true
  kubectl delete ingressclassparams alb --ignore-not-found=true
)

terraform -chdir="%INFRA_DIR%" destroy -auto-approve || exit /b 1
echo 전체 삭제 완료
exit /b 0

:terraform_output
set "%~1="
pushd "%INFRA_DIR%" >nul 2>&1
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%A in (`terraform output -raw %~2 2^>nul`) do set "%~1=%%A"
popd >nul
exit /b 0
