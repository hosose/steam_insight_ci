@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
set "INFRA_DIR=%PROJECT_ROOT%\infra"
if not defined APP_NAMESPACE set "APP_NAMESPACE=de-ai-07"

call :terraform_output AWS_REGION aws_region || exit /b 1
call :terraform_output CLUSTER_NAME cluster_name || exit /b 1
aws eks update-kubeconfig --region "%AWS_REGION%" --name "%CLUSTER_NAME%" || exit /b 1

echo === EKS Auto Mode 리소스 ===
kubectl get nodepool,nodeclass,nodeclaim,nodes
echo.
echo === 애플리케이션 리소스 ===
kubectl get pods,svc,ingress,hpa,pdb -n "%APP_NAMESPACE%"
echo.
echo === Pod 배치 정보 ===
kubectl get pods -n "%APP_NAMESPACE%" -o wide
exit /b 0

:terraform_output
set "%~1="
pushd "%INFRA_DIR%" >nul
for /f "usebackq delims=" %%A in (`terraform output -raw %~2`) do set "%~1=%%A"
popd >nul
if not defined %~1 exit /b 1
exit /b 0
