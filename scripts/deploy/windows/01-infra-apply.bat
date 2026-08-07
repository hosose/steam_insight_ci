@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
set "INFRA_DIR=%PROJECT_ROOT%\infra"

if not exist "%INFRA_DIR%\terraform.tfvars" (
  echo [ERROR] %INFRA_DIR%\terraform.tfvars 파일이 없습니다.
  exit /b 1
)

 echo [1/3] Terraform init
terraform -chdir="%INFRA_DIR%" init || exit /b 1
 echo [2/3] Terraform apply
terraform -chdir="%INFRA_DIR%" apply -auto-approve || exit /b 1

call :terraform_output AWS_REGION aws_region || exit /b 1
call :terraform_output CLUSTER_NAME cluster_name || exit /b 1

 echo [3/3] kubeconfig 갱신 및 Metrics Server 확인
aws eks update-kubeconfig --region "%AWS_REGION%" --name "%CLUSTER_NAME%" || exit /b 1
kubectl rollout status deployment/metrics-server -n kube-system --timeout=15m || exit /b 1

echo.
echo 인프라 준비 완료
exit /b 0

:terraform_output
set "%~1="
pushd "%INFRA_DIR%" >nul
for /f "usebackq delims=" %%A in (`terraform output -raw %~2`) do set "%~1=%%A"
popd >nul
if not defined %~1 exit /b 1
exit /b 0
